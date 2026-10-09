export type ExperimentPoint = {
  timestamp: string;
  close: number;
};

export type ExperimentMarket = {
  symbol: string;
  timeframe: Timeframe;
  sequence: number;
  asOf: string;
  quality: "COMPLETE" | "FORMING";
  referenceSnapshotId: string;
  points: ExperimentPoint[];
  reference: number[];
};

// Mock instrument catalogue for the asset selector. Presentation only, no pricing authority.
// Twelve instruments so at least ten fit on screen at once on a wide viewport.
export const MOCK_INSTRUMENTS = [
  { symbol: "FX.EURUSD", label: "EUR/USD", base: 1.0842, decimals: 4 },
  { symbol: "FX.GBPUSD", label: "GBP/USD", base: 1.2712, decimals: 4 },
  { symbol: "FX.USDJPY", label: "USD/JPY", base: 149.62, decimals: 2 },
  { symbol: "FX.AUDUSD", label: "AUD/USD", base: 0.6612, decimals: 4 },
  { symbol: "FX.USDCAD", label: "USD/CAD", base: 1.3584, decimals: 4 },
  { symbol: "FX.NZDUSD", label: "NZD/USD", base: 0.6018, decimals: 4 },
  { symbol: "CRYPTO.BTCUSD", label: "BTC/USD", base: 64250, decimals: 1 },
  { symbol: "CRYPTO.ETHUSD", label: "ETH/USD", base: 3142.5, decimals: 2 },
  { symbol: "CRYPTO.SOLUSD", label: "SOL/USD", base: 148.32, decimals: 2 },
  { symbol: "COMMODITY.XAUUSD", label: "XAU/USD", base: 2318.4, decimals: 2 },
  { symbol: "COMMODITY.XAGUSD", label: "XAG/USD", base: 27.41, decimals: 3 },
  { symbol: "COMMODITY.WTIUSD", label: "WTI/USD", base: 78.24, decimals: 2 },
] as const;

// WHY parity-locked (m10.4.10): these nine values are copied verbatim from the backend
// registry financial_analyst_app/shared/timeframes.py INTERVAL_MINUTES — the same keys
// FeatureRequest validates and the Temporal Resolver accepts. The name MOCK_TIMEFRAMES is
// kept because the list still drives the MOCK chart window; the lock (exact 9, both
// directions) is enforced by frontend/tests/test_m10.4.10_timeframe_dropdown_spec.py.
// Uppercase 1H/4H/1D were a vocabulary mismatch the backend hard-rejects and are gone;
// calendar buckets (1w/1mo/1y) are deliberately absent — fixed-duration strings only.
export const MOCK_TIMEFRAMES = ["1m", "5m", "15m", "30m", "40m", "1h", "2h", "4h", "24h"] as const;
export type Timeframe = (typeof MOCK_TIMEFRAMES)[number];

// How many mock points each timeframe shows; presentation window, not a calculation.
// Every registry key must appear — Record<Timeframe, number> forces it in tsc (E3),
// and the parity test asserts the key set independently at text level.
export const TIMEFRAME_WINDOW: Record<Timeframe, number> = {
  "1m": 12,
  "5m": 24,
  "15m": 32,
  "30m": 36,
  "40m": 40,
  "1h": 40,
  "2h": 56,
  "4h": 48,
  "24h": 64,
};

const MAX_POINTS = 64;
const SEED_POINTS = 24;

// The drawdown starts here on the time axis: price peaks, then keeps falling as
// time advances, so the visible tail of the series stays in a sustained drawdown.
const drawdownStart = 18;

// Price is a pure function of elapsed time (the point index). Elapsed time is the
// only input, so the same index always yields the same price for every asset and
// switching asset never rewrites history. Shape: drift up + two slow cycles, then
// a linear decline from the peak. Mock presentation only — no pricing authority.
function pricePathAt(index: number): number {
  const drift = index * 0.0021;
  const cycle = Math.sin(index / 3.1) * 0.0026 + Math.cos(index / 7.3) * 0.0042;
  const drawdown = index > drawdownStart ? (index - drawdownStart) * 0.0043 : 0;
  return 1 + drift + cycle - drawdown;
}

// Visual reference only: a straight trend line, not a calculation or a signal.
function referencePathAt(index: number): number {
  return 1 + index * 0.0021;
}

function timestampFor(sequence: number): string {
  return new Date(Date.UTC(2026, 0, 1, 12, sequence)).toISOString();
}

function buildPoints(count: number, decimals: number, scale: number): ExperimentPoint[] {
  return Array.from({ length: count }, (_, index) => ({
    timestamp: timestampFor(index),
    close: Number((pricePathAt(index) * scale).toFixed(decimals)),
  }));
}

function buildReference(count: number, decimals: number, scale: number): number[] {
  return Array.from({ length: count }, (_, index) => Number((referencePathAt(index) * scale).toFixed(decimals)));
}

export function createExperimentMarket(): ExperimentMarket {
  const seed = MOCK_INSTRUMENTS[0];
  return {
    symbol: seed.symbol,
    timeframe: "1m",
    sequence: 0,
    asOf: timestampFor(SEED_POINTS - 1),
    quality: "FORMING",
    referenceSnapshotId: "REF-EXPERIMENT-001",
    points: buildPoints(SEED_POINTS, seed.decimals, seed.base),
    reference: buildReference(SEED_POINTS, seed.decimals, seed.base),
  };
}

// Switching asset rescales the same time path; the history is not rewritten.
export function withSymbol(market: ExperimentMarket, symbol: string): ExperimentMarket {
  const instrument = MOCK_INSTRUMENTS.find((entry) => entry.symbol === symbol) ?? MOCK_INSTRUMENTS[0];
  const count = market.points.length;

  return {
    ...market,
    symbol: instrument.symbol,
    points: buildPoints(count, instrument.decimals, instrument.base),
    reference: buildReference(count, instrument.decimals, instrument.base),
  };
}

export function withTimeframe(market: ExperimentMarket, timeframe: Timeframe): ExperimentMarket {
  return { ...market, timeframe };
}

// Which phase of the mock path the elapsed time is currently in. Presentation label only.
export function pathPhase(index: number): "advance" | "drawdown" {
  return index > drawdownStart ? "drawdown" : "advance";
}

// --- Active trading sections ----------------------------------------------------
//
// AssetClass is frozen to forex | crypto | commodity
// (shared/contracts/instrument.py:9,23-25), so the markets view groups its sections by
// exactly those three values and invents no category of its own.
//
// The session windows below are INDICATIVE market hours, read off the UTC clock so the
// screen never claims a session is open when it is not. They are not a trading calendar:
// no holiday calendar, no DST rule, no per-broker override. Presentation only — the
// authoritative calendar is M2b `TradingCalendar`, which is not built yet. A section
// states that its market is trading and for how long; it never states a position, an
// order, or a fill.

export type AssetClass = "forex" | "crypto" | "commodity";

export const ASSET_CLASSES: AssetClass[] = ["forex", "crypto", "commodity"];

// AssetClass comes from the symbol namespace the catalogue already uses.
export function classOf(symbol: string): AssetClass {
  if (symbol.startsWith("CRYPTO.")) {
    return "crypto";
  }
  return symbol.startsWith("COMMODITY.") ? "commodity" : "forex";
}

export type Session = {
  assetClass: AssetClass;
  label: string;
  open: boolean;
  // Human phrasing of the window, e.g. "open 21:00–21:00 UTC".
  window: string;
  // "closes in 3h 20m" or "reopens in 1d 4h"; null for a market that never closes.
  countdown: string | null;
};

const SESSION_RULES: Record<AssetClass, { label: string; openDay: number; openHour: number; closeDay: number; closeHour: number; always: boolean }> = {
  forex: { label: "Forex", openDay: 0, openHour: 21, closeDay: 5, closeHour: 21, always: false },
  crypto: { label: "Crypto", openDay: 0, openHour: 0, closeDay: 6, closeHour: 0, always: true },
  commodity: { label: "Commodities", openDay: 0, openHour: 22, closeHour: 22, closeDay: 5, always: false },
};

// Hours until the next open of a weekly market. The week runs Sun(0) → Fri(5), so the
// three "closed" shapes each need their own answer:
//   Saturday                       → tomorrow, Sunday's open hour
//   Sunday before the open hour    → later the same day
//   Friday after the early close   → 2 days + the open hour (skips Saturday)
function hoursUntilOpen(now: Date, rule: (typeof SESSION_RULES)[AssetClass]): number {
  const day = now.getUTCDay();
  const hour = now.getUTCHours() + now.getUTCMinutes() / 60;

  if (day === 6) {
    return 24 + rule.openHour - hour;
  }
  if (day === 0 && hour < rule.openHour) {
    return rule.openHour - hour;
  }
  if (day === rule.closeDay && hour >= rule.closeHour) {
    return 48 + rule.openHour - hour;
  }
  // Unreachable for a well-formed rule; report the next full week rather than a wrong answer.
  return 168;
}

function formatCountdown(hours: number): string {
  const whole = Math.floor(hours);
  const minutes = Math.round((hours - whole) * 60);
  if (minutes === 60) {
    return `${whole + 1}h`;
  }
  return whole > 0 ? `${whole}h ${minutes}m` : `${minutes}m`;
}

// Which of the three frozen asset classes is trading right now, and for how much longer.
export function readSessions(now: Date): Session[] {
  return ASSET_CLASSES.map((assetClass) => {
    const rule = SESSION_RULES[assetClass];
    const day = now.getUTCDay();
    const hour = now.getUTCHours() + now.getUTCMinutes() / 60;
    const window = `${pad(rule.openHour)}:00–${pad(rule.closeHour)}:00 UTC`;

    if (rule.always) {
      return { assetClass, label: rule.label, open: true, window: "24h · never closes", countdown: null };
    }

    // The week runs Sun(0) → Fri(5); Saturday(6) is closed for every non-crypto class.
    const weekend = day === 6 || (day === 0 && hour < rule.openHour);
    const closedEarly = day === rule.closeDay && hour >= rule.closeHour;
    const open = !weekend && !closedEarly;

    if (open) {
      const until = day === rule.closeDay ? rule.closeHour : rule.closeHour + 24;
      return { assetClass, label: rule.label, open: true, window, countdown: `closes in ${formatCountdown(until - hour)}` };
    }
    return { assetClass, label: rule.label, open: false, window, countdown: `reopens in ${formatCountdown(hoursUntilOpen(now, rule))}` };
  });
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

// One tick of elapsed time: append the next point of the same path.
export function stepExperimentMarket(market: ExperimentMarket): ExperimentMarket {
  const nextSequence = market.sequence + 1;
  const instrument = MOCK_INSTRUMENTS.find((entry) => entry.symbol === market.symbol) ?? MOCK_INSTRUMENTS[0];
  const nextIndex = market.points.length;
  const points = [
    ...market.points,
    {
      timestamp: timestampFor(nextIndex),
      close: Number((pricePathAt(nextIndex) * instrument.base).toFixed(instrument.decimals)),
    },
  ].slice(-MAX_POINTS);
  const reference = [
    ...market.reference,
    Number((referencePathAt(nextIndex) * instrument.base).toFixed(instrument.decimals)),
  ].slice(-MAX_POINTS);

  return {
    ...market,
    sequence: nextSequence,
    asOf: timestampFor(nextIndex),
    points,
    reference,
  };
}
