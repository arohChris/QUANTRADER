// Admin — Configuration. Status of every config file the platform reads, and the rule
// that governs editing them.
//
//   feature flags -> shared/core/feature_flags.py:30-71
//   environments   -> shared/core/environment.py:17-19
//   accounts       -> shared/core/config.py:54-75
//
// TWO THINGS ARE TRUE HERE AND BOTH MATTER:
//   1. Direct browser YAML editing is not permitted (integration proposal:778). Editing
//      is versioned and audited, server-side. This surface shows redacted status only.
//   2. There is NO config_versions contract — a repo-wide search finds no such table. So
//      there is no version history to show, and none is invented.
import { CONFIG_FILES, MOCK_ENVIRONMENT, MOCK_FEATURE_FLAGS } from "./mock_admin_data";
import { AdminHead, AdminPanel, AdminScope, NoContractNote, RedactedNote } from "./admin_ui";
import { Note } from "./surface_ui";

export default function AdminConfigurationSurface() {
  return (
    <section className="admin-surface" aria-label="Admin configuration">
      <AdminHead
        title="Configuration"
        subtitle="Every configuration file the platform reads, and what each one is allowed to reveal. Status only — this screen cannot change a setting."
        badge={`env ${MOCK_ENVIRONMENT} · redacted · no browser editing`}
      />
      <AdminScope scope="global" />

      <AdminPanel title="Config files">
        <NoContractNote what="config_versions" needs="a versioned, audited configuration store" />
        <table className="data-table">
          <thead>
            <tr>
              <th>file</th>
              <th>what it may show</th>
            </tr>
          </thead>
          <tbody>
            {CONFIG_FILES.map((file) => (
              <tr key={file.path}>
                <td className="mono">{file.path}</td>
                <td className="muted">{file.shows}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Note>
          Editing is a versioned, audited operation. A browser YAML editor is explicitly not
          permitted, because a change with no version and no record is indistinguishable from a
          mistake.
        </Note>
        <RedactedNote />
      </AdminPanel>

      <AdminPanel title="Feature flags">
        <p className="muted">
          Flags default to off. A new flag is inert until someone deliberately enables it, so
          adding a flag can never change behaviour by accident.
        </p>
        <table className="data-table">
          <thead>
            <tr>
              <th>flag</th>
              <th>state</th>
              <th>note</th>
            </tr>
          </thead>
          <tbody>
            {MOCK_FEATURE_FLAGS.map((flag) => (
              <tr key={flag.name}>
                <td className="mono">{flag.name}</td>
                <td>
                  <span className={flag.enabled ? "state-chip is-filled" : "state-chip is-unknown"}>
                    {flag.enabled ? "on" : "off"}
                  </span>
                </td>
                <td className="muted">{flag.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <Note>
          Flags reload on a signal and log a key-by-key diff, so a flag change leaves the same kind
          of trace an audit record would.
        </Note>
      </AdminPanel>

      <AdminPanel title="Precedence">
        <ol className="admin-flow">
          <li>
            <span className="admin-flow-index">1</span>Automatic default
          </li>
          <li>
            <span className="admin-flow-index">2</span>Your override
          </li>
          <li>
            <span className="admin-flow-index">3</span>Reset to default
          </li>
        </ol>
        <p className="admin-warn">
          Hard safety boundaries sit outside this chain. No override, at any layer, can raise a
          limit past its hard ceiling.
        </p>
      </AdminPanel>
    </section>
  );
}
