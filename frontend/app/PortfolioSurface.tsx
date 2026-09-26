// Portfolio surface. Field names are copied from the frozen contracts (portfolio.py, risk.py);
// values are mock and backend-owned (frontend_spec.md:61).
// Side is the sign of qty — the contract has no side field on a position.
import { MOCK_PORTFOLIO, MOCK_RISK_DECISION, type Position } from "./mock_trader_data";
import { KeyValues, Note, SurfaceHead, SurfacePanel, money, signedMoney } from "./surface_ui";

function PositionRow({ position }: { position: Position }) {
  const side = position.qty > 0 ? "long" : position.qty < 0 ? "short" : "flat";
  return (
    <tr>
      <td className="mono">{position.symbol}</td>
      <td>{side}</td>
      <td className="mono">{position.qty}</td>
      <td className="mono" title={`trade plan lineage: ${position.last_trade_plan_id ?? "none"}`}>
        {position.avg_entry}
      </td>
      <td className={position.unrealized_pnl !== null && position.unrealized_pnl < 0 ? "mono is-negative" : "mono is-positive"}>
        {signedMoney(position.unrealized_pnl)}
      </td>
      <td className="mono">{signedMoney(position.realized_pnl)}</td>
      <td className="mono muted">{position.last_trade_plan_id ?? "—"}</td>
    </tr>
  );
}

export default function PortfolioSurface() {
  const portfolio = MOCK_PORTFOLIO;
  const positions = Object.values(portfolio.positions);
  const cash = Object.entries(portfolio.cash);

  return (
    <section className="trader-surface" aria-label="Portfolio">
      <SurfaceHead
        title="Portfolio"
        subtitle={
          <>
            <span className="mono">{portfolio.user_id}</span> · broker <span className="mono">{portfolio.broker}</span> ·
            correlation <span className="mono">{portfolio.correlation_id}</span> · <span className="mono">{portfolio.timestamp}</span>
          </>
        }
        badge="mock tenant state · values are backend-owned and read-only"
      />

      <section className="stat-strip" aria-label="Account summary">
        <div className="stat">
          <span className="stat-label">cash</span>
          <span className="stat-value mono">{cash.map(([ccy, value]) => `${value.toLocaleString("en-US")} ${ccy}`).join(" · ")}</span>
        </div>
        <div className="stat">
          <span className="stat-label">equity</span>
          <span className="stat-value mono">{money(portfolio.equity)}</span>
        </div>
        <div className="stat">
          <span className="stat-label">margin used</span>
          <span className="stat-value mono">{money(portfolio.margin_used)}</span>
        </div>
        <div className="stat">
          <span className="stat-label">margin available</span>
          <span className="stat-value mono">{money(portfolio.margin_available)}</span>
        </div>
        <div className="stat">
          <span className="stat-label">leverage</span>
          <span className="stat-value mono">{portfolio.leverage}×</span>
        </div>
      </section>

      <SurfacePanel title="Positions">
        <table className="data-table">
          <thead>
            <tr>
              <th>symbol</th>
              <th>side</th>
              <th>qty (lots)</th>
              <th>avg entry</th>
              <th>unrealised</th>
              <th>realised</th>
              <th>last trade plan</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((position) => (
              <PositionRow key={position.symbol} position={position} />
            ))}
          </tbody>
        </table>
        <Note>
          Side is the sign of qty — there is no stored side field. Realised and unrealised come from M7; this view
          never recomputes them.
        </Note>
      </SurfacePanel>

      <SurfacePanel title="Exposure by symbol">
        <table className="data-table">
          <thead>
            <tr>
              <th>symbol</th>
              <th>exposure</th>
              <th>share of equity</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(portfolio.exposure).map(([symbol, exposure]) => (
              <tr key={symbol}>
                <td className="mono">{symbol}</td>
                <td className="mono">{money(exposure)}</td>
                <td className="mono">{((exposure / portfolio.equity) * 100).toFixed(2)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Note>
          Gross exposure per symbol per tenant, as tracked by M5. The share column is presentation-only arithmetic
          on a backend-owned number.
        </Note>
      </SurfacePanel>

      <SurfacePanel title="Last risk decision">
        <KeyValues
          rows={[
            { label: "decision", value: MOCK_RISK_DECISION.decision_id },
            {
              label: "approved",
              value: `${MOCK_RISK_DECISION.approved ? "yes" : "no"} · qty ${MOCK_RISK_DECISION.approved_qty}`,
              tone: MOCK_RISK_DECISION.approved ? "is-positive" : "is-negative",
            },
            { label: "sizing", value: MOCK_RISK_DECISION.sizing_method },
            { label: "budget", value: `${MOCK_RISK_DECISION.latency_budget_ms} ms` },
            ...Object.entries(MOCK_RISK_DECISION.limits_checked).map(([limit, passed]) => ({
              label: limit,
              value: passed ? "pass" : "fail",
              tone: passed ? "is-positive" : "is-negative",
            })),
          ]}
        />
        <Note>Rejection reasons carry an ErrorCode such as insufficient_margin; this decision was approved.</Note>
      </SurfacePanel>
    </section>
  );
}
