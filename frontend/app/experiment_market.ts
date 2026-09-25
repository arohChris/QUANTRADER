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

export const MOCK_TIMEFRAMES = ["1m", "5m", "15m", "1H", "4H", "1D"] as const;
export type Timeframe = (typeof MOCK_TIMEFRAMES)[number];

// How many mock points each timeframe shows; presentation window, not a calculation.
export const TIMEFRAME_WINDOW: Record<Timeframe, number> = {
  "1m": 12,
  "5m": 24,
  "15m": 32,
  "1H": 40,
  "4H": 48,
  "1D": 64,
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
