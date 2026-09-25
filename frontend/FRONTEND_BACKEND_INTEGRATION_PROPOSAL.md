# Frontend ↔ Backend Integration Proposal

> **Status:** Open for amendment. This is an implementation proposal, not a frozen specification.
>
> **Purpose:** Define the contract-first plan for connecting the QUANTRADER2 frontend to the backend safely, with explicit role boundaries, configuration ownership, data provenance, and backend integration requirements.
>
> **Scope:** `frontend/` as the delivery shell and `financial_analyst_app/` as the backend. The backend remains the authority for analytics, risk, PnL, execution, and account state.
>
> **Authority:** `AGENTS.md`, `BUILD_STATUS.md`, `USER_ACTIONS.md`, the approved manual sections, and frozen contracts win over this proposal. Any amendment that changes a frozen contract must go through the required change process and human sign-off.

---

## 1. Current Baseline

The current project is at **G0 — Edge Validation**. `BUILD_STATUS.md:6`

The current frontend is a static, dependency-free Day-1 account panel. The full M10.4 workstation is gated and has not been implemented. `frontend/README.md:12-28`

The current backend exposes only the account-gate API:

| Method | Path | Current status |
|---|---|---|
| GET | `/api/v1/account/status` | Implemented |
| GET | `/api/v1/account/mode` | Implemented alias |
| POST | `/api/v1/account/mode` | Implemented |
| POST | `/api/v1/account/auto-trade` | Implemented |

`financial_analyst_app/app/main.py:74-105`

The current backend does **not** yet expose production market-data, analytics, strategy, portfolio, risk, order, execution, ledger, health, command, or WebSocket APIs for the frontend.

The following are current repository facts:

- M3.0 Capability Registry is complete. `BUILD_STATUS.md:34`
- M3.1 Feature Engine is a draft specification awaiting ACTION 35 LGTM. `docs/specs/m3.1_feature_engine.spec.md:16`
- M2.9 HOT is a cache, not a frontend data authority. `docs/specs/m2.9_hot.spec.md:1-6`
- M2.10 WARM is deferred and has no tests or implementation. `BUILD_STATUS.md:33`
- M4, M5, M6, and M7 have not started. `BUILD_STATUS.md:38-42`
- The frontend must not query Redis, the Lake, TimescaleDB, or internal Python modules directly. ``frontend/specs/frontend_spec.md`:18-23`

---

## 2. Goals

1. Give users a safe institutional workstation over backend-owned state.
2. Keep all business calculations and execution decisions in the backend.
3. Make provenance, quality, staleness, and uncertainty visible.
4. Support read-only public market intelligence for viewers.
5. Support signed-in, tenant-scoped trading workflows for traders.
6. Give the owner/admin controlled configuration, audit, and emergency operations.
7. Use one typed contract path for REST, WebSocket, command, and future AI/news/backtest surfaces.
8. Keep the frontend amendable until the backend contracts are explicitly frozen.

---

## 3. Non-goals

The frontend will not:

- calculate RSI, EMA, MA, ATR, PnL, margin, exposure, sizing, or R:R;
- read Redis/Valkey, TimescaleDB, Parquet Lake, or broker credentials;
- decide whether a strategy is tradable;
- treat `MODE=LIVE` or `AUTO=ON` as complete execution authorization;
- create an executable order without the backend preview/risk/confirmation path;
- expose secrets or raw vault values;
- invent unavailable market data or fabricate missing candles;
- silently mix forming and closed analytics;
- trust a client-supplied role in production.

``frontend/specs/frontend_spec.md`:59-72` ``frontend/specs/frontend_spec.md`:182-204`

---

## 4. Role Model

The frontend uses three access classes:

| Access class | User state | Intended access |
|---|---|---|
| **Viewer** | Not signed in | Public/global market data, capabilities, public analytics, health, public research |
| **Trader** | Signed in | Own tenant portfolio, signals, trade plans, risk preview, order preview, manual trade preview/confirm, account controls, and approved-strategy arming |
| **Owner/Admin** | Signed in as owner | Highest application privilege: all tenant data, all trader operations, global strategy/risk/configuration, kill/resume, user/role administration, audit, and system controls |

The current RBAC basis is `viewer`, `trader`, and `admin`; the UI label `Owner/Admin` maps to the backend `admin` role. `financial_analyst_app/shared/core/permissions.py:13-43`

### 4.1 Production authentication requirement

The current `X-Role` header is a local demonstration mechanism only. It is not authentication and must not be trusted from a browser in production. `financial_analyst_app/app/main.py:39-43`

The backend must derive:

```text
user_id
role
available_brokers
active_broker
tenant scope
permissions
```

from a validated server-side session or JWT.

The frontend may use the returned permissions to hide or disable actions, but the backend must enforce every permission again.

### 4.2 Viewer privacy rule

An unauthenticated Viewer must not receive private portfolio, order, risk, ledger, position, or account data. Market data is global and may be public; private trading state remains tenant-scoped.

The current demo permission map includes some broad read permissions. That must be reconciled before production authentication is introduced.

### 4.3 Owner/Admin privilege and scope

Owner/Admin is the highest application privilege. The Owner/Admin workspace may access:

- all authorized tenants and brokers;
- all trader portfolios, positions, orders, fills, risk decisions, and ledger data;
- global strategies, strategy versions, capability state, and runtime health;
- global risk limits, feature flags, broker/instrument configuration, and reference snapshots;
- kill and explicit resume controls;
- user/role administration and support actions;
- system audit and activity records.

Owner/Admin access is still controlled by the backend. The UI must show the active tenant scope, record every privileged action, and require elevated confirmation for destructive, live, configuration, kill, and resume operations.

Owner/Admin does not receive secret values. Broker tokens, database credentials, Redis credentials, vault contents, and private keys remain redacted and backend-only.

---

## 5. Frontend Product Architecture

The full frontend should be one workstation shell, not ten unrelated websites.

### 5.1 Shell regions

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ SYSTEM STATUS   MARKET   MODE   RISK   SEARCH / COMMAND       USER / SYS │
├────────┬──────────────────────────────────────────────────────┬─────────────┤
│ NAV    │                 PRIMARY WORKSPACE                   │ INSPECTOR   │
│        │                                                      │             │
├────────┴──────────────────────────────────────────────────────┴─────────────┤
│ EVENTS │ ORDERS │ POSITIONS │ ALERTS │ SYSTEM │ ACTIVITY │ TERMINAL       │
└────────────────────────────────────────────────────────────────────────────┘
```

The shell contains:

- system status strip;
- market context strip;
- MODE and AUTO status;
- risk status;
- command bar;
- left operational navigation;
- primary workspace;
- right universal inspector;
- bottom activity dock.

``frontend/specs/frontend_spec.md`:97-132`

### 5.2 Work environments

1. Home / Command Center
2. Market Workstation
3. Research Workstation
4. AI Intelligence Center
5. Signal / Recommendation Center
6. News / Event Intelligence
7. Trading and Execution
8. Portfolio and Risk
9. Strategy / Algorithm Lab
10. System Control Center

These are modes of one shell. Each workspace may contain multiple panels, charts, tables, and inspectors. ``frontend/specs/frontend_spec.md`:160-180`

### 5.3 Unified Trading Workspace — amendment 2026-09-26

Manual and automatic trading are **one Trading Workspace**, not two separate applications or duplicate dashboards. The workspace switches the active panel according to backend-authoritative state:

```text
Environment: RESEARCH | PAPER | LIVE
Execution:   MANUAL | AUTO
Automation:  OFF | ARMED | RUNNING | PAUSED
```

These states are related but must remain independent. `LIVE` describes the environment; it does not automatically mean `AUTO`. `AUTO` describes who creates trade intent; it does not bypass risk, capability, kill, broker, or live-arm checks.

The workspace keeps the same:

- chart and market context;
- portfolio and risk summary;
- recommendations and signal stream;
- order/fill activity;
- inspector and provenance panel.

It changes only the execution panel:

| State | Primary panel | Behavior |
|---|---|---|
| `MANUAL` | Order ticket | Trader enters order inputs, requests preview, then explicitly confirms |
| `AUTO` | Recommendation and automation panel | Approved strategies may be armed for the trader's own broker/account; backend execution events drive fills and exits |
| `RESEARCH` | Simulation/research panel | No live order submission |
| `PAPER` | Manual or automated paper path | Uses the same contracts with paper/simulated execution |
| `LIVE` | Manual or automated live path | Requires explicit live arming, risk approval, and final backend authorization |

The backend, not the browser, decides whether automation is eligible to fire. The browser may arm or pause an approved strategy, but it must not run strategy logic, timers, risk calculations, sizing, or order submission itself.

The main UI may show a compact recommendation strip and active-trade status, while the dedicated Trading Workspace contains the full chart, ticket/control panel, risk view, and order blotter. This is one shell with contextual depth, not a second unrelated frontend.

### 5.4 Recommendation and trade-result visualization

Recommendation markers on the chart represent backend events, not frontend predictions. Each marker must retain its state and lineage:

```text
RECOMMENDED → VALIDATED → PLAN_CREATED → RISK_APPROVED
→ SUBMITTED → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED → CLOSED
```

Markers may be colored by state or risk, but not by eventual profit while the position is open. A trade receives win/loss coloring only after the backend reports a closed/settled result. Live unrealized PnL and closed realized PnL must remain separate, backend-owned values.

### 5.5 Access surfaces and role navigation

The UI is one application with role-specific surfaces:

| Surface | Access | Visibility |
|---|---|---|
| `/` | Viewer | Public market data, chart, public analytics, capability status, public research, system status |
| `/login` | Unauthenticated | Authentication only; no private account data |
| `/app` | Trader | Personal market context, portfolio, signals, trade plans, manual trading, approved automation, own orders/fills/ledger |
| `/app/settings` | Trader | Own broker/account settings, approved strategy arming, notification and workspace preferences |
| `/admin` | Owner/Admin | Global health, all authorized tenant views, global strategies/risk/configuration, users/roles, audit, kill/resume |
| `/admin/system` | Owner/Admin | Versioned configuration, feature flags, broker/instrument/reference snapshots, system diagnostics |

Viewer navigation excludes private portfolio, orders, risk, ledger, account mode, automation, kill/resume, and administration. Trader navigation includes private trading state but excludes global configuration and unrestricted tenant access. Owner/Admin navigation includes the System Control Center and scoped cross-tenant support views.

Role checks must be enforced by the backend on every route and WebSocket subscription. Hiding navigation is presentation only and is not authorization.

---

## 6. Frontend Technical Architecture

The stack remains:

- Vite;
- React;
- TypeScript;
- feature-first folders;
- no backend Python imports;
- backend does not serve static frontend files.

``frontend/specs/frontend_spec.md`:25-57` `frontend/README.md:34-39`

### 6.1 Target structure

```text
frontend/
  app/
  design-system/
    foundations/
    components/
    patterns/
    layouts/
  features/
    account/
    market/
    chart/
    analytics/
    capabilities/
    strategies/
    signals/
    trade_plans/
    trading/
    portfolio/
    risk/
    orders/
    execution/
    ledger/
    health/
    command/
    system/
  services/
  state/
  realtime/
  types/
  shared/
  tests/
```

### 6.2 Service boundary

One service module per backend domain:

```text
session_api
account_api
market_data_api
analytics_api
capability_api
strategy_api
signal_api
trade_plan_api
trade_api
portfolio_api
risk_api
order_api
execution_api
ledger_api
health_api
command_api
```

Feature components must use services and typed state. They must not call `fetch` directly.

### 6.3 State separation

#### Server state

- session;
- account status;
- market data;
- analytics;
- capabilities;
- strategies;
- signals;
- trade plans;
- portfolio;
- risk;
- orders;
- executions;
- ledger;
- health.

#### Realtime state

- WebSocket connection state;
- subscribed topics;
- last accepted sequence;
- snapshot version;
- pending deltas;
- replay status;
- stale/disconnected state.

#### UI state

- selected instrument;
- selected timeframe;
- selected broker;
- workspace layout;
- chart settings;
- table filters;
- inspector selection;
- density level;
- command palette state.

#### Safety state

- MODE;
- AUTO;
- live-arm status;
- kill state;
- risk decision;
- capability status;
- confirmation state;
- execution authorization.

The frontend may perform presentation-only arithmetic, but never authoritative financial, risk, sizing, or indicator calculations. ``frontend/specs/frontend_spec.md`:59-72`

---

## 7. Backend Contract Proposal

### 7.1 Session and tenant

Proposed endpoint:

```text
GET /api/v1/session
```

Proposed response data:

```text
authenticated
user_id
role
display_name
available_brokers
active_broker
permissions
tenant_scope
```

Required behavior:

- Viewer receives a public session with no private tenant data.
- Trader receives only their own user/broker scope.
- Admin receives the owner scope required for administration.
- Broker selection is validated server-side.
- No secret values are returned.

### 7.2 Common REST response

Recommended envelope:

```json
{
  "data": {},
  "meta": {
    "correlation_id": "uuid",
    "as_of": "2026-09-26T12:00:00Z",
    "schema_version": "1.0",
    "sequence": 1842,
    "next_cursor": null
  },
  "error": null
}
```

Recommended error shape:

```json
{
  "data": null,
  "meta": {
    "correlation_id": "uuid",
    "as_of": "2026-09-26T12:00:00Z"
  },
  "error": {
    "code": "CAPABILITY_UNAVAILABLE",
    "message": "Order-book imbalance is unavailable for this broker",
    "retryable": false,
    "details": {}
  }
}
```

The frontend must not depend on FastAPI string-only `detail`. It must consume stable `error.code` values.

### 7.3 Common request headers

```text
Authorization: Bearer <session-token>
X-Broker: deriv
X-Correlation-Id: <request-id>
Idempotency-Key: <required for trade writes>
```

`X-Role` remains a local-demo compatibility mechanism only.

### 7.4 Proposed API inventory

These routes are a contract proposal and must be frozen in M10.2 before frontend implementation.

| Domain | Proposed endpoint | Role | Status |
|---|---|---|---|
| Session | `GET /api/v1/session` | Viewer/Trader/Admin | Proposed |
| Account | `GET /api/v1/account/status` | Viewer/Trader/Admin | Implemented |
| Account mode | `POST /api/v1/account/mode` | Trader/Admin | Implemented, demo-only auth |
| AUTO | `POST /api/v1/account/auto-trade` | Trader/Admin | Implemented, demo-only auth |
| Health | `GET /api/v1/health` | Viewer/Trader/Admin | Proposed |
| Module health | `GET /api/v1/health/{module}` | Viewer/Trader/Admin | Proposed |
| Instruments | `GET /api/v1/market/instruments` | Viewer/Trader/Admin | Proposed |
| Candles | `GET /api/v1/market/candles` | Viewer/Trader/Admin | Proposed |
| Live market data | WebSocket snapshot/delta | Viewer/Trader/Admin | Proposed |
| Capabilities | `GET /api/v1/capabilities?broker=deriv` | Viewer/Trader/Admin | Proposed |
| Analytics | `GET /api/v1/analytics` | Viewer/Trader/Admin | Proposed |
| Strategies | `GET /api/v1/strategies` | Viewer/Trader/Admin | Proposed |
| Strategy control | `POST /api/v1/strategies/{id}/enable` | Admin; Trader only if explicitly approved | Proposed |
| Signals | `GET /api/v1/signals` | Viewer/Trader/Admin | Proposed |
| Trade plans | `GET /api/v1/trade-plans/{id}` | Viewer/Trader/Admin | Proposed |
| Trade preview | `POST /api/v1/trades/preview` | Trader/Admin | Proposed |
| Trade confirmation | `POST /api/v1/trades/{id}/confirm` | Trader/Admin | Proposed |
| Portfolio | `GET /api/v1/portfolio` | Trader/Admin; public viewer only with demo/public projection | Proposed |
| Risk read | `GET /api/v1/risk` | Trader/Admin; Viewer may receive redacted public risk only | Proposed |
| Risk preview | `POST /api/v1/risk/preview` | Trader/Admin | Proposed |
| Orders | `GET /api/v1/orders` | Viewer/Trader/Admin, tenant-scoped | Proposed |
| Order detail | `GET /api/v1/orders/{id}` | Viewer/Trader/Admin, tenant-scoped | Proposed |
| Executions | `GET /api/v1/executions` | Viewer/Trader/Admin, tenant-scoped | Proposed |
| PnL | `GET /api/v1/accounting/pnl` | Viewer/Trader/Admin, tenant-scoped | Proposed |
| Ledger | `GET /api/v1/ledger` | Viewer/Trader/Admin, tenant-scoped | Proposed |
| Reconciliation | `GET /api/v1/reconciliation` | Viewer/Trader/Admin, tenant-scoped | Proposed |
| Kill | `POST /api/v1/risk/kill` | Admin | Proposed |
| Resume | `POST /api/v1/risk/resume` | Admin | Proposed |
| Command | `POST /api/v1/commands` | Viewer/Trader/Admin by command | Proposed |
| Job status | `GET /api/v1/jobs/{job_id}` | Requester/Admin | Proposed |

### 7.5 Market-data contract

The market-data response must preserve:

```text
instrument_id
broker/provider
timeframe
interval_start
interval_end
OHLC
tick_count or volume when genuinely available
quality
lifecycle
provider_event_time
availability_time
processed_time
watermark
reference_snapshot_id
aggregation_version
```

The frontend must display quality, gaps, stale state, and lineage. It must not infer missing data as complete.

### 7.6 Analytics contract

The M3.1 `FeatureRequest` currently defines:

```text
instrument_id
broker
timeframe
features
as_of
asset_class
```

`docs/specs/m3.1_feature_engine.spec.md:53-61`

The API response should return the frozen `AnalyticsResult` plus any later-approved `AnalysisSnapshot` metadata. The frontend must not compute missing indicator values.

Initial analytics states must be explicit:

```text
READY
WARMING_UP
INSUFFICIENT
INVALID
STALE
UNAVAILABLE
PROXY_LIMITED
```

### 7.7 Realtime contract

Proposed WebSocket envelope:

```json
{
  "type": "snapshot | delta | replay | heartbeat | error",
  "topic": "portfolio.deriv",
  "user_id": "42",
  "broker": "deriv",
  "sequence": 1843,
  "as_of": "2026-09-26T12:00:00Z",
  "schema_version": "1.0",
  "correlation_id": "uuid",
  "payload": {}
}
```

Sequence rules:

1. Receive full snapshot on subscribe.
2. Accept only the next contiguous sequence.
3. Stop applying deltas on a gap.
4. Request replay from the last accepted sequence.
5. Show `RECONNECTING` or `STALE` until replay succeeds.
6. Never mix snapshots from different sequences.

The current M10.1 synchronization contract must be frozen before realtime frontend work. `System_Development_Manual/modules/10_M10_Experience.md:29-46`

### 7.8 Trade and execution contract

A trade must follow:

```text
TradePlan
  → preview
  → RiskDecision
  → explicit confirmation
  → Order
  → ExecutionResult
  → Fill
  → Ledger
```

The frontend must display effective parameters and the backend risk result at confirmation time.

The backend must enforce:

- MODE;
- AUTO where applicable;
- permissions;
- broker capability;
- live arming;
- kill state;
- risk approval;
- idempotency;
- state-machine legality.

`MODE=LIVE` and `AUTO=ON` are not sufficient execution authorization. ``frontend/specs/frontend_spec.md`:182-204`

---

## 8. Configuration Ownership

The frontend receives redacted state only. It must never receive secret values.

| Configuration | Owner | Frontend visibility |
|---|---|---|
| `config/accounts.yaml` | Backend/admin | Redacted account status and arming state |
| `config/brokers/*.yaml` | Backend/admin | Broker name, configured state, health, capability summary |
| `config/assets/*.yaml` | Backend/admin | Instruments, display metadata, effective specs |
| `config/capabilities.yaml` | Backend/admin | Capability badges and reasons |
| `config/strategies/*.yaml` | Backend/admin | Strategy status and version metadata |
| `config/risk/limits.yaml` | Backend/admin | Redacted limits and status |
| `config/environments/*.yaml` | Backend/admin | Environment name and health |
| `config/system/*.yaml` | Backend/admin | Redacted runtime/system state |
| `config/system/feature_flags.yaml` | Backend/admin | Feature availability |
| `config/reference/snapshot.yaml` | Backend/admin | `reference_snapshot_id` and effective lineage |
| `.env` and vault | Backend/secret manager | Never exposed |
| Redis/Valkey config | Backend/operator | Health only |
| TimescaleDB config | Backend/operator | Health only |
| Broker tokens | Vault/operator | Never exposed |

---

## 9. Frontend Feature Plan

### 9.1 Account and environment controls

Components:

- mode status;
- AUTO status;
- broker selector;
- live-arm status;
- session status;
- disconnect/reconnect status;
- safety confirmation dialog.

Behaviors:

- Viewer cannot mutate.
- Trader can switch their own tenant mode and AUTO.
- Admin can inspect and override according to backend policy.
- Every mutation requires a typed backend response.
- Non-2xx never produces a success toast.

### 9.2 Market workstation

Components:

- instrument selector;
- timeframe selector;
- chart;
- forming/closed badge;
- quality/gap badge;
- watermark and reference snapshot;
- chart event timeline;
- market data inspector;
- data source status.

The chart consumes backend candle/delta data. It does not call M2.9 directly.

### 9.3 Analytics and capabilities

Components:

- feature request builder;
- indicator values;
- capability matrix;
- proxy warnings;
- warmup/progress state;
- insufficient/invalid/stale state;
- analytics provenance.

The UI must show why a feature is unavailable and must not treat a warning as a fully supported result.

### 9.4 Research and signals

Components:

- signal board;
- evidence panel;
- explainability drawer;
- strategy eligibility;
- signal freshness;
- shadow state;
- trade-plan preview;
- entry/SL/TP/timing inspection.

The UI consumes `Signal`, `AnalyticsResult`, and `TradePlan`; it does not interpret a signal as an order.

### 9.5 Trading and execution

The Trading Workspace has one shared context and two conditional execution panels.

#### Manual panel

- instrument/side/order type;
- quantity;
- entry;
- SL;
- TP;
- time-in-force and applicable execution fields;
- optional recommendation-to-ticket action;
- backend risk preview;
- final effective-parameter confirmation;
- order lifecycle;
- partial fills;
- rejection and unknown state.

A recommendation may populate the manual ticket, but editing it creates a new backend trade preview/plan. The frontend must not silently modify a backend-generated TradePlan.

#### Automatic panel

- approved strategy selector;
- broker/account scope;
- strategy state: disabled, shadow, armed, running, paused;
- instrument and timeframe scope;
- risk budget and exposure limits;
- max trades and session limits;
- schedule/session controls;
- arm, pause, disarm, and kill actions;
- recommendation and execution event stream.

Trader may arm only strategies already approved for that trader's own broker/account. Strategy parameters and risk controls belong in Settings and are persisted through versioned backend contracts. Admin owns global strategy, risk, kill, and configuration controls. The browser may request arm/pause/disarm, but the backend scheduler and M5/M6 own whether a trade fires.

AUTO OFF does not hide manual trading. ``frontend/specs/frontend_spec.md`:198-204`

### 9.6 Portfolio and risk

Components:

- cash/equity/margin;
- positions;
- gross/net exposure;
- per-trade portfolio impact;
- risk status;
- limits;
- reconciliation;
- health drawer.

All money and risk values are backend-owned.

### 9.7 Kill and resume

Admin-only controls:

```text
Kill
  → confirmation
  → backend kill request
  → current kill state
  → affected scopes
  → audit record

Resume
  → explicit human action
  → reason
  → backend validation
  → result
```

Resume must never auto-resume after a daily-loss breach.

### 9.8 System control center

Owner/Admin-only surface. It includes:

- feed health;
- module health;
- latency;
- queue depth;
- data quality;
- broker state;
- strategy health;
- audit/activity stream;
- redacted configuration status.

Configuration editing requires versioned, audited Owner/Admin APIs. Direct browser YAML editing is not permitted. Every privileged action displays its target scope and produces an audit record.

### 9.9 Future intelligence surfaces

Planned only after backend contracts exist:

- AI analyst;
- news;
- economic calendar;
- research;
- backtests;
- alerts;
- watchlists;
- screeners;
- heatmaps;
- workspace persistence.

These surfaces are read-only unless a separate role and backend contract explicitly permits writes.

---

## 10. Implementation Phases

### Phase 0 — Contract freeze

Backend must deliver:

- session/JWT/tenant middleware;
- role/permission response;
- common error envelope;
- OpenAPI snapshot;
- market/analytics DTOs;
- capability endpoint;
- health endpoint;
- WebSocket grammar;
- trade preview/confirm;
- kill/resume;
- redacted configuration contract.

### Phase 1 — Frontend foundation

Deliver:

- Vite/React/TypeScript shell;
- design-system foundations;
- feature routing;
- service layer;
- typed API client;
- state boundaries;
- error/loading/empty states;
- role-aware navigation;
- accessibility baseline.

### Phase 2 — Account and session

Deliver:

- login/session surface when backend auth exists;
- account status;
- broker selector;
- MODE/AUTO controls;
- role-based visibility;
- safe confirmation dialogs.

### Phase 3 — Market workspace

Deliver:

- public market data;
- instrument/timeframe selection;
- chart;
- quality/provenance;
- live and closed states;
- M2 data inspector.

### Phase 4 — Analytics workspace

Deliver after M3.1/M3.2 contracts:

- feature requests;
- indicator panels;
- capability badges;
- warnings;
- warmup/insufficient states;
- PIT/as-of provenance.

### Phase 5 — Research and strategy

Deliver after M4 contracts:

- signals;
- strategy status;
- shadow mode;
- explainability;
- trade plans;
- simulation/research actions.

### Phase 6 — Trading

Deliver after M5/M6 contracts and G0/G1 requirements:

- manual trade session;
- preview;
- risk result;
- confirmation;
- order state;
- fills;
- unknown/reconciliation state.

### Phase 7 — Portfolio and operations

Deliver after M5/M7 contracts:

- portfolio;
- risk;
- ledger/PnL;
- reconciliation;
- kill/resume;
- health;
- activity/audit.

### Phase 8 — Realtime and commands

Deliver after M10.1/M10.3:

- WS snapshot/delta;
- sequence tracking;
- replay;
- reconnect;
- command palette;
- read commands;
- trade/confirm command flow.

### Phase 9 — Future intelligence

Deliver only after separate contracts:

- AI;
- news;
- calendar;
- research;
- backtests;
- alerts;
- advanced workspace persistence.

---

## 11. Frontend Testing and Acceptance

Required tests:

- TypeScript typecheck;
- production Vite build;
- API-client contract tests;
- OpenAPI/generated-type compatibility;
- role and permission tests;
- Viewer/Trader/Admin visibility tests;
- 401/403/409/422 handling;
- session expiry;
- tenant isolation;
- WebSocket snapshot/delta sequence tests;
- WebSocket gap/replay/reconnect tests;
- stale/disconnected state tests;
- no-secret exposure tests;
- no-client-business-calculation tests;
- live confirmation safety tests;
- kill/resume safety tests;
- trade idempotency tests;
- accessibility and keyboard tests;
- browser tests for chart, tables, and workspace interactions;
- human review of execution-critical screens.

The frontend is not complete when it merely renders cards. It is complete when every displayed state is traceable to a backend contract and every write follows the backend authorization and safety path.

---

## 12. Backend Integration Order

Recommended backend order for the frontend dependency graph:

```text
M10.2 auth/session/tenant foundation
  → M10.2 health/account/common error contracts
  → M3.0 capability read contract
  → M2 market-data read contract
  → M3.1 Feature Engine
  → M3.2 indicators
  → M10.1 synchronization
  → M4 strategy/signal/TradePlan
  → M5 portfolio/risk/kill/resume
  → M6 orders/executions
  → M7 ledger/reconciliation
  → M10.3 commands
  → M10.4 full frontend
```

The exact order remains subject to the repository gate order and human approval. This proposal does not authorize backend implementation or frontend implementation by itself.

---

## 13. Open Amendments

This proposal is intentionally amendable. The following decisions require human review:

| Decision | Current proposal | Status |
|---|---|---|
| Authentication | Server-validated session or JWT | Open |
| Viewer private data | Public viewer cannot access private tenant data | Recommended |
| Role source | Backend-derived role only; `X-Role` local demo only | Recommended |
| Owner/Admin privilege | Highest application privilege across authorized tenants and system controls | Amended 2026-09-26 |
| Owner/Admin secrets | Secrets, vault contents, credentials, and tokens remain redacted | Amended 2026-09-26 |
| Account scope | MODE/AUTO scoped to user + broker | Recommended |
| API error format | Common `ErrorCode` envelope | Open |
| API prefix | `/api/v1/...` | Open |
| Health routes | `/api/v1/health`, `/api/v1/health/{module}` | Open |
| Trade preview | `POST /api/v1/trades/preview` | Open |
| Trade confirmation | `POST /api/v1/trades/{id}/confirm` | Open |
| Kill route | `POST /api/v1/risk/kill` | Open |
| Resume route | `POST /api/v1/risk/resume` | Open |
| WebSocket route | One authenticated per-tenant endpoint | Open |
| WebSocket grammar | `snapshot | delta | replay | heartbeat | error` | Open |
| Ledger contract | New frozen M7 contract required | Open |
| Configuration editing | Versioned Owner/Admin API, no direct YAML browser edit | Amended 2026-09-26 |
| Strategy control | Owner/Admin global; Trader approved strategies for own broker/account | Amended 2026-09-26 |
| Public analytics | Viewer allowed only for non-private/global data | Recommended |
| Future AI/news/backtests | Planned/read-only until separately contracted | Recommended |
| Trading workspace | One shared Trading Workspace; execution panel switches between MANUAL and AUTO | Amended 2026-09-26 |
| Environment state | `RESEARCH`, `PAPER`, and `LIVE` remain independent from MANUAL/AUTO | Amended 2026-09-26 |
| Automation ownership | Backend scheduler and M5/M6 decide firing; browser may only request arm/pause/disarm | Amended 2026-09-26 |
| Trader automation scope | Trader may arm approved strategies for own broker/account; Owner/Admin owns global controls | Amended 2026-09-26 |
| Frontend filename | This proposal is `FRONTEND_BACKEND_INTEGRATION_PROPOSAL.md` | Amendable |

---

## 14. Current Blockers

Before full frontend development, the repository needs:

1. A real authentication/session/tenant contract.
2. A standard frontend-consumable API error envelope.
3. Frozen market-data and analytics API DTOs.
4. A capability read endpoint.
5. A health endpoint.
6. A frozen M10.1 WebSocket sequence/replay contract.
7. Frozen M5/M6/M7 API contracts for trading, risk, execution, ledger, and reconciliation.
8. A resolution for the kill/resume route conflict.
9. A resolution for the current process-global account-gate behavior versus per-user tenant behavior.
10. Human opening of M10.4 for full frontend implementation.

---

## 15. Proposal Status

**Open for amendment.**

This document may be revised as backend contracts are written, tests are approved, and human decisions are recorded. It must not be treated as a frozen specification until the human explicitly approves the relevant sections and `BUILD_STATUS.md` is updated.

**Created:** 2026-09-26  
**Owner:** Builder proposal for human amendment  
**Related:** ``frontend/specs/frontend_spec.md``, `System_Development_Manual/modules/10_M10_Experience.md`, `docs/specs/m3.1_feature_engine.spec.md`, `docs/specs/m2.9_hot.spec.md`
