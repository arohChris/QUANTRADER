// Admin — Trading. The global trading lever: who may enable a strategy, and what an
// Admin can see across authorized tenants.
//
//   role / permission strings -> shared/core/permissions.py:13-43
//   approval state machine    -> shared/core/strategy_approval_store.py (`_LEGAL`, frozen)
//
// The Strategies panel below is wired to the live M4.9 gateway; `services/approval_api.ts`
// is the only module in `app/` permitted to touch the network for it (the
// market_data_api.ts choke-point law). Everything rendered here is server truth:
// rows come from GET, a row is replaced only by the POST response that proved the
// write, and a store this screen cannot read renders no rows at all — never a guess
// (M10.4.4 honesty rule). The backend re-decides every click; a hidden button is not
// a control.
import { useCallback, useEffect, useRef, useState } from "react";
import { Globe, RefreshCw } from "lucide-react";
import { ADMIN_LINEAGE, ROLE_PERMISSION_MATRIX } from "./mock_admin_data";
import { AdminHead, AdminPanel, AdminScope, RedactedNote } from "./admin_ui";
import { Note } from "./surface_ui";
import type { AccessRole } from "./nav_config";
import { ApprovalApiError, listApprovals, setApprovalState } from "../services/approval_api";
import type { ApprovalRow, ApprovalState } from "../services/approval_api";

const TRADING_BOUNDARIES = [
  "Global strategy control is Admin-only. A Trader may enable a strategy only where explicitly approved.",
  "Calculations stay broker-agnostic; only broker configuration and adapters differ per broker.",
  "Every order attempt, risk decision and state transition is logged immutably with its correlation id.",
  "The first deployment of any strategy runs dry-run: orders are logged, never sent.",
] as const;

// WHY a mirrored constant rather than a fetched one: the legal targets must be known
// before any server answer arrives, because an illegal target must not exist in the
// DOM at all — not merely render disabled (spec m4.9 §3). This copy is pinned to the
// backend `_LEGAL` by the cross-tree parity test
// (test_legal_transitions_parity_with_backend_matrix_is_exact), the m10.4.10
// timeframe-parity precedent. Drift in either tree fails CI.
const LEGAL_TRANSITIONS: Record<ApprovalState, readonly ApprovalState[]> = {
  draft: ["testing", "rejected", "discontinued"],
  testing: ["draft", "approved", "rejected"],
  approved: ["testing", "rejected", "discontinued"],
  rejected: ["testing"],
  discontinued: ["testing", "approved"],
};

// WHY an enum, not a boolean: a 503 must be visible as `unavailable` and a 403 as
// `forbidden` — a `rows.length === 0` check alone would render both as the honest
// "empty registry" state, which would be a lie (dataState philosophy).
type PanelStatus = "loading" | "ready" | "empty" | "unavailable" | "forbidden";

const bannerUnavailableFirst = "approval store unavailable — no state shown";
const bannerUnavailableLater = "approval store unavailable — showing last confirmed states";

export default function AdminTradingSurface({ role }: { role: AccessRole }) {
  const [rows, setRows] = useState<ApprovalRow[]>([]);
  const [status, setStatus] = useState<PanelStatus>("loading");
  const [banner, setBanner] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  // WHY a ref and not state: it must outlive re-renders without changing `load`'s
  // identity — a setState here would re-fire the effect in an endless loop.
  const loadedRef = useRef(false);

  const load = useCallback(async (): Promise<void> => {
    setActionError(null);
    try {
      const next = await listApprovals(role);
      setRows(next);
      setStatus(next.length === 0 ? "empty" : "ready");
      setBanner(null);
    } catch (error) {
      if (error instanceof ApprovalApiError && error.code === "permission_denied") {
        setStatus("forbidden");
        setRows([]);
        setBanner("forbidden — permission denied: approval state is Admin-only");
        return;
      }
      if (loadedRef.current) {
        // Later failure (E11): the last server-confirmed rows stay on screen and the
        // banner declares them possibly stale. Nothing is cleared, nothing is guessed.
        setBanner(bannerUnavailableLater);
      } else {
        // First load failed (E2): zero rows. Fabricating a plausible row here would be
        // the M10.4.4 honesty violation, so the panel shows why instead.
        setRows([]);
        setStatus("unavailable");
        setBanner(bannerUnavailableFirst);
      }
      return;
    } finally {
      loadedRef.current = true;
    }
  }, [role]);

  useEffect(() => {
    void load();
  }, [load]);

  const transition = async (row: ApprovalRow, target: ApprovalState): Promise<void> => {
    const key = `${row.strategy_id}@${row.version}`;
    // Entering `approved` makes the strategy runnable by traders (runner gate), so
    // every transition is an explicit, confirmed action — never a silent click.
    const confirmed = window.confirm(
      `Move ${row.name} ${row.version} from ${row.state} to ${target}?` +
        (target === "approved" ? " An approved strategy becomes runnable by traders." : ""),
    );
    if (!confirmed) {
      return;
    }
    setActionError(null);
    setBusyKey(key);
    try {
      const updated = await setApprovalState(role, {
        strategy_id: row.strategy_id,
        version: row.version,
        new_state: target,
      });
      // Row replaced ONLY from the response body — the store's read-back proof.
      // No optimistic local mutation anywhere (spec m4.9 §3).
      setRows((current) =>
        current.map((item) =>
          item.strategy_id === updated.strategy_id && item.version === updated.version
            ? {
                ...item,
                state: updated.state,
                changed_by: updated.changed_by,
                changed_at: updated.changed_at,
                note: updated.note,
              }
            : item,
        ),
      );
    } catch (error) {
      if (error instanceof ApprovalApiError && error.retryable) {
        // 503: banner, row untouched — the last confirmed state stays visible.
        setBanner(bannerUnavailableLater);
      } else {
        // 409 (and any other typed failure): inline message, row untouched.
        setActionError(error instanceof Error ? error.message : "transition failed");
      }
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <section className="admin-surface" aria-label="Admin trading">
      <AdminHead
        title="Trading"
        subtitle="The global lever over strategies and tenants: approval state is server truth from the M4.8 store, and this screen offers only the transitions the frozen matrix allows. The backend decides, every time."
        badge={`global · v${ADMIN_LINEAGE.version} · approval control`}
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
        {banner !== null && (
          <p className="admin-warn" role="status">
            {banner}
          </p>
        )}
        {actionError !== null && (
          <p className="admin-warn" role="alert">
            {actionError}
          </p>
        )}
        <button
          type="button"
          className="admin-action"
          disabled={status === "loading" || busyKey !== null}
          onClick={() => void load()}
        >
          <RefreshCw size={12} /> Refresh
        </button>

        {status === "loading" && <p className="admin-empty">loading approval state…</p>}

        {status === "empty" && (
          <p className="admin-empty">
            No strategies are registered. That is expected at this gate: nothing is allowed to
            trade until one strategy has a proven out-of-sample expectancy after fees.
          </p>
        )}

        {status === "unavailable" && (
          <p className="admin-empty">
            No approval state is shown because the store could not be read. Nothing is guessed
            here — use Refresh to retry.
          </p>
        )}

        {status === "forbidden" && (
          <p className="admin-empty">
            Permission denied: the backend refused this role for approval state. Navigation
            hiding is a UX measure; the server is the actual gate.
          </p>
        )}

        {rows.length > 0 && (
          <table className="data-table">
            <thead>
              <tr>
                <th>strategy</th>
                <th>version</th>
                <th>state</th>
                <th>changed_by</th>
                <th>changed_at</th>
                <th>note</th>
                <th>transition</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const key = `${row.strategy_id}@${row.version}`;
                return (
                  <tr key={key}>
                    <td>{row.name}</td>
                    <td className="mono muted">{row.version}</td>
                    <td>
                      <span className={`state-chip is-${row.state}`}>{row.state}</span>
                    </td>
                    <td className="mono muted">{row.changed_by ?? "—"}</td>
                    <td className="mono muted">{row.changed_at ?? "—"}</td>
                    <td>{row.note ?? "—"}</td>
                    <td>
                      <span className="admin-token-row">
                        {LEGAL_TRANSITIONS[row.state].map((target) => (
                          <button
                            key={target}
                            type="button"
                            className="admin-action"
                            disabled={busyKey === key}
                            onClick={() => void transition(row, target)}
                          >
                            {target}
                          </button>
                        ))}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}

        <Note>
          State chips and transition buttons mirror the frozen matrix; entering{" "}
          <strong>approved</strong> makes a strategy runnable by traders, so every change is
          confirmed, server-stamped and logged with its correlation id.
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
