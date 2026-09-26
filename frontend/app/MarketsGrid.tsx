import { useEffect, useMemo, useRef } from "react";
import { ColorType, LineSeries, createChart, type UTCTimestamp } from "lightweight-charts";
import {
  MOCK_INSTRUMENTS,
  TIMEFRAME_WINDOW,
  classOf,
  pathPhase,
  readSessions,
  withSymbol,
  type ExperimentMarket,
} from "./experiment_market";

type Theme = "dark" | "light";

// ONE source of truth for the sparkline height. The old defect: the chart was built at
// 96px inside a 4.5rem (72px) box, so the canvas bled over the card below it.
const SPARKLINE_HEIGHT_PX = 72;

// Sparkline palette: cool axis neutrals, data accents for lines.
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
    phase: pathPhase(market.points.length),
    window,
  };
}

// Each dashboard owns one sparkline at its own price level. The chart is built once
// per asset/theme and updated in place, so twelve live panels do not rebuild per tick.
function MarketSparkline({ trend, theme }: { trend: AssetTrend; theme: Theme }) {
  const container = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof createChart> | null>(null);
  const series = useRef<ReturnType<ReturnType<typeof createChart>["addSeries"]> | null>(null);

  useEffect(() => {
    if (!container.current) {
      return undefined;
    }

    const palette = CHART_PALETTE[theme];
    const chart = createChart(container.current, {
      width: container.current.clientWidth,
      height: SPARKLINE_HEIGHT_PX,
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
    chartRef.current = chart;
    series.current = chart.addSeries(LineSeries, { color: trend.color, lineWidth: 2, priceLineVisible: false });

    // The card grid reflows when the viewport changes column count. Without this the
    // canvas kept its old pixel width and the line was stretched or clipped.
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (width && width > 0) {
        chart.applyOptions({ width: Math.round(width) });
      }
    });
    observer.observe(container.current);

    return () => {
      observer.disconnect();
      chartRef.current = null;
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

  return (
    <div
      ref={container}
      className="market-sparkline"
      style={{ height: `${SPARKLINE_HEIGHT_PX}px` }}
      aria-label={`${trend.label} price chart`}
    />
  );
}

// Where price sits inside the day's range, as a bar with a position marker.
// Two elements only: the fill and the marker. The old third overlay (drawdown) was
// absolutely positioned on top of the fill, which read as elements floating over
// each other, and it duplicated a number the card no longer shows.
function RangeBar({ trend }: { trend: AssetTrend }) {
  const span = trend.high - trend.low;
  const position = span === 0 ? 50 : Math.min(100, Math.max(0, ((trend.last - trend.low) / span) * 100));

  return (
    <div
      className="range-bar"
      title={`day range ${formatPrice(trend.low, trend.decimals)} to ${formatPrice(trend.high, trend.decimals)}`}
    >
      <span className="range-fill" style={{ width: `${position}%` }} />
      <span className="range-marker" style={{ left: `${position}%` }} />
    </div>
  );
}

function MarketCard({ trend, theme }: { trend: AssetTrend; theme: Theme }) {
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
      <p className="market-card-trend">
        <span className={up ? "trend-value is-up" : "trend-value is-down"}>
          {up ? "▲" : "▼"} {Math.abs(trend.changePct).toFixed(2)}%
        </span>
        <span className="trend-caption">trend is {up ? "up" : "down"}</span>
      </p>
      <MarketSparkline trend={trend} theme={theme} />
      <RangeBar trend={trend} />
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
    <section className="leaderboard" aria-label="Assets ranked by today's move">
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

// Active trading sections, one per frozen AssetClass. This replaces the old three-chip
// summary row (best asset, worst asset, widest drawdown) with the question the reader
// actually has: which markets are trading right now, and for how much longer. Read from
// the UTC clock, so a section cannot claim to be open when it is not. These are market
// sessions, never positions, orders, or fills.
function ActiveTrading({ base, trends }: { base: ExperimentMarket; trends: AssetTrend[] }) {
  const now = useMemo(() => new Date(), []);
  const sessions = useMemo(() => readSessions(now), [now]);

  return (
    <section className="active-trading" aria-label="Active trading sections by asset class">
      {sessions.map((session) => {
        const members = trends.filter((trend) => classOf(trend.market.symbol) === session.assetClass);
        const advancing = members.filter((trend) => trend.changePct >= 0).length;
        const best = members.reduce<AssetTrend | null>((top, trend) => (!top || trend.changePct > top.changePct ? trend : top), null);

        return (
          <article className={session.open ? "active-trading-card is-open" : "active-trading-card"} key={session.assetClass}>
            <header>
              <h2>{session.label}</h2>
              <span className={session.open ? "session-chip is-open" : "session-chip is-closed"}>
                {session.open ? "session open" : "session closed"}
              </span>
            </header>
            <p className="session-window mono">{session.window}</p>
            <p className={session.open ? "session-countdown is-open" : "session-countdown"}>{session.countdown ?? "continuous"}</p>
            <p className="session-breadth">
              {advancing} of {members.length} advancing
              {best ? (
                <>
                  {" · best "}
                  <strong>{best.market.symbol}</strong> {best.changePct >= 0 ? "+" : ""}
                  {best.changePct.toFixed(2)}%
                </>
              ) : null}
            </p>
          </article>
        );
      })}
    </section>
  );
}

export default function MarketsGrid({ base, theme }: { base: ExperimentMarket; theme: Theme }) {
  const trends = MOCK_INSTRUMENTS.map((instrument, index) =>
    readTrend(base, instrument.symbol, CARD_COLORS[index % CARD_COLORS.length], index),
  );

  return (
    <section className="markets-view" aria-label="Markets dashboards">
      <header className="markets-view-head">
        <h1>Markets</h1>
      </header>
      <ActiveTrading base={base} trends={trends} />
      <MarketsHeatmap trends={trends} />
      <Leaderboard trends={trends} />
      <div className="markets-grid">
        {trends.map((trend) => (
          <MarketCard key={trend.market.symbol} trend={trend} theme={theme} />
        ))}
      </div>
    </section>
  );
}
