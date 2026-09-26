// Admin — System. Reads the same health vocabulary the backend actually emits.
//
// WHY two vocabularies and not one: `HealthStatus.status` (engine_interface.py:31) has
// FOUR values including `offline`, while `HealthSnapshot.status` (health.py:25) has THREE
// and no `offline`. Lifecycle's roll-up downgrades `offline` to `degraded`
// (lifecycle.py:80-81). So an engine can be offline while the snapshot only ever says
// degraded — flattening the two would show an Admin a state the aggregator cannot emit.
import { ADMIN_LINEAGE, MOCK_ENVIRONMENT, MOCK_HEALTH, P95_BUDGET_MS, type EngineHealth } from "./mock_admin_data";
import { AdminHead, AdminPanel, AdminScope, RedactedNote, healthStateClass } from "./admin_ui";
import { Note } from "./surface_ui";

const HEALTH_TONE: Record<EngineHealth, string> = {
  healthy: "is-positive",
  degraded: "is-accent",
  unhealthy: "is-negative",
  offline: "is-dim",
};

export default function AdminSystemSurface() {
  const engines = MOCK_HEALTH.engines;

  return (
    <section className="admin-surface" aria-label="Admin system status">
      <AdminHead
        title="System"
        subtitle="Runtime health for every engine, latency against the enforced p95 budgets, and the environment this build is running in. Read-only: nothing here restarts or reconfigures an engine."
        badge={`env ${MOCK_ENVIRONMENT} · snapshot ${MOCK_HEALTH.status} · v${ADMIN_LINEAGE.version} · read-only`}
      />
      <AdminScope scope="global" />

      <AdminPanel title="Health roll-up">
        <Note>
          Worst-of across {engines.length} engines. An engine reporting <code>offline</code> is
          counted as <code>degraded</code> in this roll-up — that is the aggregation rule, not a
          display choice.
        </Note>
        <table className="data-table">
          <thead>
            <tr>
              <th>engine</th>
              <th>status</th>
              <th>latency</th>
              <th>version</th>
              <th>details</th>
            </tr>
          </thead>
          <tbody>
            {engines.map((entry) => (
              <tr key={entry.engine}>
                <td className="mono">{entry.engine}</td>
                <td>
                  <span className={healthStateClass(entry.status)}>{entry.status}</span>
                </td>
                <td className="mono">
                  {entry.latency_ms === null ? "—" : `${entry.latency_ms} ms`}
                </td>
                <td className="mono">{entry.version}</td>
                <td className="muted">{Object.entries(entry.details).map(([k, v]) => `${k}=${String(v)}`).join(" · ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </AdminPanel>

      <AdminPanel title="Latency against the p95 budgets">
        <p className="muted">
          Budgets are enforced in the guardrails. An engine over budget degrades rather than
          silently continuing.
        </p>
        <table className="data-table">
          <thead>
            <tr>
              <th>hop</th>
              <th>budget</th>
              <th>fail action</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(P95_BUDGET_MS).map(([hop, budget]) => (
              <tr key={hop}>
                <td>{hop}</td>
                <td className="mono">&lt;{budget} ms</td>
                <td className="muted">
                  {hop === "risk" ? "reject if exceeded (safer)" : "alert + degrade"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </AdminPanel>

      <AdminPanel title="Environment">
        <p className="muted">
          Environments are a closed set. A production environment is not selectable from this
          screen — switching is a server-side operation, not a browser one.
        </p>
        <p className="admin-token-row">
          {(["development", "testing", "paper", "canary", "production"] as const).map((name) => (
            <span className={name === MOCK_ENVIRONMENT ? "state-chip is-filled" : "state-chip is-unknown"} key={name}>
              {name}
            </span>
          ))}
        </p>
        <Note>
          Fail-fast applies: an unrecognised environment is refused at startup rather than
          defaulted.
        </Note>
        <RedactedNote />
      </AdminPanel>
    </section>
  );
}

// Kept for the tone map's type safety; unused values are intentional documentation.
export const HEALTH_TONE_KEYS = Object.keys(HEALTH_TONE) as EngineHealth[];
