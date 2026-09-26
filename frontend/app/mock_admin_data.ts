// WHY: mock admin data for the Admin workspace surfaces.
// Every field name below is copied from the FROZEN backend contracts so these surfaces
// can be wired to the real endpoints without a redesign. The VALUES are mock.
//
//   RiskLimits / SystemConfig -> financial_analyst_app/shared/core/config.py:34-52
//   AccountsConfig            -> shared/core/config.py:71
//   RiskDecision              -> shared/contracts/risk.py:13-31
//   HealthStatus              -> shared/core/engine_interface.py:25-36
//   HealthSnapshot            -> shared/core/health.py:21-27
//   EngineHealth / EngineState-> shared/core/health.py:14-18, engine_interface.py:39-47
//   Lifecycle.health()        -> shared/core/lifecycle.py:59-82
//   Environment               -> shared/core/environment.py:17-19
//   feature flags             -> shared/core/feature_flags.py:30-71
//   BaseContract lineage      -> shared/contracts/base.py:51-68
//   role / permission strings -> shared/core/permissions.py:13-43
//
// THREE INVENTIONS, CLEARLY MARKED — no backend contract exists for any of them:
//   INVENTED  kill state          — only the action string "risk.kill_switch" exists
//             (shared/core/permissions.py:48). There is no KillState contract.
//   INVENTED  audit records      — repo-wide search of financial_analyst_app/**/*.py
//             finds no audit contract, no AuditLog, no config_versions table.
//   INVENTED  configuration diffs — config/*.yaml is read by
//             shared/core/feature_flags.py and shared/core/config.py, but no versioned
//             snapshot of it is exposed as a contract.
// Anything marked INVENTED below is a frontend shape waiting for its backend, and the
// surface that renders it says so on screen. None of it is presented as real.
//
// Rules honoured here (not optional):
//   * Money is float in these contracts (Candle/PortfolioState are float8 by design —
//     warm.py:18-19), so `min_free_margin` is a plain number here, never a Decimal.
//   * `max_exposure_per_symbol` is a FRACTION (0.1 = 10%), bounded 0..1 by config.py:35.
//   * `sizing_method` is a free string in risk.py:27 (vol_target | fixed_fractional |
//     risk_budget) — NOT a Literal, so it must not be typed as a union here.
//   * `HealthSnapshot.status` has THREE values and no `offline` (health.py:25), while
//     `HealthStatus.status` has FOUR (engine_interface.py:31). They are kept apart
//     below because Lifecycle downgrades `offline` to `degraded` (lifecycle.py:80-81)
//     and flattening them would show a state the aggregator can never emit.

export type RiskLimitsShape = {
  max_exposure_per_symbol: number;
  max_leverage: number;
  min_free_margin: number;
  max_drawdown: number;
};

export type SystemConfigShape = {
  base_currency: string;
  clock: string;
  log_level: string;
};

// shared/core/config.py:34-52 — ge/le bounds are the contract, mirrored here.
export const MOCK_RISK_LIMITS: RiskLimitsShape = {
  max_exposure_per_symbol: 0.1,
  max_leverage: 10,
  min_free_margin: 100,
  max_drawdown: 0.2,
};

export const MOCK_SYSTEM_CONFIG: SystemConfigShape = {
  base_currency: "USD",
  clock: "utc",
  log_level: "INFO",
};

// health.py:14-18 — four values.
export type EngineHealth = "healthy" | "degraded" | "unhealthy" | "offline";
export const ENGINE_HEALTH_VALUES: EngineHealth[] = ["healthy", "degraded", "unhealthy", "offline"];

// health.py:25 — three values, deliberately NOT the four above.
export const HEALTH_SNAPSHOT_STATUS = ["healthy", "degraded", "unhealthy"] as const;
export type HealthSnapshotStatus = (typeof HEALTH_SNAPSHOT_STATUS)[number];

// engine_interface.py:39-47
export const ENGINE_STATE_VALUES = [
  "initializing",
  "healthy",
  "degraded",
  "unhealthy",
  "shutting_down",
  "offline",
] as const;

// engine_interface.py:30-36
export type HealthStatus = {
  engine: string;
  status: EngineHealth;
  latency_ms: number | null;
  details: Record<string, unknown>;
  version: string;
};

// health.py:26 — modules is an open dict; Lifecycle fills it per engine (lifecycle.py:75).
export type HealthSnapshot = {
  status: HealthSnapshotStatus;
  modules: Record<string, unknown>;
};

// AGENTS.md §4.1 p95 budgets, per hop.
export const P95_BUDGET_MS = {
  "tick → parquet": 50,
  analytics: 20,
  risk: 10,
  "order → ack": 500,
  "tick broadcast to UI": 20,
} as const;

export const MOCK_HEALTH: HealthSnapshot & { engines: HealthStatus[] } = {
  // Worst-of roll-up, mirroring lifecycle.py:72-81.
  status: "degraded",
  modules: { count: 6 },
  engines: [
    { engine: "m2_market_data", status: "healthy", latency_ms: 12, details: { ws_connected: true, lag_ms: 12 }, version: "1.0.0" },
    { engine: "m3_analytics", status: "healthy", latency_ms: 8, details: { features_resolved: 0 }, version: "1.0.0" },
    // Degraded, not unhealthy: m4 has no strategies armed yet at this gate.
    { engine: "m4_alpha", status: "degraded", latency_ms: 0, details: { strategies_armed: 0, reason: "gate G0 — no validated edge yet" }, version: "1.0.0" },
    { engine: "m5_risk", status: "healthy", latency_ms: 4, details: { decisions_per_min: 0 }, version: "1.0.0" },
    { engine: "m6_execution", status: "offline", latency_ms: null, details: { reason: "no broker connection in this build" }, version: "1.0.0" },
    { engine: "m7_ledger", status: "healthy", latency_ms: 6, details: { postings: 0 }, version: "1.0.0" },
  ],
};

// environment.py:17-19
export const ENVIRONMENT_VALUES = ["development", "testing", "paper", "canary", "production"] as const;
export type EnvironmentName = (typeof ENVIRONMENT_VALUES)[number];
export const MOCK_ENVIRONMENT: EnvironmentName = "development";

// risk.py:21-30 — anatomy only. A real RiskDecision arrives per signal; none exist at G0.
export type RiskDecisionShape = {
  decision_id: string;
  signal_id: string | null;
  trade_plan_id: string | null;
  approved: boolean;
  approved_qty: number | null;
  rejected_reason: string | null;
  sizing_method: string;
  limits_checked: Record<string, boolean>;
  rejected_details: Record<string, unknown> | null;
  latency_budget_ms: number | null;
};

// risk.py:26 — rejected_reason carries an ErrorCode, e.g. insufficient_margin.
export const RISK_REJECTION_CODES = [
  "insufficient_margin",
  "max_exposure_exceeded",
  "max_leverage_exceeded",
  "daily_loss_limit",
  "kill_switch_active",
] as const;

export const MOCK_RISK_DECISION: RiskDecisionShape = {
  decision_id: "00000000-0000-4000-8000-000000000001",
  signal_id: null,
  trade_plan_id: null,
  approved: false,
  approved_qty: null,
  rejected_reason: null,
  sizing_method: "vol_target",
  limits_checked: {
    max_exposure_per_symbol: true,
    max_leverage: true,
    min_free_margin: true,
    max_drawdown: true,
  },
  rejected_details: null,
  latency_budget_ms: 10,
};

// permissions.py:13-43 — the backend literals are lowercase; the UI capitalises them.
export const ROLE_PERMISSION_MATRIX: { role: string; permissions: string[]; source: string }[] = [
  { role: "Viewer", permissions: ["portfolio.read", "market_data.read", "analytics.read", "health.read"], source: "permissions.py:18-23" },
  { role: "Trader", permissions: ["risk.preview", "risk.read", "order.preview", "order.read", "trade.create", "trade.confirm", "account.switch", "trading.enable"], source: "permissions.py:24-39" },
  // admin is the "*" wildcard (permissions.py:40-42) — expanded here for display only.
  { role: "Admin", permissions: ["*", "risk.kill_switch", "strategy.enable", "config.write", "audit.read"], source: "permissions.py:40-42, :48" },
];

// feature_flags.py:51-54 — default False, "new flags are off until explicitly enabled".
export const MOCK_FEATURE_FLAGS: { name: string; enabled: boolean; note: string }[] = [
  { name: "enable_stat_arb", enabled: false, note: "capability UNAVAILABLE on Deriv — needs an external feed" },
];

// proposal:607-616 — the config files an Admin Configuration surface must list. Status
// only: values are redacted and nothing here is editable in the browser.
export const CONFIG_FILES: { path: string; shows: string }[] = [
  { path: "config/accounts.yaml", shows: "redacted account status and arming state" },
  { path: "config/brokers/*.yaml", shows: "broker name, configured state, health, capability summary" },
  { path: "config/assets/*.yaml", shows: "instruments, display metadata, effective specs" },
  { path: "config/capabilities.yaml", shows: "capability badges and reasons" },
  { path: "config/strategies/*.yaml", shows: "strategy status and version metadata" },
  { path: "config/risk/limits.yaml", shows: "redacted limits and status" },
  { path: "config/environments/*.yaml", shows: "environment name and health" },
  { path: "config/system/*.yaml", shows: "redacted runtime/system state" },
  { path: "config/system/feature_flags.yaml", shows: "feature availability" },
  { path: "config/reference/snapshot.yaml", shows: "reference_snapshot_id and effective lineage" },
];

// INVENTED — no backend contract. Only "risk.kill_switch" exists (permissions.py:48).
export const MOCK_KILL_STATE: { active: boolean; scope: string; reason: string | null; action: string } = {
  active: false,
  scope: "global",
  reason: null,
  action: "risk.kill_switch",
};

// INVENTED — no backend contract, no AuditLog, no config_versions anywhere in the repo.
export const MOCK_AUDIT_RECORDS: {
  correlation_id: string;
  actor: string;
  action: string;
  target: string;
  before: string;
  after: string;
  at: string;
}[] = [];

// BaseContract lineage (base.py:53-65) — every admin panel is stamped with this.
export const ADMIN_LINEAGE = {
  version: "1.0",
  correlation_id: "00000000-0000-4000-8000-0000000000ad",
  trace_id: "00000000-0000-4000-8000-0000000000ae",
  timestamp: "2026-09-26T00:00:00Z",
};
