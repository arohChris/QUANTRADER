/**
 * Real market chart, replacing the mock-sourced line overlay.
 *
 * WHY A NEW COMPONENT RATHER THAN PATCHING `ExperimentChart`: that one draws a `LineSeries`
 * per asset with `time: position as UTCTimestamp` - a positional index, not a timestamp. It
 * is structurally incapable of showing real candles, because real bars carry interval-open
 * times and a positional axis would misplace every one of them. Changing its axis would have
 * altered the multi-asset overlay that other surfaces depend on, so the real chart lives
 * here and the mock one is left untouched for the surfaces that still read mocks.
 *
 * WHAT IS DELIBERATELY ABSENT: a WebSocket subscription. The backend serves REST only today
 * (Deriv streams raw ticks and the digesting daemon is not wired), so this component polls
 * while the document is visible. `watermark` is already on every response and is what a
 * future `sequence` baseline would be seeded from - see the note in `useCandles`.
 */
import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";

import type { Timeframe } from "../services/market_data_api";
import { useCandles } from "./useCandles";

const TIMEFRAME_OPTIONS: Timeframe[] = [
  "1m", "5m", "15m", "30m", "40m", "1h", "2h", "4h", "24h",
];

interface Props {
  instrument: string;
  interval: Timeframe;
  onIntervalChange: (next: Timeframe) => void;
  theme: "light" | "dark";
}

/** `2026-10-03T18:25:00Z` -> seconds. lightweight-charts wants seconds, not a string. */
function toEpochSeconds(iso: string): UTCTimestamp {
  return Math.floor(Date.parse(iso) / 1000) as UTCTimestamp;
}

/**
 * Formats a value for the TIME AXIS at the bottom of the chart.
 *
 * WHY THIS IS PER-TIMEFRAME. lightweight-charts' default tick formatter assumes intraday
 * data and will happily print a bare hour for a 24h series, or a raw timestamp that means
 * nothing to a trader. Worse, the same axis has to stay readable when the user switches from
 * 1m to 24h: at 1m you need date AND time, at 24h the date alone is ambiguous across months.
 * So the label density is derived from the interval, not chosen once.
 *
 * Note the axis reads BUCKET-START, matching the backend contract (interval-open, UTC). That
 * is the correct reading for a candle - a 5m bar labelled 18:25 covers 18:25-18:30 - and it is
 * also what makes the label stable while that bar is still forming.
 */
function makeTickFormatter(interval: Timeframe): (time: UTCTimestamp) => string {
  const dayOf = (t: UTCTimestamp) =>
    new Date(Number(t) * 1000).toLocaleDateString("en-GB", {
      day: "2-digit", month: "short", timeZone: "UTC",
    });
  const clockOf = (t: UTCTimestamp) =>
    new Date(Number(t) * 1000).toLocaleTimeString("en-GB", {
      hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC",
    });

  if (interval === "24h") {
    return dayOf;
  }
  // Anything >= 1h is too coarse to justify minutes on every tick.
  if (interval === "4h" || interval === "2h" || interval === "1h") {
    return (t: UTCTimestamp) => `${dayOf(t)} ${clockOf(t)}`;
  }
  // Intraday: print the date when the day rolls over, the clock otherwise. Keeps the axis
  // compact while still anchoring the reader in time across a multi-day window.
  let lastDay = "";
  return (t: UTCTimestamp) => {
    const label = dayOf(t);
    if (label !== lastDay) {
      lastDay = label;
      return label;
    }
    return clockOf(t);
  };
}

export function MarketChart({ instrument, interval, onIntervalChange, theme }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const { page, loading, error, retryable, freshness, prefetch } = useCandles(instrument, interval);

  // Create the chart once. Rebuilding it on every data change would drop the user's zoom,
  // pan position and crosshair, which is the fastest way to make a chart feel broken.
  useEffect(() => {
    if (!container.current) {
      return undefined;
    }
    const chart = createChart(container.current, {
      width: container.current.clientWidth,
      height: 340,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: theme === "dark" ? "#d6deeb" : "#2a2e39",
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: theme === "dark" ? "#2a2e39" : "#d1d4dc" },
      // The time reference along the bottom. `visible` is stated explicitly rather than
      // assumed, and `timeVisible` is what puts clock times on an intraday axis; without
      // both the chart renders with no time reference at all and the user cannot tell where
      // they are in time. `borderVisible` keeps the axis line visible so it reads as an axis
      // rather than as floating labels.
      timeScale: {
        visible: true,
        timeVisible: true,
        secondsVisible: false,
        borderVisible: true,
        borderColor: theme === "dark" ? "#2a2e39" : "#d1d4dc",
        rightOffset: 4,
        barSpacing: 8,
      },
      localization: {
        // The CROSSHAIR label — the "black stuff" that follows the cursor. This one gets the
        // FULL date and time, because a user hovering a bar wants to know exactly when it
        // was, not which 5-minute slot within an unstated day. UTC, to match the bucket-open
        // contract; showing local time here would silently disagree with every timestamp the
        // API returns.
        dateFormat: "yyyy-MM-dd",
        timeFormatter: (t: UTCTimestamp) => {
          const d = new Date(Number(t) * 1000);
          return `${d.toISOString().slice(0, 10)} ${d.toLocaleTimeString("en-GB", {
            hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC",
          })} UTC`;
        },
      },
    });
    // Axis ticks are the SHORT form: the full date+time belongs on the crosshair, and
    // repeating it at every tick would be unreadable. Rebuilt per interval below.
    chart.applyOptions({ timeScale: { tickMarkFormatter: makeTickFormatter(interval) } });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderVisible: false,
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
    });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [theme]);

  // Axis label density follows the interval: at 1m you need date AND time, at 24h a bare
  // time would be meaningless. Applying it on change rather than rebuilding the chart is what
  // preserves the user's zoom, pan and crosshair across a timeframe switch.
  useEffect(() => {
    chartRef.current?.applyOptions({
      timeScale: { tickMarkFormatter: makeTickFormatter(interval) },
    });
  }, [interval]);

  useEffect(() => {
    const onResize = () => {
      if (container.current && chartRef.current) {
        chartRef.current.applyOptions({ width: container.current.clientWidth });
      }
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // `setData`, never `update`. lightweight-charts documents `update` as "updates a single
  // data point ... for existing data points" - it is built for streaming the LAST bar. Using
  // it to patch arbitrary history is a misuse, and on a timeframe switch the bar BOUNDARIES
  // change, so every prior bar is different data, not an update to existing data.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !page) {
      return;
    }
    series.setData(
      page.bars.map((bar) => ({
        time: toEpochSeconds(bar.t),
        open: Number(bar.o),
        high: Number(bar.h),
        low: Number(bar.l),
        close: Number(bar.c),
      })),
    );
    chartRef.current?.timeScale().fitContent();
  }, [page]);

  const badgeTone =
    page?.data_state === "LIVE" ? "ok" : page?.data_state === "DELAYED" ? "warn" : "bad";

  return (
    <div className="market-chart">
      <div className="market-toolbar">
        <label className="toolbar-label" htmlFor="real-timeframe-select">
          Timeframe
        </label>
        <select
          id="real-timeframe-select"
          className="toolbar-select"
          value={interval}
          onChange={(event) => onIntervalChange(event.target.value as Timeframe)}
          // Prefetch on hover AND focus: keyboard users tabbing the dropdown get the same
          // warm-cache benefit as a mouse user resting on the option.
          onMouseEnter={(event) => {
            const option = (event.target as HTMLSelectElement).value as Timeframe;
            if (option !== interval) {
              prefetch(instrument, option);
            }
          }}
        >
          {TIMEFRAME_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>

        {/* The honesty badge. Our store was 28.8h stale for two days and nothing said so;
            a chart that renders old bars looking healthy is worse than no chart. */}
        {freshness ? (
          <span className={`freshness-badge freshness-${badgeTone}`} data-testid="freshness">
            {page?.partial_bar_included ? "forming · " : ""}
            {freshness}
          </span>
        ) : null}
        {page && page.gapped_bars > 0 ? (
          <span
            className="freshness-badge freshness-bad"
            data-testid="gapped-badge"
            title="Some bars contain missing minutes. They are shown, not averaged away."
          >
            {page.gapped_bars} gapped
          </span>
        ) : null}
        {loading ? <span className="chart-status">loading…</span> : null}
      </div>

      {error ? (
        <div className="chart-error" data-testid="chart-error">
          <strong>{retryable ? "Market data temporarily unavailable" : "Request failed"}</strong>
          <span>{error}</span>
          {retryable ? <span>Retrying is safe — the store is expected to recover.</span> : null}
        </div>
      ) : null}

      {page && page.bars.length === 0 && !error ? (
        <div className="chart-empty" data-testid="chart-empty">
          No bars for {instrument} at {interval}.
          {instrument.startsWith("FX.") ? " Forex markets are closed at weekends." : ""}
        </div>
      ) : null}

      <div ref={container} className="chart-canvas" />
    </div>
  );
}