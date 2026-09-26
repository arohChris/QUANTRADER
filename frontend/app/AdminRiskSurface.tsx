// Admin — Risk. Global limits, the three-layer override, and the anatomy of a
// RiskDecision so an Admin can read why a trade was refused.
//
//   RiskLimits / SystemConfig -> shared/core/config.py:34-52
//   RiskDecision              -> shared/contracts/risk.py:21-31
//
// Editing limits is a versioned, audited, backend operation. This surface shows the
// current limits and their bounds; it does not let a browser write them.
import { MOCK_RISK_DECISION, MOCK_RISK_LIMITS, MOCK_SYSTEM_CONFIG, RISK_REJECTION_CODES } from "./mock_admin_data";
import { AdminHead, AdminPanel, AdminScope, RedactedNote } from "./admin_ui";
import { Note } from "./surface_ui";

const LAYERS = ["Automatic default", "Your override", "Reset to default"] as const;

export default function AdminRiskSurface() {
  const limits = MOCK_RISK_LIMITS;

  return (
    <section className="admin-surface" aria-label="Admin risk">
      <AdminHead
        title="Risk"
        subtitle="Global risk limits and the refusal reasons they can produce. These bounds are enforced at more than one layer, so a single bypass cannot open the gate."
        badge={`${MOCK_SYSTEM_CONFIG.base_currency} · risk budget <10 ms · read-only`}
      />
      <AdminScope scope="global" />

      <AdminPanel title="Global limits">
        <table className="data-table">
          <thead>
            <tr>
              <th>limit</th>
              <th>value</th>
              <th>bound</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>max_exposure_per_symbol</td>
              <td className="mono">{(limits.max_exposure_per_symbol * 100).toFixed(0)}%</td>
              <td className="muted mono">0 – 1 (fraction of the account)</td>
            </tr>
            <tr>
              <td>max_leverage</td>
              <td className="mono">{limits.max_leverage}×</td>
              <td className="muted mono">≥ 1</td>
            </tr>
            <tr>
              <td>min_free_margin</td>
              <td className="mono">
                {limits.min_free_margin} {MOCK_SYSTEM_CONFIG.base_currency}
              </td>
              <td className="muted mono">free margin kept in reserve</td>
            </tr>
            <tr>
              <td>max_drawdown</td>
              <td className="mono">{(limits.max_drawdown * 100).toFixed(0)}%</td>
              <td className="muted mono">session drawdown ceiling</td>
            </tr>
          </tbody>
        </table>
        <Note>
          A limit outside its sane bound stops the system starting rather than being clamped at
          runtime. Absurd configuration is refused, not tolerated.
        </Note>
        <RedactedNote />
      </AdminPanel>

      <AdminPanel title="Override layers">
        <ol className="admin-flow">
          {LAYERS.map((layer, index) => (
            <li key={layer}>
              <span className="admin-flow-index">{index + 1}</span>
              {layer}
            </li>
          ))}
        </ol>
        <p className="admin-warn">
          Hard safety boundaries cannot be overridden by any layer. An override narrows a limit;
          it never widens one past the hard ceiling.
        </p>
      </AdminPanel>

      <AdminPanel title="RiskDecision — field anatomy">
        <p className="muted">
          One decision is stamped per signal. The values below are the frozen field set, so this
          shape survives wiring unchanged.
        </p>
        <dl className="kv-grid">
          <div>
            <dt>decision_id</dt>
            <dd className="mono">{MOCK_RISK_DECISION.decision_id.slice(0, 8)}…</dd>
          </div>
          <div>
            <dt>approved</dt>
            <dd className={MOCK_RISK_DECISION.approved ? "is-positive" : "is-negative"}>
              {String(MOCK_RISK_DECISION.approved)}
            </dd>
          </div>
          <div>
            <dt>approved_qty</dt>
            <dd className="mono">{MOCK_RISK_DECISION.approved_qty ?? "—"}</dd>
          </div>
          <div>
            <dt>rejected_reason</dt>
            <dd className="mono">{MOCK_RISK_DECISION.rejected_reason ?? "—"}</dd>
          </div>
          <div>
            <dt>sizing_method</dt>
            <dd className="mono">{MOCK_RISK_DECISION.sizing_method}</dd>
          </div>
          <div>
            <dt>latency_budget_ms</dt>
            <dd className="mono">{MOCK_RISK_DECISION.latency_budget_ms}</dd>
          </div>
        </dl>
        <Note>
          A refusal always names the rule that refused it, with the numbers that failed — a bare
          "rejected" is not an acceptable answer.
        </Note>
      </AdminPanel>

      <AdminPanel title="Refusal codes">
        <p className="admin-token-row">
          {RISK_REJECTION_CODES.map((code) => (
            <span className="state-chip is-rejected" key={code}>
              {code}
            </span>
          ))}
        </p>
      </AdminPanel>
    </section>
  );
}
