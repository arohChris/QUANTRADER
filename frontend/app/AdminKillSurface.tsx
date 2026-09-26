// Admin — Kill / Resume. The most dangerous surface in the app, so it is the most
// conservative one.
//
// NO BACKEND CONTRACT EXISTS for any of this: a repo-wide search of
// financial_analyst_app/**/*.py finds no KillState, no kill-switch table, and no resume
// endpoint. The only kill reference in the backend is the permission action string
// "risk.kill_switch" (shared/core/permissions.py:48). The state below is therefore
// INVENTED — a shape waiting for its backend — and the panel says so on screen rather
// than implying a live switch is being read.
//
// The flow follows the documented sequence (integration proposal:743-762):
//   Kill   → confirmation → backend kill request → current state → affected scope → audit
//   Resume → explicit human action → reason → backend validation → result
// and "Resume must never auto-resume after a daily-loss breach" (proposal:762, AGENTS.md
// §3.3). This slice is read-only: it can describe that flow but cannot halt or resume
// anything, because there is no endpoint to call.
import { Ban, ShieldAlert } from "lucide-react";
import { MOCK_KILL_STATE } from "./mock_admin_data";
import { AdminHead, AdminPanel, AdminScope, NoContractNote, RedactedNote } from "./admin_ui";
import { Note } from "./surface_ui";

const KILL_STEPS = [
  "Confirm",
  "Backend kill request",
  "Current kill state",
  "Affected scopes",
  "Audit record",
] as const;

const RESUME_STEPS = ["Explicit human action", "Reason", "Backend validation", "Result"] as const;

export default function AdminKillSurface() {
  return (
    <section className="admin-surface" aria-label="Admin kill and resume">
      <AdminHead
        title="Kill / Resume"
        subtitle="Halting trading is a structural action, not a setting. It is deliberately the slowest thing in this app to trigger and the hardest to undo."
        badge={`action ${MOCK_KILL_STATE.action} · ${MOCK_KILL_STATE.active ? "ACTIVE" : "not active"} · read-only`}
      />
      <AdminScope scope={MOCK_KILL_STATE.scope} />

      <AdminPanel title="Current state">
        <NoContractNote what="Kill switch state" needs="a kill/resume endpoint and an auditable state record" />
        <dl className="kv-grid">
          <div>
            <dt>active</dt>
            <dd className={MOCK_KILL_STATE.active ? "is-negative" : "is-positive"}>
              {MOCK_KILL_STATE.active ? "yes — trading halted" : "no"}
            </dd>
          </div>
          <div>
            <dt>scope</dt>
            <dd>{MOCK_KILL_STATE.scope}</dd>
          </div>
          <div>
            <dt>reason</dt>
            <dd>{MOCK_KILL_STATE.reason ?? "—"}</dd>
          </div>
          <div>
            <dt>action</dt>
            <dd className="mono">{MOCK_KILL_STATE.action}</dd>
          </div>
        </dl>
        <Note>
          <ShieldAlert size={12} /> If the kill state cannot be read, this view assumes the safer
          state rather than implying trading is available. An unreadable switch is treated as
          engaged.
        </Note>
      </AdminPanel>

      <AdminPanel title="Kill — required sequence">
        <ol className="admin-flow">
          {KILL_STEPS.map((step, index) => (
            <li key={step}>
              <span className="admin-flow-index">{index + 1}</span>
              {step}
            </li>
          ))}
        </ol>
        <button className="admin-danger" type="button" disabled>
          <Ban size={14} />
          Kill trading — unavailable in this build
        </button>
        <Note>
          The control is disabled and cannot be enabled from the browser. A kill must be a
          deliberate server-side action that leaves an audit record, never a button that a
          mis-click can fire.
        </Note>
      </AdminPanel>

      <AdminPanel title="Resume — required sequence">
        <ol className="admin-flow">
          {RESUME_STEPS.map((step, index) => (
            <li key={step}>
              <span className="admin-flow-index">{index + 1}</span>
              {step}
            </li>
          ))}
        </ol>
        <p className="admin-warn">
          Resume never auto-resumes. After a daily-loss breach, trading stays halted until a human
          explicitly resumes with a reason recorded.
        </p>
      </AdminPanel>

      <AdminPanel title="Independence">
        <p className="muted">
          The kill switch must keep working when the things most likely to fail are already down.
          It does not depend on analytics or on the market-data feed being healthy, and it is
          reachable directly rather than through a chain of services.
        </p>
        <RedactedNote />
      </AdminPanel>
    </section>
  );
}
