// Admin — Trading. The global trading lever: who may enable a strategy, and what an
// Admin can see across authorized tenants.
//
//   role / permission strings -> shared/core/permissions.py:13-43
//
// NO STRATEGY CONTRACT EXISTS. `shared/contracts/` has no Strategy or strategy-version
// type, and there is no strategy list endpoint. So this surface does not render an
// invented portfolio of strategies. It shows the permission model that governs the
// global lever, and the per-tenant view boundary, and names what is missing.
import { CircleSlash, Globe } from "lucide-react";
import { ADMIN_LINEAGE, ROLE_PERMISSION_MATRIX } from "./mock_admin_data";
import { AdminHead, AdminPanel, AdminScope, NoContractNote, RedactedNote } from "./admin_ui";
import { Note } from "./surface_ui";

const TRADING_BOUNDARIES = [
  "Global strategy control is Admin-only. A Trader may enable a strategy only where explicitly approved.",
  "Calculations stay broker-agnostic; only broker configuration and adapters differ per broker.",
  "Every order attempt, risk decision and state transition is logged immutably with its correlation id.",
  "The first deployment of any strategy runs dry-run: orders are logged, never sent.",
] as const;

export default function AdminTradingSurface() {
  return (
    <section className="admin-surface" aria-label="Admin trading">
      <AdminHead
        title="Trading"
        subtitle="The global lever over strategies and tenants, and the permission model behind it. Read-only: enabling a strategy is a server-side, audited action."
        badge={`global · v${ADMIN_LINEAGE.version} · read-only`}
      />
      <AdminScope scope="all authorized tenants and brokers" />

      <AdminPanel title="What Admin controls here">
        <ul className="admin-list">
          {TRADING_BOUNDARIES.map((rule) => (
            <li key={rule}>
              <Globe size={13} />
              {rule}
            </li>
          ))}
        </ul>
      </AdminPanel>

      <AdminPanel title="Strategies">
        <NoContractNote what="A strategy list" needs="a Strategy contract and a versioned strategy registry" />
        <p className="admin-empty">
          No strategies are registered. That is expected at this gate: nothing is allowed to trade
          until one strategy has a proven out-of-sample expectancy after fees.
        </p>
        <Note>
          <CircleSlash size={12} /> Strategy control stays closed until that number exists. The
          gate is the feature.
        </Note>
      </AdminPanel>

      <AdminPanel title="Who may do what">
        <table className="data-table">
          <thead>
            <tr>
              <th>role</th>
              <th>permissions</th>
              <th>source</th>
            </tr>
          </thead>
          <tbody>
            {ROLE_PERMISSION_MATRIX.map((row) => (
              <tr key={row.role}>
                <td>{row.role}</td>
                <td>
                  <span className="admin-token-row">
                    {row.permissions.map((permission) => (
                      <span className="state-chip is-unknown" key={permission}>
                        {permission}
                      </span>
                    ))}
                  </span>
                </td>
                <td className="mono muted">{row.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Note>
          Admin holds the wildcard, which is why the kill action resolves for it. A hidden button
          is not a control: the backend decides, every time.
        </Note>
        <RedactedNote />
      </AdminPanel>
    </section>
  );
}
