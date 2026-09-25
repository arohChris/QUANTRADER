import { useEffect, useRef } from "react";
import { ColorType, LineSeries, createChart, type UTCTimestamp } from "lightweight-charts";
import {
  MOCK_INSTRUMENTS,
  TIMEFRAME_WINDOW,
  pathPhase,
  withSymbol,
  type ExperimentMarket,
} from "./experiment_market";

type Theme = "dark" | "light";

const CHART_PALETTE: Record<Theme, { text: string; grid: string; border: string }> = {
  dark: { text: "#a5b4fc", grid: "rgba(129, 140, 248, 0.14)", border: "rgba(34, 211, 238, 0.35)" },
  light: { text: "#475569", grid: "rgba(11, 18, 32, 0.08)", border: "rgba(11, 18, 32, 0.16)" },
};

const CARD_COLORS = [
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

// Renders each price at its own instrument precision: EURUSD 1.0842, USDJPY 149.62,
// BTC 64250.4, XAG 27.415. Without this the same row mixes 4dp and float noise.
export function formatPrice(value: number, decimals: number): string {
  return value.toFixed(decimals);
}

// What the market is doing, read straight off the mock series. Presentation of
// mock data only: no market authority, no risk figure, no order implication.
type AssetTrend = {
  market: ExperimentMarket;
  label: string;
  color: string;
  decimals: number;
  last: number;
  high: number;
  low: number;
  // The asset's trend of the day: move across the visible window, in percent.
  changePct: number;
  // Depth below the window high, in percent. Negative while still above the open.
  drawdownPct: number;
  phase: "advance" | "drawdown";
  window: number;
};

function readTrend(base: ExperimentMarket, symbol: string, color: string, index: number): AssetTrend {
  const market = withSymbol(base, symbol);
  const window = TIMEFRAME_WINDOW[market.timeframe];
  const visible = market.points.slice(-window);
  const first = visible[0]?.close ?? 1;
  const last = visible.at(-1)?.close ?? first;
  const high = Math.max(...visible.map((point) => point.close));
  const low = Math.min(...visible.map((point) => point.close));
  const instrument = MOCK_INSTRUMENTS[index];

  return {
    market,
    label: instrument.label,
    color,
    decimals: instrument.decimals,
    last,
    high,
    low,
    changePct: Number((((last - first) / first) * 100).toFixed(2)),
    drawdownPct: Number((((last - high) / high) * 100).toFixed(2)),
    phase: pathPhase(market.points.length),
    window,
  };
}

// Each dashboard owns one sparkline at its own price level. The chart is built once
// per asset/theme and updated in place, so twelve live panels do not rebuild per tick.
function MarketSparkline({ trend, theme }: { trend: AssetTrend; theme: Theme }) {
  const container = useRef<HTMLDivElement>(null);
  const series = useRef<ReturnType<ReturnType<typeof createChart>["addSeries"]> | null>(null);

  useEffect(() => {
    if (!container.current) {
      return undefined;
    }

    const palette = CHART_PALETTE[theme];
    const chart = createChart(container.current, {
      width: container.current.clientWidth,
      height: 96,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: palette.text,
        attributionLogo: false,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: palette.grid } },
      rightPriceScale: { visible: false },
      timeScale: { visible: false, borderColor: palette.border },
      crosshair: { mode: 0 },
      handleScale: false,
      handleScroll: false,
    });
    series.current = chart.addSeries(LineSeries, { color: trend.color, lineWidth: 2, priceLineVisible: false });

    return () => {
      series.current = null;
      chart.remove();
    };
  }, [theme, trend.color]);

  useEffect(() => {
    series.current?.setData(
      trend.market.points.slice(-trend.window).map((point, index) => ({
        time: index as UTCTimestamp,
        value: point.close,
      })),
    );
  }, [trend]);

  return <div ref={container} className="market-sparkline" aria-label={`${trend.label} price chart`} />;
}

// Where price sits inside the day's range, as a bar with a position marker.
function RangeBar({ trend }: { trend: AssetTrend }) {
  const span = trend.high - trend.low;
  const position = span === 0 ? 50 : Math.min(100, Math.max(0, ((trend.last - trend.low) / span) * 100));
  const depth = Math.min(100, Math.abs(trend.drawdownPct) * 12);

  return (
    <div
      className="range-bar"
      title={`day range ${formatPrice(trend.low, trend.decimals)} to ${formatPrice(trend.high, trend.decimals)}`}
    >
      <span className="range-fill" style={{ width: `${position}%` }} />
      <span className="range-marker" style={{ left: `${position}%` }} />
      <span className="range-drawdown" style={{ width: `${depth}%` }} />
    </div>
  );
}

function MarketCard({ trend, theme, index, total }: { trend: AssetTrend; theme: Theme; index: number; total: number }) {
  const up = trend.changePct >= 0;

  return (
    <article className="panel market-card" aria-label={`${trend.label} market dashboard`}>
      <header className="market-card-head">
        <h3>
          {trend.market.symbol}
          <span className="market-card-alias">{trend.label}</span>
        </h3>
        <span className={trend.phase === "drawdown" ? "phase-chip is-drawdown" : "phase-chip"}>{trend.phase}</span>
      </header>
      <p className="market-trend">
        <span className={up ? "trend-value is-up" : "trend-value is-down"}>
          {up ? "▲" : "▼"} {Math.abs(trend.changePct).toFixed(2)}%
        </span>
        <span className="trend-caption">trend of the day</span>
      </p>
      <MarketSparkline trend={trend} theme={theme} />
      <RangeBar trend={trend} />
      <dl className="market-card-stats">
        <div>
          <dt>last</dt>
          <dd>{formatPrice(trend.last, trend.decimals)}</dd>
        </div>
        <div>
          <dt>from high</dt>
          <dd className={trend.drawdownPct < 0 ? "is-negative" : "is-positive"}>{trend.drawdownPct.toFixed(2)}%</dd>
        </div>
        <div>
          <dt>day range</dt>
          <dd>
            {formatPrice(trend.low, trend.decimals)} – {formatPrice(trend.high, trend.decimals)}
          </dd>
        </div>
        <div>
          <dt>window</dt>
          <dd>
            {trend.window} pts · {trend.market.timeframe}
          </dd>
        </div>
      </dl>
      <p className="market-card-note">
        {up
          ? `Trend of the day is up ${trend.changePct.toFixed(2)}% across the window.`
          : `Trend of the day is down ${Math.abs(trend.changePct).toFixed(2)}%, ${Math.abs(trend.drawdownPct).toFixed(2)}% below the window high.`}{" "}
        Panel {index + 1} of {total} · as_of {new Date(trend.market.asOf).toISOString().slice(11, 19)}Z
      </p>
    </article>
  );
}

// Tile per instrument, tinted by the day's move: green advancing, red in drawdown.
function MarketsHeatmap({ trends }: { trends: AssetTrend[] }) {
  const peak = Math.max(...trends.map((trend) => Math.abs(trend.changePct)), 0.01);

  return (
    <div className="markets-heatmap" aria-label="Market heatmap by trend of the day">
      {trends.map((trend) => {
        const up = trend.changePct >= 0;
        const weight = Math.min(1, Math.abs(trend.changePct) / peak);
        return (
          <div
            className={up ? "heat-tile is-up" : "heat-tile is-down"}
            key={trend.market.symbol}
            style={{ opacity: 0.55 + weight * 0.45 }}
            title={`${trend.market.symbol} ${trend.changePct.toFixed(2)}% today`}
          >
            <span className="heat-symbol">{trend.market.symbol}</span>
            <span className="heat-value">{up ? "+" : ""}{trend.changePct.toFixed(2)}%</span>
          </div>
        );
      })}
    </div>
  );
}

// Ranked best to worst on the day's move, with a proportional bar.
function Leaderboard({ trends }: { trends: AssetTrend[] }) {
  const ranked = [...trends].sort((a, b) => b.changePct - a.changePct);
  const peak = Math.max(...ranked.map((trend) => Math.abs(trend.changePct)), 0.01);

  return (
    <section className="leaderboard" aria-label="Ranked leaders and laggards today">
      <h2 className="leaderboard-heading">Trend of the day — ranked</h2>
      <ol className="leaderboard-list">
        {ranked.map((trend, rank) => {
          const up = trend.changePct >= 0;
          return (
            <li className="leaderboard-row" key={trend.market.symbol}>
              <span className="leaderboard-rank">{rank + 1}</span>
              <span className="leaderboard-symbol">{trend.market.symbol}</span>
              <span className="leaderboard-bar">
                <span
                  className={up ? "leaderboard-fill is-up" : "leaderboard-fill is-down"}
                  style={{ width: `${(Math.abs(trend.changePct) / peak) * 100}%` }}
                />
              </span>
              <span className={up ? "leaderboard-value is-up" : "leaderboard-value is-down"}>
                {up ? "+" : ""}
                {trend.changePct.toFixed(2)}%
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export default function MarketsGrid({ base, theme }: { base: ExperimentMarket; theme: Theme }) {
  const trends = MOCK_INSTRUMENTS.map((instrument, index) =>
    readTrend(base, instrument.symbol, CARD_COLORS[index % CARD_COLORS.length], index),
  );
  const advancing = trends.filter((trend) => trend.changePct >= 0).length;
  const best = trends.reduce((top, trend) => (trend.changePct > top.changePct ? trend : top), trends[0]);
  const worst = trends.reduce((low, trend) => (trend.changePct < low.changePct ? trend : low), trends[0]);

  return (
    <section className="markets-view" aria-label="Markets dashboards">
      <header className="markets-view-head">
        <h1>Markets</h1>
        <p className="muted">
          {trends.length} mock dashboards · as_of {base.asOf} · {advancing} advancing / {trends.length - advancing} in drawdown
        </p>
      </header>
      <div className="markets-summary">
        <p className="summary-chip">
          Leader <strong>{best.market.symbol}</strong> {best.changePct >= 0 ? "+" : ""}
          {best.changePct.toFixed(2)}%
        </p>
        <p className="summary-chip">
          Laggard <strong>{worst.market.symbol}</strong> {worst.changePct >= 0 ? "+" : ""}
          {worst.changePct.toFixed(2)}%
        </p>
        <p className="summary-chip">
          Widest drawdown <strong>{worst.market.symbol}</strong> {worst.drawdownPct.toFixed(2)}% from high
        </p>
      </div>
      <MarketsHeatmap trends={trends} />
      <Leaderboard trends={trends} />
      <div className="markets-grid">
        {trends.map((trend, index) => (
          <MarketCard key={trend.market.symbol} trend={trend} theme={theme} index={index} total={trends.length} />
        ))}
      </div>
    </section>
  );
}
