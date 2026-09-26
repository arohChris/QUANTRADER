import { Bot, UserRound } from "lucide-react";
import { ExperimentChart, MarketToolbar, type MarketControls, type Theme } from "./MarketPanel";
import TradingTicket from "./TradingTicket";
import type { ExperimentMarket } from "./experiment_market";

export type TradeMode = "auto" | "manual";

const TRADE_MODES: { id: TradeMode; label: string; note: string }[] = [
  { id: "auto", label: "Auto", note: "Strategy decides. Manual inputs stay hidden." },
  { id: "manual", label: "Manual", note: "You decide every field before preview." },
];

// True when the selected assets sit more than two magnitudes apart, so a
// single shared price axis would flatten the smaller one. Flagged, never rebased.
function scalesDiffer(markets: ExperimentMarket[]): boolean {
  const prices = markets.map((entry) => entry.points.at(-1)?.close ?? 1).filter((price) => price > 0);
  if (prices.length < 2) {
    return false;
  }
  return Math.max(...prices) / Math.min(...prices) > 100;
}

export default function TradePane({
  market,
  markets,
  theme,
  controls,
  tradeMode,
  onTradeMode,
}: {
  market: ExperimentMarket;
  markets: ExperimentMarket[];
  theme: Theme;
  controls: MarketControls;
  tradeMode: TradeMode;
  onTradeMode: (mode: TradeMode) => void;
}) {
  const manual = tradeMode === "manual";

  return (
    <section className="workspace trade-pane" aria-label="Trade pane">
      <div className="trade-pane-main">
        <section className="panel chart-panel">
          <h2>Execution chart</h2>
          <div className="market-toolbar">
            <div className="toolbar-group" role="group" aria-label="Trading mode">
              <span className="toolbar-label">Trading mode</span>
              <div className="mode-switch">
                {TRADE_MODES.map((entry) => (
                  <button
                    key={entry.id}
                    type="button"
                    className={tradeMode === entry.id ? "mode-button is-active" : "mode-button"}
                    aria-pressed={tradeMode === entry.id}
                    title={entry.note}
                    onClick={() => onTradeMode(entry.id)}
                  >
                    {entry.label}
                  </button>
                ))}
              </div>
            </div>
            <MarketToolbar market={market} {...controls} />
          </div>
          <ExperimentChart markets={markets} theme={theme} />
        </section>
        <section className="panel">
          <h2>Execution checklist</h2>
          <p className="data-large">{manual ? "Preview before confirm" : "Strategy decides"}</p>
          <p>
            {manual
              ? "Manual mode: every field is yours to set, then preview. Nothing is sent."
              : "Auto mode: inputs are hidden because the strategy owns the decision. Nothing is sent."}
          </p>
        </section>
      </div>
      <aside className="workspace-side">
        {manual ? <TradingTicket /> : <AutoModePanel />}
      </aside>
    </section>
  );
}

function AutoModePanel() {
  return (
    <section className="panel auto-mode-panel" aria-label="Automatic trading mode">
      <h2>Automatic mode</h2>
      <p className="data-large">Strategy owns the decision</p>
      <p>Manual inputs are hidden in this mode. Switch to Manual to place a trade yourself.</p>
      <ul className="auto-mode-list">
        <li>Signal → strategy → risk decision</li>
        <li>No manual quantity, entry, stop or target</li>
        <li>Still preview-only: nothing is submitted</li>
      </ul>
    </section>
  );
}
