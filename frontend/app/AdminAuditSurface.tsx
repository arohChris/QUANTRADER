// Admin — Audit. Read-only activity, and an honest account of the fact that the audit
// record does not exist yet.
//
// NO BACKEND CONTRACT. A repo-wide search of financial_analyst_app/**/*.py finds no
// AuditLog, no audit contract, and no append-only audit table. What exists is the
// requirement: AGENTS.md §3.12 (every order attempt, every risk decision with its exact
// numbers, every state transition, correlated) and M12 ("who changed what, old→new, when,
// why — append-only").
//
// So this surface renders the REQUIRED SHAPE and an explicitly empty record set. It does
// not render sample rows: a fabricated audit entry is indistinguishable from a real one,
// and an audit trail that can be invented is worse than no audit trail at all.
import { ADMIN_LINEAGE, MOCK_AUDIT_RECORDS } from "./mock_admin_data";
import { AdminHead, AdminPanel, AdminScope, NoContractNote, RedactedNote } from "./admin_ui";
import { Note } from "./surface_ui";

const REQUIRED_FIELDS = [
  { field: "correlation_id", rule: "one id traces a trade end to end: tick → signal → risk → order → fill → ledger" },
  { field: "actor", rule: "who or what acted — a human role or a service" },
  { field: "action", rule: "what changed" },
  { field: "target", rule: "what it changed, scoped to tenant and broker" },
  { field: "before → after", rule: "the old and new value, both present" },
  { field: "at", rule: "when, in UTC" },
] as const;

export default function AdminAuditSurface() {
  return (
    <section className="admin-surface" aria-label="Admin audit">
      <AdminHead
        title="Audit"
        subtitle="Who changed what, old value to new value, when, and why — append-only. This is the record that has to make a decision reconstructable without asking anyone."
        badge={`append-only · ${MOCK_AUDIT_RECORDS.length} records · correlation ${ADMIN_LINEAGE.correlation_id.slice(0, 8)}…`}
      />
      <AdminScope scope="global" />

      <AdminPanel title="Records">
        <NoContractNote what="The audit log" needs="an append-only store exposed as a read endpoint" />
        {MOCK_AUDIT_RECORDS.length === 0 ? (
          <p className="admin-empty">No records. Nothing has been written, and nothing is being invented to fill this space.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>at</th>
                <th>actor</th>
                <th>action</th>
                <th>target</th>
                <th>before → after</th>
              </tr>
            </thead>
            <tbody>
              {MOCK_AUDIT_RECORDS.map((record) => (
                <tr key={record.correlation_id}>
                  <td className="mono">{record.at}</td>
                  <td>{record.actor}</td>
                  <td>{record.action}</td>
                  <td className="mono">{record.target}</td>
                  <td className="mono">
                    {record.before} → {record.after}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Note>
          Append-only means no update and no delete. A correction is a new record that references
          the one it corrects.
        </Note>
      </AdminPanel>

      <AdminPanel title="What every record must carry">
        <table className="data-table">
          <thead>
            <tr>
              <th>field</th>
              <th>rule</th>
            </tr>
          </thead>
          <tbody>
            {REQUIRED_FIELDS.map((entry) => (
              <tr key={entry.field}>
                <td className="mono">{entry.field}</td>
                <td className="muted">{entry.rule}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Note>
          The correlation id is the load-bearing one: it is what makes{" "}
          <code>grep correlation_id=…</code> return the whole chain instead of a fragment.
        </Note>
        <RedactedNote />
      </AdminPanel>
    </section>
  );
}
