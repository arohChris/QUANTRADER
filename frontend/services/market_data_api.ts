/**
 * WHY this file exists: it is the ONLY place in the frontend permitted to perform network
 * I/O against the market API. Features call typed functions here; they never call `fetch`.
 *
 * That rule (M10 proposal 6.2) had ZERO implementations until now - every surface imported
 * `mock_data` directly. This is that implementation, and it is deliberately the single
 * choke point so a contract change lands in one file rather than in twenty components.
 *
 * Three decisions worth knowing:
 *
 * 1. PRICES ARE STRINGS, and are parsed to `number` only at the final line where they become
 *    chart values. The backend serves them as strings because a float64 cannot hold a 5dp FX
 *    price exactly. Parsing any earlier would reintroduce that error in every comparison.
 *
 * 2. `dataState` is carried through to the UI rather than collapsed into a boolean. The whole
 *    reason the backend reports it is that this store was 28.8 hours stale for two days and
 *    nothing was able to say so. A component that receives a number is free to forget to
 *    compare it; one that receives an enum is forced to switch on it.
 *
 * 3. Errors surface the frozen `error.code`, never a generic failure string. The proposal
 *    withdrew a second `UPPER_SNAKE` dialect for exactly this reason.
 */

export type Timeframe = "1m" | "5m" | "15m" | "30m" | "40m" | "1h" | "2h" | "4h" | "24h";

/** Mirrors the backend's D7 thresholds. Kept as a type so an unmapped value is a compile error. */
export type DataState = "LIVE" | "DELAYED" | "STALE";

export interface Candle {
  /** Interval-open, UTC. Never a bucket start rounded client-side. */
  t: string;
  o: string;
  h: string;
  l: string;
  c: string;
  /** Contributing 1m rows. Below `expectedBars` the bucket is `gapped`. */
  n: number;
  quality: "complete" | "gapped";
  reference_snapshot_id: string | null;
}

export interface Instrument {
  instrument: string;
  asset_class?: string | null;
  decimals?: number | null;
}

export interface CandlesData {
  instrument: string;
  interval: Timeframe;
  bars: Candle[];
  /** The last bar is still forming and its OHLC WILL change. */
  partial_bar_included: boolean;
  data_state: DataState;
  staleness_sec: number | null;
  watermark: string | null;
  expected_bars: number;
  gapped_bars: number;
}

export interface CandlesPage extends CandlesData {
  next_cursor: string | null;
  has_more: boolean;
}

export interface InstrumentsData {
  instruments: Instrument[];
  intervals: Timeframe[];
  broker: string;
}

export class MarketApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "MarketApiError";
    this.code = code;
    this.status = status;
  }

  /** Retryable per the backend's frozen `_RETRYABLE` set - do NOT show these as user error. */
  get retryable(): boolean {
    return this.status === 503;
  }
}

interface Envelope<T> {
  data: T | null;
  meta: {
    correlation_id: string;
    as_of: string;
    schema_version: string;
    next_cursor?: string | null;
    has_more?: boolean;
  };
  error: { code: string; message: string; details?: Record<string, unknown> } | null;
}

async function getJson<T>(path: string, params: Record<string, string | number | undefined>): Promise<T> {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) {
      url.searchParams.set(key, String(value));
    }
  }

  const response = await fetch(url.toString(), {
    headers: { "X-Correlation-Id": crypto.randomUUID() },
  });

  let body: Envelope<T>;
  try {
    body = (await response.json()) as Envelope<T>;
  } catch {
    // A non-JSON body means something in front of the API answered instead of the API.
    // That is a deployment problem, and reporting it as one beats reporting it as "no data".
    throw new MarketApiError("bad_response", `non-JSON response from ${path}`, response.status);
  }

  if (body.error) {
    throw new MarketApiError(body.error.code, body.error.message, response.status);
  }
  if (!response.ok || body.data === null) {
    throw new MarketApiError("http_error", `${path} failed with ${response.status}`, response.status);
  }
  return body.data;
}

export interface InstrumentsResult extends InstrumentsData {
  next_cursor: string | null;
  has_more: boolean;
}

export function fetchInstruments(): Promise<InstrumentsResult> {
  return getJson<InstrumentsResult>("/api/v1/market/instruments", {});
}

export function fetchCandles(params: {
  instrument: string;
  interval: Timeframe;
  limit?: number;
  cursor?: string;
}): Promise<CandlesPage> {
  return getJson<CandlesPage>("/api/v1/market/candles", {
    instrument: params.instrument,
    interval: params.interval,
    limit: params.limit,
    cursor: params.cursor,
  });
}

/**
 * Human-readable freshness. Returns `null` when the backend could not say - an unknown age
 * must never render as "fresh", which is the whole failure this API shape exists to prevent.
 */
export function describeFreshness(state: DataState, stalenessSec: number | null): string | null {
  if (state === "STALE") {
    if (stalenessSec === null) {
      return "data age unknown";
    }
    const minutes = Math.floor(stalenessSec / 60);
    const hours = Math.floor(minutes / 60);
    if (hours >= 24) {
      return `stale - ${Math.floor(hours / 24)}d old`;
    }
    if (hours >= 1) {
      return `stale - ${hours}h old`;
    }
    return `stale - ${minutes}m old`;
  }
  if (state === "DELAYED") {
    const minutes = stalenessSec === null ? 0 : Math.floor(stalenessSec / 60);
    return `delayed - ${minutes}m old`;
  }
  return stalenessSec === null ? "live" : `live - ${stalenessSec}s old`;
}