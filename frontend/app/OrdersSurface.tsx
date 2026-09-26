// Orders and executions surface. Field names are copied from the frozen contracts
// (order.py, execution.py); state machine per modules/06_M6_Execution.md:52-62.
// The order contract has no filled_qty field — filled quantity is summed from fills.
import { useState } from "react";
import { MOCK_ORDERS, ORDER_STATES, ORDER_TRANSITIONS, type MockOrder } from "./mock_trader_data";
import { KeyValues, Note, SurfaceHead, SurfacePanel, money, stateClass } from "./surface_ui";

const TRADE_CHAIN = ["TradePlan", "preview", "RiskDecision", "confirmation", "Order", "ExecutionResult", "Fill", "Ledger"];

function filledFor(order: MockOrder): number {
  return order.execution?.fills.reduce((sum, fill) => sum + fill.qty, 0) ?? 0;
}

export default function OrdersSurface() {
  const [selected, setSelected] = useState<string>(MOCK_ORDERS[0].order_id);
  const order = MOCK_ORDERS.find((entry) => entry.order_id === selected) ?? MOCK_ORDERS[0];
  const filled = filledFor(order);

  return (
    <section className="trader-surface" aria-label="Orders and executions">
      <SurfaceHead
        title="Orders"
        subtitle={TRADE_CHAIN.join(" → ")}
        badge="preview only · no order can be submitted from this build"
      />

      <section className="chain-strip" aria-label="Trade chain">
        {TRADE_CHAIN.map((step, index) => (
          <span className="chain-step" key={step}>
            {step}
            {index < TRADE_CHAIN.length - 1 ? (
              <span className="chain-arrow" aria-hidden="true">
                →
              </span>
            ) : null}
          </span>
        ))}
      </section>

      <SurfacePanel title="Order state machine">
        <div className="state-legend">
          {ORDER_STATES.map((state) => (
            <span
              className={stateClass(state)}
              key={state}
              title={`next: ${ORDER_TRANSITIONS[state].join(", ") || "terminal"}`}
            >
              {state}
              {state === "unknown" ? " ⚠ query before retry" : ""}
            </span>
          ))}
        </div>
        <Note>
          An UNKNOWN state is never treated as success or failure — the broker is queried before any retry. State
          transitions are logged with the correlation id, never inferred from the absence of an error.
        </Note>
      </SurfacePanel>

      <SurfacePanel title="Orders">
        <table className="data-table">
          <thead>
            <tr>
              <th>client order id</th>
              <th>symbol</th>
              <th>side</th>
              <th>type</th>
              <th>qty</th>
              <th>filled</th>
              <th>price</th>
              <th>stop loss</th>
              <th>take profit</th>
              <th>state</th>
              <th>cap check</th>
              <th>timestamp</th>
            </tr>
          </thead>
          <tbody>
            {MOCK_ORDERS.map((entry) => (
              <tr
                key={entry.order_id}
                className={entry.order_id === selected ? "is-selected" : undefined}
                onClick={() => setSelected(entry.order_id)}
              >
                <td className="mono" title={`correlation ${entry.correlation_id} · trace ${entry.trace_id}`}>
                  {entry.client_order_id.slice(0, 13)}…
                </td>
                <td className="mono">{entry.symbol}</td>
                <td>{entry.side}</td>
                <td>{entry.order_type}</td>
                <td className="mono">{entry.qty}</td>
                <td className="mono">
                  {filledFor(entry)} / {entry.qty}
                </td>
                <td className="mono">{entry.price ?? "market"}</td>
                <td className="mono">{entry.stop_loss ?? "—"}</td>
                <td className="mono">{entry.take_profit ?? "—"}</td>
                <td>
                  <span className={stateClass(entry.state)}>{entry.state}</span>
                </td>
                <td className={entry.broker_capability_checked ? "is-positive" : "is-negative"}>
                  {entry.broker_capability_checked ? "yes" : "no"}
                </td>
                <td className="mono muted">{entry.timestamp}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Note>
          client_order_id is the idempotency key: a retry of the same trade plan reuses it, so the broker
          deduplicates instead of double-filling. Lineage for every row is on hover.
        </Note>
      </SurfacePanel>

      <SurfacePanel title={`Execution detail — ${order.order_id}`}>
        {order.execution ? (
          <>
            <KeyValues
              rows={[
                { label: "state", value: order.execution.state },
                { label: "broker order id", value: order.execution.broker_order_id ?? "—" },
                { label: "avg fill price", value: order.execution.avg_fill_price ?? "—" },
                { label: "latency", value: `${order.execution.latency_ms ?? "—"} ms` },
                { label: "slippage", value: `${order.execution.slippage_bps ?? "—"} bps` },
                { label: "trade plan", value: order.execution.trade_plan_id ?? "—" },
              ]}
            />
            <table className="data-table">
              <thead>
                <tr>
                  <th>fill id</th>
                  <th>price</th>
                  <th>qty</th>
                  <th>side</th>
                  <th>commission</th>
                  <th>timestamp</th>
                </tr>
              </thead>
              <tbody>
                {order.execution.fills.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="muted">
                      no fills recorded
                    </td>
                  </tr>
                ) : (
                  order.execution.fills.map((fill) => (
                    <tr key={fill.fill_id}>
                      <td className="mono">{fill.fill_id}</td>
                      <td className="mono">{fill.price}</td>
                      <td className="mono">{fill.qty}</td>
                      <td>{fill.side ?? "—"}</td>
                      <td className="mono">{money(fill.commission)}</td>
                      <td className="mono muted">{fill.timestamp ?? "—"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </>
        ) : (
          <Note>No ExecutionResult for this order — it never reached the broker.</Note>
        )}
        <Note>
          Filled {filled} of {order.qty} lots, summed from the fills above. The raw broker response is kept for
          audit only and is never used as logic.
        </Note>
      </SurfacePanel>
    </section>
  );
}
