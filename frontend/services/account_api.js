// Account REST — only talks to M10.2. Absolute base (file:// has no origin API).
// Consumers: features/account_mode/main.js
const API_BASE = "http://127.0.0.1:8000";

export const STATUS_URL = `${API_BASE}/api/v1/account/status`;
export const MODE_URL = `${API_BASE}/api/v1/account/mode`;
export const AUTO_URL = `${API_BASE}/api/v1/account/auto-trade`;

/** GET account status. Non-2xx → throw-like result, never silent success. */
export async function fetchStatus() {
  const r = await fetch(STATUS_URL);
  if (!r.ok) {
    return { ok: false, status: r.status, detail: `HTTP ${r.status} on GET status` };
  }
  const body = await r.json();
  return { ok: true, status: r.status, body };
}

/**
 * POST account command with X-Role.
 * frontend_spec: never treat non-2xx as success.
 */
export async function postAccount(url, payload, role) {
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Role": role,
      },
      body: JSON.stringify(payload),
    });
    let body = null;
    try {
      body = await r.json();
    } catch {
      /* non-JSON */
    }
    if (!r.ok) {
      const detail = (body && body.detail) || `HTTP ${r.status}`;
      return { ok: false, status: r.status, detail, body };
    }
    return { ok: true, status: r.status, body };
  } catch (e) {
    return { ok: false, status: 0, detail: String(e), body: null };
  }
}
