// WHY: the header the Trader dashboard opens with — total P&L and the three
// trade-state counts (active/closed/rejected) for the period the user picks
// (M10.4.11). Presentation only: every number is a precomputed mock record from
// mock_trader_data.ts, so no aggregation and no I/O live here; real per-user
// figures wire to the M6/M7 endpoints in a later slice.
import { useState } from "react";
import { signedMoney } from "./surface_ui";
import {
  MOCK_TRADE_SUMMARY,
  TRADE_SUMMARY_PERIODS,
  type TradeSummaryPeriod,
} from "./mock_trader_data";

// Human labels over the period keys (human directive: the day button reads as
// Day, the current day). Keys are lowercase words so they never collide with
// the chart-timeframe literals banned app-wide (spec Amendment A1).
const PERIOD_LABELS: Record<TradeSummaryPeriod, string> = {
  day: "Day",
  week: "Week",
  month: "Month",
  year: "Year",
  all: "All",
};

export default function TradeSummaryHeader() {
  const [period, setPeriod] = useState<TradeSummaryPeriod>("day");
  // Unknown key falls back to the Day record — never renders undefined.
  const summary = MOCK_TRADE_SUMMARY[period] ?? MOCK_TRADE_SUMMARY["day"];
  const pnl = summary.total_pnl;
  const pnlTone =
    pnl !== null && pnl < 0 ? "summary-value is-negative" : "summary-value is-positive";

  return (
    <header className="trade-summary-header" aria-label="Trade summary">
      <div className="summary-stats">
        <div className="summary-stat">
          <span className="summary-label">Total P&L</span>
          <span className={pnlTone}>{signedMoney(pnl)}</span>
        </div>
        <div className="summary-stat">
          <span className="summary-label">Active</span>
          <span className="summary-value">{summary.active}</span>
        </div>
        <div className="summary-stat">
          <span className="summary-label">Closed</span>
          <span className="summary-value">{summary.closed}</span>
        </div>
        <div className="summary-stat">
          <span className="summary-label">Rejected</span>
          <span className="summary-value">{summary.rejected}</span>
        </div>
      </div>
      <div className="mode-switch" role="group" aria-label="Summary period">
        {TRADE_SUMMARY_PERIODS.map((key) => (
          <button
            key={key}
            type="button"
            className={period === key ? "mode-button is-active" : "mode-button"}
            aria-pressed={period === key}
            title={key === "all" ? "All periods" : `Current ${PERIOD_LABELS[key].toLowerCase()}`}
            onClick={() => setPeriod(key)}
          >
            {PERIOD_LABELS[key]}
          </button>
        ))}
      </div>
      <p className="summary-provenance muted">
        Mock summary for user {summary.user_id ?? "—"} · per-user figures wire to M6/M7 endpoints
      </p>
    </header>
  );
}
