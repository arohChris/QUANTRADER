import {
  ExperimentChart,
  MarketToolbar,
  SERIES_COLORS,
  scalesDiffer,
  type MarketControls,
  type Theme,
} from "./MarketPanel";
import type { ExperimentMarket } from "./experiment_market";

const INTELLIGENCE = [
  ["FX.EURUSD", "Momentum improving", "positive"],
  ["CRYPTO.BTCUSD", "More context needed", "warning"],
];

const NEWS = [
  ["FX", "Dollar steadies before the next data window"],
  ["CRYPTO", "Volatility remains the dominant theme"],
];

export default function HomeDashboard({ market, markets, theme, controls }: { market: ExperimentMarket; markets: ExperimentMarket[]; theme: Theme; controls: MarketControls }) {
  return (
    <section className="workspace viewer-dashboard" aria-label="Public market workspace">
      <section className="panel chart-panel">
        <h2>Markets</h2>
        <MarketToolbar market={market} {...controls} />
        <ExperimentChart markets={markets} theme={theme} />
        <div className="chart-footer">
          <span>as_of: {market.asOf}</span>
          <span>quality: {market.quality}</span>
          <span>reference_snapshot_id: {market.referenceSnapshotId}</span>
        </div>
        <ul className="series-legend" aria-label="Displayed assets">
          {markets.map((entry, index) => (
            <li key={entry.symbol}>
              <span className="series-dot" style={{ background: SERIES_COLORS[index % SERIES_COLORS.length] }} />
              {entry.symbol}
              <span className="series-last">{entry.points.at(-1)?.close}</span>
            </li>
          ))}
          {scalesDiffer(markets) ? (
            <li className="scale-hint">scales differ — compare each asset in Markets</li>
          ) : null}
        </ul>
      </section>
      <div className="side-panels">
        <section className="panel">
          <h2>Public intelligence</h2>
          {INTELLIGENCE.map(([symbol, message, tone]) => (
            <div className="signal" key={symbol}>
              <span>{symbol}</span>
              <span className={tone}>{message}</span>
            </div>
          ))}
        </section>
        <section className="panel">
          <h2>News</h2>
          {NEWS.map(([tag, headline]) => (
            <div className="signal" key={headline}>
              <span>{tag}</span>
              <span>{headline}</span>
            </div>
          ))}
        </section>
        <section className="panel">
          <h2>Predictive quality for today</h2>
          <p className="data-large">Estimated accuracy today: 68.4%</p>
          <p>Mock daily estimate. The backend will provide the validated figure later.</p>
        </section>
      </div>
      <div className="state-row" aria-label="Presentation states">
        <span>loading</span>
        <span>empty</span>
        <span>stale</span>
      </div>
    </section>
  );
}
