// Settings surface: the two-gate account model (shared/core/account_mode.py:3-4),
// three-layer limit overrides (System_Development_Manual.md:108-130), account slot
// references only (shared/core/config.py:55), and display density.
// No secret value is ever displayed — not for Trader, not for Admin.
import { useState } from "react";
import { AlertTriangle, CircleSlash, Lock, ShieldCheck } from "lucide-react";
import { MOCK_ACCOUNT, MOCK_ACCOUNT_SLOTS, MOCK_KILL, MOCK_LIMITS, MOCK_OVERRIDES, type LimitOverride } from "./mock_trader_data";
import { Note, SurfaceHead, SurfacePanel } from "./surface_ui";

const DENSITIES = ["L1", "L2", "L3"] as const;

export default function SettingsSurface() {
  const [density, setDensity] = useState<(typeof DENSITIES)[number]>("L2");
  const [overrides, setOverrides] = useState<LimitOverride[]>(MOCK_OVERRIDES);

  const reset = (key: LimitOverride["key"]) =>
    setOverrides((current) => current.map((entry) => (entry.key === key ? { ...entry, override: null } : entry)));

  return (
    <section className="trader-surface" aria-label="Settings">
      <SurfaceHead
        title="Settings"
        subtitle="Own effective configuration for this tenant. Global configuration is Admin-only and version-controlled."
        badge="own effective config · no secret values are ever displayed"
      />

      <SurfacePanel title="Execution gates">
        <Note>
          MODE chooses the ledger and credentials. AUTO decides whether the bot may call execution. LIVE + OFF is
          armed but paused — neither gate alone authorises a trade.
        </Note>
        <div className="gate-row">
          <div className="gate">
            <span className="gate-label">MODE</span>
            <span className="gate-value mono">{MOCK_ACCOUNT.mode}</span>
            <span className="gate-source mono">source {MOCK_ACCOUNT.source}</span>
          </div>
          <div className="gate">
            <span className="gate-label">AUTO</span>
            <span className="gate-value mono">{MOCK_ACCOUNT.auto}</span>
            <span className="gate-source mono">source {MOCK_ACCOUNT.auto_source}</span>
          </div>
          <div className="gate">
            <span className="gate-label">live armed</span>
            <span className={MOCK_ACCOUNT.live_armed ? "is-positive" : "is-negative"}>
              {MOCK_ACCOUNT.live_armed ? "yes" : "no"}
            </span>
            <span className="gate-source">hard gate satisfied now</span>
          </div>
          <div className="gate">
            <span className="gate-label">bot will trade</span>
            <span className={MOCK_ACCOUNT.bot_will_trade ? "is-negative" : "is-positive"}>
              {MOCK_ACCOUNT.bot_will_trade ? "yes" : "no"}
            </span>
            <span className="gate-source">mode live and auto on</span>
          </div>
        </div>
        <Note>
          <Lock size={12} /> Switching mode and toggling AUTO are the only write actions here, and both require an
          explicit confirmation with a reason. This build sends neither.
        </Note>
      </SurfacePanel>

      <SurfacePanel title="Risk limits — three layers">
        <Note>
          Automatic value → your override → reset to default. A hard boundary can never be overridden; changing one
          needs a code change and a bible amendment.
        </Note>
        <table className="data-table">
          <thead>
            <tr>
              <th>limit</th>
              <th>Automatic</th>
              <th>Your Override</th>
              <th>hard boundary</th>
              <th>effective</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {overrides.map((entry) => (
              <tr key={entry.key}>
                <td className="mono">{entry.key}</td>
                <td className="mono">{entry.automatic}</td>
                <td className="mono">{entry.override ?? "—"}</td>
                <td className="mono">{entry.boundary ?? "—"}</td>
                <td className="mono is-positive">{entry.override ?? entry.automatic}</td>
                <td>
                  <button className="mini-button" type="button" disabled={entry.override === null} onClick={() => reset(entry.key)}>
                    Reset to Default
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <Note>
          <ShieldCheck size={12} /> Defaults: max_exposure_per_symbol {MOCK_LIMITS.max_exposure_per_symbol}, max_leverage{" "}
          {MOCK_LIMITS.max_leverage}, min_free_margin {MOCK_LIMITS.min_free_margin}, max_drawdown {MOCK_LIMITS.max_drawdown}.
        </Note>
      </SurfacePanel>

      <SurfacePanel title="Broker accounts">
        <table className="data-table">
          <thead>
            <tr>
              <th>broker</th>
              <th>mode</th>
              <th>enabled</th>
              <th>account reference</th>
            </tr>
          </thead>
          <tbody>
            {MOCK_ACCOUNT_SLOTS.map((slot) => (
              <tr key={slot.broker}>
                <td className="mono">{slot.broker}</td>
                <td className="mono">{slot.mode}</td>
                <td className={slot.enabled ? "is-positive" : "muted"}>{slot.enabled ? "yes" : "no"}</td>
                <td className="mono muted">{slot.account_id_env}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Note>
          Only references are shown — an env or vault name, never a credential value. Broker selection is validated
          server-side.
        </Note>
      </SurfacePanel>

      <SurfacePanel title="Kill switch">
        <div className="kill-row">
          <span className={MOCK_KILL.active ? "state-chip is-rejected" : "state-chip is-filled"}>
            {MOCK_KILL.active ? "ACTIVE" : "inactive"}
          </span>
          <span className="muted">scope {MOCK_KILL.scope}</span>
          <span className="muted read-only-note">read-only for Trader · full control is Admin-only</span>
        </div>
        <Note>
          <AlertTriangle size={12} /> A daily-loss breach halts trading and never auto-resumes: resume is an
          explicit human action with a reason, validated by the backend.
        </Note>
        <Note>
          <CircleSlash size={12} /> If the kill state cannot be read, this view assumes the safer state rather than
          implying trading is available.
        </Note>
      </SurfacePanel>

      <SurfacePanel title="Display">
        <div className="density-row" role="group" aria-label="Density">
          {DENSITIES.map((level) => (
            <button
              key={level}
              className={density === level ? "mode-button is-active" : "mode-button"}
              type="button"
              aria-pressed={density === level}
              onClick={() => setDensity(level)}
            >
              {level}
            </button>
          ))}
          <span className="muted">
            {density === "L1" ? "glance" : density === "L2" ? "investigate" : "inspect"} — density is part of the saved
            workspace
          </span>
        </div>
      </SurfacePanel>
    </section>
  );
}
