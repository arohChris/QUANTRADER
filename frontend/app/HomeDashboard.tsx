import {
  ExperimentChart,
  MarketToolbar,
  SERIES_COLORS,
  scalesDiffer,
  type MarketControls,
  type Theme,
} from "./MarketPanel";
import type { ExperimentMarket } from "./experiment_market";
import NewsSection from "./NewsSection";

// WHY no Public intelligence and no News panels (human directive 2026-09-27): both
// removed. They were static mock strings with no data behind them, so they read as
// claims the build could not support — a headline and a "momentum improving" verdict are
// exactly the kind of thing that must not appear unless something real produced them.
// The research surface is where a question like that gets answered honestly.
export default function HomeDashboard({ market, markets, theme, controls }: { market: ExperimentMarket; markets: ExperimentMarket[]; theme: Theme; controls: MarketControls }) {
  return (
    <section className="workspace viewer-dashboard" aria-label="Public market workspace">
      <section className="panel chart-panel">
        <h2>Markets</h2>
        <MarketToolbar market={market} {...controls} />
        <ExperimentChart markets={markets} theme={theme} />
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
        <NewsSection />
        <section className="panel">
          <h2>Predictive quality for today</h2>
          <p className="data-large">Estimated accuracy today: 68.4%</p>
          <p>Mock daily estimate. The backend will provide the validated figure later.</p>
        </section>
      </div>
    </section>
  );
}
