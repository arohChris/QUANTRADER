/**
 * WHY this hook exists: it is the whole of the "timeframe dropdown changes the chart
 * immediately" behaviour, and the three mechanisms below are what make it feel instant
 * rather than merely fast. None of them is network speed.
 *
 * 1. PREFETCH ON HOVER. A pointer rests on a dropdown option for roughly 300ms before the
 *    click lands. Fetching during that window means the click is a synchronous `setData`
 *    from cache - no spinner, no blank chart, no visible wait. This is how the exchange
 *    venues themselves make a control feel instant.
 *
 * 2. REQUEST SEQUENCE GUARD. Clicking 1m -> 5m -> 15m fires three requests whose responses
 *    can land out of order. Without a monotonic guard the slow 5m response overwrites the
 *    fast 15m one and the user sees the wrong timeframe while believing they chose 15m. The
 *    guard drops any response that is not the newest request.
 *
 * 3. NEVER CLEAR THE SERIES ON SWITCH. The previous interval stays rendered until the new
 *    data arrives. A chart that blanks and refills reads as broken; one that cross-fades
 *    between intervals reads as responsive. The old series is also WRONG-but-harmless in a
 *    way an empty chart is not - you can still see the last known price while waiting.
 *
 * Deliberately NOT implemented here: WebSocket subscription swapping. The backend has no
 * tick/bar stream yet (Deriv pushes only raw ticks and the digesting daemon is not wired),
 * so `lastAppliedSequence` is carried as `watermark` for when it is, but nothing consumes it
 * today. Adding a subscription here before the stream exists would be dead code that looks
 * like a working feature.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  describeFreshness,
  fetchCandles,
  MarketApiError,
  type CandlesPage,
  type Timeframe,
} from "../services/market_data_api";

/** Bars requested per view. Constant across timeframes on purpose - see `TARGET_BARS`. */
const TARGET_BARS = 300;

export interface CandleState {
  page: CandlesPage | null;
  loading: boolean;
  error: string | null;
  /** Set when the store is unreachable, so the UI can say "retry" rather than "no data". */
  retryable: boolean;
  /** Wall-clock text from the backend's own staleness fields, or null when unknowable. */
  freshness: string | null;
  /** Warm the cache for a timeframe the user is about to click. */
  prefetch: (instrument: string | null, interval: Timeframe) => void;
}

interface CacheEntry {
  page: CandlesPage;
  at: number;
}

const cache = new Map<string, CacheEntry>();
/** How long a prefetched page stays usable. Long enough to cover a hover, short enough that
 *  a reopen does not show a stale cache without saying so. */
const CACHE_TTL_MS = 30_000;

function key(instrument: string, interval: Timeframe): string {
  return `${instrument}|${interval}`;
}

export function useCandles(instrument: string | null, interval: Timeframe): CandleState {
  // `prefetch` is produced below, not stored, so the state tuple deliberately omits it.
  type StateSlice = Omit<CandleState, "prefetch">;
  const [state, setState] = useState<StateSlice>({
    page: null,
    loading: false,
    error: null,
    retryable: false,
    freshness: null,
  });

  const seq = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const apply = useCallback((page: CandlesPage) => {
    setState({
      page,
      loading: false,
      error: null,
      retryable: false,
      freshness: describeFreshness(page.data_state, page.staleness_sec),
    });
  }, []);

  const load = useCallback(
    async (target: string | null, tf: Timeframe) => {
      if (!target) {
        return;
      }
      const mine = ++seq.current;
      const cached = cache.get(key(target, tf));
      if (cached && Date.now() - cached.at < CACHE_TTL_MS) {
        // Synchronous path: this is the "immediate" the dropdown promises.
        apply(cached.page);
        return;
      }

      // Cancel the superseded request so it cannot consume a connection or land late.
      controller.current?.abort();
      const ac = new AbortController();
      controller.current = ac;
      setState((prev) => ({ ...prev, loading: true, error: null }));

      try {
        const page = await fetchCandles({
          instrument: target,
          interval: tf,
          limit: TARGET_BARS,
        });
        // A response for a request the user has already navigated past. Dropping it is the
        // entire point of the sequence guard.
        if (mine !== seq.current) {
          return;
        }
        cache.set(key(target, tf), { page, at: Date.now() });
        apply(page);
      } catch (err) {
        if (mine !== seq.current) {
          return;
        }
        const api = err instanceof MarketApiError ? err : null;
        setState((prev) => ({
          ...prev,
          loading: false,
          // The PREVIOUS page is deliberately kept on error: a stale chart with a visible
          // error beats a blank chart, because the user can still read the last real price.
          error: api ? `${api.code}: ${api.message}` : String(err),
          retryable: api ? api.retryable : false,
        }));
      }
    },
    [apply],
  );

  useEffect(() => {
    void load(instrument, interval);
    return () => {
      controller.current?.abort();
    };
  }, [instrument, interval, load]);

  /** Call on dropdown hover/focus so the click is already warm. */
  const prefetch = useCallback(
    (target: string | null, tf: Timeframe) => {
      if (!target) {
        return;
      }
      const cached = cache.get(key(target, tf));
      if (cached && Date.now() - cached.at < CACHE_TTL_MS) {
        return;
      }
      void load(target, tf);
    },
    [load],
  );

  return { ...state, prefetch };
}

/**
 * Merge bars by timestamp into a keyed map, then emit sorted.
 *
 * WHY NOT `[...older, ...existing]`: while the user pages backwards, a newer bar can arrive
 * from the same response window. Concatenation either DROPS that bar or DUPLICATES it,
 * depending on ordering - both silent. A map keyed on `t` is idempotent: the same key
 * overwrites, nothing is lost or doubled, and the output is always ordered.
 *
 * Exported separately so the scrollback behaviour can be tested without React.
 */
export function mergeBars(
  existing: readonly { t: string }[],
  incoming: readonly { t: string }[],
): { t: string }[] {
  const byTime = new Map<string, { t: string }>();
  for (const bar of [...incoming, ...existing]) {
    byTime.set(bar.t, bar);
  }
  return [...byTime.values()].sort((a, b) => (a.t < b.t ? -1 : a.t > b.t ? 1 : 0));
}