// Shared shell for the Admin workspace surfaces.
//
// WHY a separate mapper: surface_ui.stateClass is typed to OrderState, so it cannot
// classify a health state. Reusing it would be a type lie, and silently mapping
// `offline` onto an order class would show an Admin a state Lifecycle never emits
// (lifecycle.py:80-81 downgrades offline to degraded in the roll-up).
import type { ReactNode } from "react";
import { Info, Lock, ShieldCheck } from "lucide-react";
import type { EngineHealth } from "./mock_admin_data";
import { Note } from "./surface_ui";

export function healthStateClass(status: EngineHealth): string {
  switch (status) {
    case "healthy":
      return "state-chip is-healthy";
    case "degraded":
      return "state-chip is-degraded";
    case "unhealthy":
      return "state-chip is-unhealthy";
    default:
      return "state-chip is-offline";
  }
}

// Every Admin surface opens with the same two facts: what scope it is acting over, and
// that the backend is the authority on whether the action is allowed. proposal:123 says
// the UI must show the active tenant scope; :125 says Admin never receives secret values.
export function AdminScope({ scope, children }: { scope: string; children?: ReactNode }) {
  return (
    <p className="admin-scope">
      <ShieldCheck size={13} />
      <span>
        Scope <strong>{scope}</strong> · authorisation is decided by the backend, not by this screen
      </span>
      {children}
    </p>
  );
}

export function AdminHead({
  title,
  subtitle,
  badge,
}: {
  title: string;
  subtitle: ReactNode;
  badge: string;
}) {
  return (
    <header className="surface-head">
      <h1>{title}</h1>
      <p className="muted">{subtitle}</p>
      <p className="surface-badge">{badge}</p>
    </header>
  );
}

// The standing redaction + no-write statement, inherited by every Admin surface so it
// is stated once rather than retyped per panel.
export function RedactedNote({ children }: { children?: ReactNode }) {
  return (
    <Note>
      <Lock size={12} /> Values are redacted: broker tokens, database and Redis credentials,
      vault contents and private keys stay backend-only and are never sent to this screen.
      {children}
    </Note>
  );
}

// Used by the three surfaces with no backend contract. Says what is missing instead of
// rendering a plausible-looking row.
export function NoContractNote({ what, needs }: { what: string; needs: string }) {
  return (
    <Note>
      <Info size={12} />
      <span>
        <strong>{what}</strong> has no contract in the backend yet — a repo-wide search of{" "}
        <code>financial_analyst_app</code> finds none. It needs {needs}. Nothing is shown here
        because a fabricated row would be indistinguishable from a real one.
      </span>
    </Note>
  );
}

export function AdminPanel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="surface-panel admin-panel">
      <h2>{title}</h2>
      {children}
    </section>
  );
}
