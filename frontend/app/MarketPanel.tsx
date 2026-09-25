import { useEffect, useRef, useState } from "react";
import { ColorType, LineSeries, createChart, type UTCTimestamp } from "lightweight-charts";
import { ChevronDown } from "lucide-react";
import {
  MOCK_INSTRUMENTS,
  MOCK_TIMEFRAMES,
  TIMEFRAME_WINDOW,
  pathPhase,
  type ExperimentMarket,
  type Timeframe,
} from "./experiment_market";

export type Theme = "dark" | "light";

const CHART_PALETTE: Record<Theme, { text: string; grid: string; border: string; line: string; accent: string }> = {
  dark: {
    text: "#a5b4fc",
    grid: "rgba(129, 140, 248, 0.14)",
    border: "rgba(34, 211, 238, 0.35)",
    line: "#22d3ee",
    accent: "#a78bfa",
  },
  light: {
    text: "#475569",
    grid: "rgba(11, 18, 32, 0.08)",
    border: "rgba(11, 18, 32, 0.16)",
    line: "#0e7490",
    accent: "#6d28d9",
  },
};
// One bright colour per selected asset; index maps to the accent order (12 entries for 12 assets).
export const SERIES_COLORS = [
  "#22d3ee",
  "#a78bfa",
  "#a3e635",
  "#fbbf24",
  "#f472b6",
  "#38bdf8",
  "#fb923c",
  "#2dd4bf",
  "#c084fc",
  "#facc15",
  "#4ade80",
  "#f87171",
];

export type MarketControls = {
  selectedSymbols: string[];
  onToggleSymbol: (symbol: string) => void;
  onTimeframe: (timeframe: Timeframe) => void;
};

// Assets are picked inside a box, same control language as the timeframe select.
// The list holds every catalogue instrument (12) and stays open for multi-select.
function AssetSelectBox({ selectedSymbols, onToggleSymbol }: Pick<MarketControls, "selectedSymbols" | "onToggleSymbol">) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    const onPointerDown = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="toolbar-group" ref={root}>
      <span className="toolbar-label">Assets</span>
      <button
        className="asset-box"
        type="button"
        aria-label="Select assets"
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((current) => !current)}
      >
        {selectedSymbols.length} selected · {MOCK_INSTRUMENTS.length} available
      </button>
      {open ? (
        <ul className="asset-box-list" role="listbox" aria-multiselectable="true" aria-label="Select assets">
          {MOCK_INSTRUMENTS.map((instrument) => {
            const active = selectedSymbols.includes(instrument.symbol);
            return (
              <li key={instrument.symbol}>
                <button
                  type="button"
                  role="option"
                  aria-selected={active}
                  className={active ? "asset-option is-active" : "asset-option"}
                  onClick={() => onToggleSymbol(instrument.symbol)}
                >
                  <span className="asset-option-tick" aria-hidden="true">
                    {active ? "✓" : ""}
                  </span>
                  {instrument.label}
                  <span className="asset-option-symbol">{instrument.symbol}</span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}

export function MarketToolbar({ market, selectedSymbols, onToggleSymbol, onTimeframe }: { market: ExperimentMarket } & MarketControls) {
  const windowSize = TIMEFRAME_WINDOW[market.timeframe];

  return (
    <div className="market-toolbar">
      <AssetSelectBox selectedSymbols={selectedSymbols} onToggleSymbol={onToggleSymbol} />
      <div className="toolbar-group">
        <label className="toolbar-label" htmlFor="timeframe-select">
          Timeframe
        </label>
        <select
          id="timeframe-select"
          aria-label="Select timeframe"
          className="toolbar-select"
          value={market.timeframe}
          onChange={(event) => onTimeframe(event.target.value as Timeframe)}
        >
          {MOCK_TIMEFRAMES.map((timeframe) => (
            <option key={timeframe} value={timeframe}>
              {timeframe}
            </option>
          ))}
        </select>
      </div>
      <div className="toolbar-group toolbar-group-end">
        <span className="toolbar-label">Session time</span>
        <span className="clock" aria-label="Market session time">
          {new Date(market.asOf).toISOString().slice(11, 19)} UTC
        </span>
      </div>
      <span className="toolbar-note">as_of {market.asOf}</span>
      <span className="toolbar-note">window {windowSize} pts</span>
      <span className={pathPhase(market.points.length) === "drawdown" ? "phase-chip is-drawdown" : "phase-chip"}>
        mock path: {pathPhase(market.points.length)}
      </span>
    </div>
  );
}

export function ExperimentChart({ markets, theme }: { markets: ExperimentMarket[]; theme: Theme }) {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current) {
      return undefined;
    }

    const palette = CHART_PALETTE[theme];
    const chart = createChart(container.current, {
      width: container.current.clientWidth,
      height: 280,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: palette.text,
      },
      grid: {
        vertLines: { color: palette.grid },
        horzLines: { color: palette.grid },
      },
      rightPriceScale: { borderColor: palette.border },
      timeScale: { borderColor: palette.border },
    });

    // One bright line per selected asset at its real price level. No rebasing:
    // the axis stays absolute, so the legend carries each asset's actual last price
    // and a scale hint warns when the selected assets sit far apart in magnitude.
    markets.forEach((entry, index) => {
      const windowSize = TIMEFRAME_WINDOW[entry.timeframe];
      const visible = entry.points.slice(-windowSize);
      const series = chart.addSeries(LineSeries, {
        color: SERIES_COLORS[index % SERIES_COLORS.length],
        lineWidth: 2,
        priceLineVisible: false,
      });
      series.setData(
        visible.map((point, position) => ({
          time: position as UTCTimestamp,
          value: point.close,
        })),
      );
    });

    if (markets.length === 1) {
      const only = markets[0];
      const windowSize = TIMEFRAME_WINDOW[only.timeframe];
      const referenceSeries = chart.addSeries(LineSeries, {
        color: palette.accent,
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      referenceSeries.setData(
        only.reference.slice(-windowSize).map((value, position) => ({
          time: position as UTCTimestamp,
          value,
        })),
      );
    }

    chart.timeScale().fitContent();

    return () => chart.remove();
  }, [markets, theme]);

  return <div ref={container} className="experiment-chart" aria-label="Experimental market chart" />;
}

// True when the selected assets sit more than two magnitudes apart, so a single
// shared price axis would flatten the smaller one. Flagged in the legend, never rebased.
export function scalesDiffer(markets: ExperimentMarket[]): boolean {
  const prices = markets.map((entry) => entry.points.at(-1)?.close ?? 1).filter((price) => price > 0);
  if (prices.length < 2) {
    return false;
  }
  return Math.max(...prices) / Math.min(...prices) > 100;
}
