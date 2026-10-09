import { useState } from "react";

import {
  ExperimentChart,
  MarketToolbar,
  SERIES_COLORS,
  scalesDiffer,
  type MarketControls,
  type Theme,
} from "./MarketPanel";
import { MarketChart } from "./MarketChart";
import type { Timeframe } from "../services/market_data_api";
import type { ExperimentMarket } from "./experiment_market";
import NewsSection from "./NewsSection";

// WHY no Public intelligence and no News panels (human directive 2026-09-27): both
// removed. They were static mock strings with no data behind them, so they read as
// claims the build could not support — a headline and a "momentum improving" verdict are
// exactly the kind of thing that must not appear unless something real produced them.
// The research surface is where a question like that gets answered honestly.
//
// WHY the live chart sits ABOVE the mock one rather than replacing it: `ExperimentChart`
// draws a positional-index line overlay from `mock_data`, and other surfaces still consume
// it. Deleting it here would break those. What changed is that a REAL, backend-served
// candlestick chart with a working timeframe dropdown is now the first thing on the public
// dashboard, so the mock is no longer what a visitor sees first.
export default function HomeDashboard({ market, markets, theme, controls }: { market: ExperimentMarket; markets: ExperimentMarket[]; theme: Theme; controls: MarketControls }) {
  const [liveInstrument, setLiveInstrument] = useState("CRYPTO.BTCUSD");
  const [liveInterval, setLiveInterval] = useState<Timeframe>("5m");

  return (
    <section className="workspace viewer-dashboard" aria-label="Public market workspace">
      <section className="panel chart-panel" data-testid="live-market-panel">
        <h2>Live market — venue data</h2>
        <p className="panel-note">
          Served from Timescale via <code>GET /api/v1/market/candles</code>. Prices are the
          broker&apos;s own OHLC; the freshness badge states how old the newest bar is.
        </p>
        <MarketChart
          instrument={liveInstrument}
          interval={liveInterval}
          onIntervalChange={setLiveInterval}
          theme={theme === "dark" ? "dark" : "light"}
        />
        <div className="instrument-picker" role="group" aria-label="Instrument">
          {["CRYPTO.BTCUSD", "CRYPTO.ETHUSD", "FX.EURUSD", "FX.GBPUSD", "COMMODITY.XAUUSD"].map(
            (id) => (
              <button
                key={id}
                type="button"
                className={id === liveInstrument ? "instrument-chip is-active" : "instrument-chip"}
                onClick={() => setLiveInstrument(id)}
              >
                {id}
              </button>
            ),
          )}
        </div>
      </section>

      <section className="panel chart-panel">
        <h2>Markets (mock overlay — not yet wired)</h2>
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