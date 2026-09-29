# Frontend ↔ Backend Integration Proposal

> **Status:** Open for amendment. This is an implementation proposal, not a frozen specification.
>
> **Purpose:** Define the contract-first plan for connecting the QUANTRADER2 frontend to the backend safely, with explicit role boundaries, configuration ownership, data provenance, and backend integration requirements.
>
> **Scope:** `frontend/` as the delivery shell and `financial_analyst_app/` as the backend. The backend remains the authority for analytics, risk, PnL, execution, and account state.
>
> **Authority:** `AGENTS.md`, `BUILD_STATUS.md`, `USER_ACTIONS.md`, the approved manual sections, and frozen contracts win over this proposal. Any amendment that changes a frozen contract must go through the required change process and human sign-off.
>
> **Amendment 2 — 2026-09-27 (human decisions recorded):**
>
> 1. **Authentication decided** — OIDC / OAuth2 Authorization Code + PKCE. The backend validates the JWT and builds the `Principal` server-side. The provider is **not yet named**; issuer, audience, and client id are configuration, and JWKS discovery follows the standard. See §4.1.
> 2. **Realtime decided** — REST for dashboards on navigate, WebSocket for **live ticks only**. This **narrows M10.1** from a per-tenant gap-free pipe to a tick stream; per-tenant sequence, replay, and `RECONNECTING` are **deferred by decision**. See §7.7 and §12.
> 3. **Contract freeze first** — no gateway implementation until the M10.2 spec and contracts are frozen. See §12.
> 4. **Build order** — directive **A65** supersedes the order previously proposed in §12. The API gateway is **step 9 of 9, last**, and no step may be skipped. `BUILD_STATUS.md:89` `USER_ACTIONS.md:239`
>
> Sections touched by Amendment 2: §1, §4.1, §4.3, §6.1, §6.2, §7.2, §7.2.1 (new), §7.4, §7.7, §11, §12, §13, §14.
>
> **Amendment 1 — 2026-09-26:** §5.3 unified Trading Workspace, §4.3 Admin privilege and secret redaction, §8 configuration ownership.

---

## 1. Current Baseline

> **Corrected by Amendment 2 (2026-09-27).** The previous revision of this section was written on 2026-09-26 and described the frontend as a static Day-1 panel. The frontend has since been built as a React + Vite + TypeScript SPA. The corrections below are the verified state at this revision.

The current project is at **G0 — Edge Validation**. `BUILD_STATUS.md:6`

The current frontend is a **React 19 + Vite + TypeScript SPA** with role-gated surfaces, a design system, and a test suite. The M10.4 workstation is **partially built**, not unimplemented. `BUILD_STATUS.md:23`

The current backend exposes only the account-gate API:

| Method | Path | Current status |
|---|---|---|
| GET | `/api/v1/account/status` | Implemented |
| GET | `/api/v1/account/mode` | Implemented alias |
| POST | `/api/v1/account/mode` | Implemented |
| POST | `/api/v1/account/auto-trade` | Implemented |

`financial_analyst_app/modules/experience/api/v1/router.py:57-88`

> **Citation correction:** the four routes are defined in `modules/experience/api/v1/router.py`, **not** in `app/main.py`. `app/main.py` is a 49-line composition root and defines no routes; it mounts the router, configures CORS, and starts the app.

The current backend does **not** yet expose production market-data, analytics, strategy, portfolio, risk, order, execution, ledger, health, command, or WebSocket APIs for the frontend.

The following are current repository facts:

- M3.0 Capability Registry is **complete**. `BUILD_STATUS.md:37`
- M3.1 Feature Engine is **complete and frozen** (REV3, LGTM A35 spec + A36 tests). `BUILD_STATUS.md:38`
- M3.1 Provider Port (Temporal Resolver + PIT retrieval) is **in progress**. `BUILD_STATUS.md:41`
- M2.9 HOT is a cache, not a frontend data authority. `docs/specs/m2.9_hot.spec.md:1-6`
- M2.10 WARM (Timescale `candles_1m`) is **complete**, not deferred. `BUILD_STATUS.md:36`
- M2.12 News is a **build-time script**, not a backend service. `docs/specs/m2.12_news_ingest.spec.md`
- M4.1 Strategy Registry is **specified with approved tests but not implemented**; the module is absent and the backend suite cannot complete collection. `BUILD_STATUS.md:42` `docs/specs/m4.1_strategy_registry.spec.md`
- M4.2 Strategy Files are **complete** (trend_following, mean_reversion, pullback, donchian_breakout, and others). `BUILD_STATUS.md:44`
- M5, M6, and M7 have **not started**. `BUILD_STATUS.md:44-52`
- The frontend must not query Redis, the Lake, TimescaleDB, or internal Python modules directly. ``frontend/specs/frontend_spec.md`:18-23`
- The frontend must not import backend Python modules. This is enforced by an active test. `frontend/tests/test_account_mode_page.py:57-65`

Additional verified facts that constrain this proposal:

- **There is no identity.** `X-Role` is a client-supplied request header with no token, no session, and no server-side principal. It is spoofable by design of the demo and must never be trusted. `financial_analyst_app/modules/experience/api/v1/router.py:35-39`
- **There are no WebSocket routes.** `fastapi` and `websockets` are available as dependencies, but no WebSocket endpoint exists for the browser.
- **There is no query-serving read path.** The existing Timescale and Valkey access is write-path and self-verification read-back only. A gateway must add readers; it cannot reuse a read API that does not exist. `financial_analyst_app/modules/m2_market_data/warm.py`
- **Store connections are never opened in production.** `connect()` methods exist but no application lifespan calls them.
- **There is no auth dependency in the project.** `pyproject.toml` declares no JWT or OAuth library.
- **The frontend already ships a read-only command registry** with `/trade` and `/confirm` deliberately absent, because M6 Execution does not exist. `frontend/app/commands.ts`
- **The `.importlinter` tenant-isolation contract is a placeholder.** The contract that would enforce "all DB access goes through tenant context" is self-described as un-enforced. `financial_analyst_app/.importlinter:17-19`
- **The only working frontend-to-backend integration is being removed** as part of Amendment 2, because it is a dependency-free vanilla-JS page superseded by the React application.

---

## 2. Goals

1. Give users a safe institutional workstation over backend-owned state.
2. Keep all business calculations and execution decisions in the backend.
3. Make provenance, quality, staleness, and uncertainty visible.
4. Support read-only public market intelligence for viewers.
5. Support signed-in, tenant-scoped trading workflows for traders.
6. Give the admin controlled configuration, audit, and emergency operations.
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
| **Admin** | Signed in as admin | Highest application privilege: all tenant data, all trader operations, global strategy/risk/configuration, kill/resume, user/role administration, audit, and system controls |

The RBAC basis is `viewer`, `trader`, and `admin`. `financial_analyst_app/shared/core/permissions.py:13-43`

> **Terminology resolved by Amendment 2.** Earlier revisions of this proposal used the label **Owner/Admin**. The shipped frontend uses **`Admin`**, and that is now the canonical label in this document. **Owner/Admin** is a **deprecated alias** retained only where it appears in already-approved manual text. The backend role value has always been `admin`; no backend identifier changes.

### 4.1 Production authentication requirement

> **Decided 2026-09-27 (Amendment 2).** Authentication is no longer an open question in this section. The mechanism is fixed; only the identity provider remains unnamed.

The current `X-Role` header is a local demonstration mechanism only. It is not authentication and must not be trusted from a browser in production. `financial_analyst_app/modules/experience/api/v1/router.py:35-39`

**The production mechanism is OIDC / OAuth2 Authorization Code with PKCE.** The backend is the resource server. It validates the provider's JWT and constructs a `Principal` server-side. The browser never asserts its own role, tenant, or user id.

The backend must derive, from a validated token only:

```text
user_id
role
available_brokers
active_broker
tenant scope
permissions
```

The derived principal is the **only** input to authorization. `X-Role` is retained solely as an explicitly local development mechanism and **must be ignored entirely whenever OIDC validation is configured**, so that a demo header can never silently grant access in a configured environment.

**Provider-agnostic by design.** The identity provider is **not yet named**. The spec must treat the following as configuration, not code:

```text
issuer / discovery URL
audience
client id
JWKS cache and refresh policy
token lifetime expectations
```

JWKS retrieval and caching follow the standard OIDC discovery model. No provider SDK is required, and no provider-specific claim handling is permitted in the gateway: role and tenant claims must be mapped through one explicit translation table so that changing providers does not change gateway code.

**The frontend may use the returned permissions to hide or disable actions, but the backend must enforce every permission again.** The frozen RBAC basis is already a correct server-side capability check and is reused unchanged. `financial_analyst_app/shared/core/permissions.py:13-43`

### 4.2 Viewer privacy rule

An unauthenticated Viewer must not receive private portfolio, order, risk, ledger, position, or account data. Market data is global and may be public; private trading state remains tenant-scoped.

The current demo permission map includes some broad read permissions. That must be reconciled before production authentication is introduced.

### 4.3 Admin privilege and scope

Admin is the highest application privilege. The Admin workspace may access:

- all authorized tenants and brokers;
- all trader portfolios, positions, orders, fills, risk decisions, and ledger data;
- global strategies, strategy versions, capability state, and runtime health;
- global risk limits, feature flags, broker/instrument configuration, and reference snapshots;
- kill and explicit resume controls;
- user/role administration and support actions;
- system audit and activity records.

Admin access is still controlled by the backend. The UI must show the active tenant scope, record every privileged action, and require elevated confirmation for destructive, live, configuration, kill, and resume operations.

Admin does not receive secret values. Broker tokens, database credentials, Redis credentials, vault contents, and private keys remain redacted and backend-only.

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
| `/admin` | Admin | Global health, all authorized tenant views, global strategies/risk/configuration, users/roles, audit, kill/resume |
| `/admin/system` | Admin | Versioned configuration, feature flags, broker/instrument/reference snapshots, system diagnostics |

Viewer navigation excludes private portfolio, orders, risk, ledger, account mode, automation, kill/resume, and administration. Trader navigation includes private trading state but excludes global configuration and unrestricted tenant access. Admin navigation includes the System Control Center and scoped cross-tenant support views.

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

> **Amended in Amendment 2 — the shipped layout differs from the target above.** This target was written when the frontend was a static vanilla-JS page. The shipped React application organises surfaces under `app/` and the layout is asserted by an active test. `frontend/tests/test_m10.4_shell_spec.py`
>
> Two specific corrections:
>
> 1. **`features/` does not exist in the shipped app.** The per-feature folders listed above are aspirational; the React app currently composes surfaces directly in `app/`. `frontend/app/App.tsx`
> 2. **`services/` exists but is empty.** The only file it ever held was a vanilla-JS account client, which is being removed as part of Amendment 2. **There is currently no service layer and no typed API client in the frontend.** Building one is the first task of the gateway work — it is the `services/*_api.ts` layer this section already describes, and it does not yet exist in any form.

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

> **The doctrine survives; its only implementation is being removed.** The swap-point rule — *features never call `fetch`; all HTTP goes through `services/*_api`* — remains correct and is the reason the gateway will be wired cleanly. What is being deleted is a dependency-free vanilla-JS implementation of it, superseded by the React application. After removal the rule has **zero** implementations, which makes building the typed service client a prerequisite for any real integration rather than a refactor. `frontend/HANDOFF.md:12`

Every service module has one job: take typed parameters, call exactly one endpoint, and return the typed `data` payload from the §7.2 envelope. Service modules are the **only** place in the frontend permitted to perform network I/O, and they must surface the frozen `error.code` rather than translating it into a generic failure.

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
- Admin receives the scope required for administration.
- Broker selection is validated server-side.
- No secret values are returned.

> **Amended in Amendment 2.** This section is where the `Principal` from §4.1 becomes concrete.
>
> The response body is the **projection** of a `Principal`; the `Principal` itself is the **server-side** object every handler receives. These are different things and the distinction is load-bearing: a handler must never accept a `Principal`, `user_id`, `role`, or `tenant_scope` as a **request parameter**. They are derived from the validated token and injected server-side. A request body that contains `user_id` or `role` is **rejected**, not ignored.
>
> **`GET /api/v1/session` is therefore a read-only introspection endpoint.** It lets the frontend learn what it is permitted to do, which the UI needs in order to render role-appropriate surfaces. It grants nothing: hiding navigation is presentation, and every route re-checks authorization independently. §5.5
>
> This is also why the frozen RBAC layer is reused unchanged rather than reimplemented in the gateway — it already performs a correct server-side capability check. `financial_analyst_app/shared/core/permissions.py:13-43`
>
> **Still missing:** no `Principal` contract exists in `shared/contracts/`, so there is currently no typed object for a handler to accept. Recorded as a blocker in §14.

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
    "code": "capability_not_supported",
    "message": "Order-book imbalance is unavailable for this broker",
    "retryable": false,
    "details": {}
  }
}
```

The frontend must not depend on FastAPI string-only `detail`. It must consume stable `error.code` values.

> **Error-code authority resolved by Amendment 2.** An earlier revision of this section used `CAPABILITY_UNAVAILABLE`. That was a second, competing dialect and is **withdrawn**. The **frozen** `ErrorCode` enum is the single authority, and its values are `lower_snake`. `financial_analyst_app/shared/contracts/errors.py:14`
>
> ```text
> insufficient_margin      exposure_limit        leverage_limit
> concentration_limit      spread_too_wide       liquidity_proxy
> data_quality_invalid     trade_plan_expired    missing_sl_tp
> rr_too_low               stale_signal          stale_alpha
> duplicate_signal         unknown_symbol        capability_not_supported
> qty_step                 insufficient_qty      broker_unavailable
> unknown_state
> ```
>
> The gateway must **reuse** these values and must never invent an error code outside the enum. A new code requires a contract change with the §5 change process, not an ad-hoc string in a handler.

**A third error shape must also be retired.** `BaseAppError.to_dict()` emits `error_code` / `message` / `details`, which is neither FastAPI's `detail` nor the envelope above. The envelope is the target; the exception's own serializer is aligned to it, or removed in favour of a single envelope builder. Until then there are three error formats in the codebase, and the frontend would have to understand all three. `financial_analyst_app/shared/core/exceptions.py:14-26`

The hierarchy itself is sound and is kept: a typed exception carrying both an `ErrorCode` and an HTTP `status_code` is exactly what the gateway needs in order to translate a failure into the envelope. Only the wire shape changes, not the internal design. `financial_analyst_app/shared/core/exceptions.py:1-6`

#### 7.2.1 `schema_version` policy

`schema_version` is present in the envelope but had no defined lifecycle. The policy is:

| Change | Bump | Compatibility |
|---|---|---|
| Documentation or clarification only | `patch` | Always compatible |
| Additive field with a default | `minor` | Older clients keep working |
| Rename, removal, or semantic change | `major` | Requires migration and two-version support |

Two versions are supported at a time. A deprecated version is announced with a `Sunset` response header before removal. `schema_version` is **per contract**, not a single global number, so one domain may be at `1.2` while another is still at `1.0`.

### 7.2.2 Pagination and rate limiting

> **New in Amendment 2.** Neither was specified in any earlier revision, while the inventory in §7.4 contains five unbounded collections. Without this, the first router written would invent its own convention.

Any endpoint returning a collection is paginated:

```text
GET /api/v1/orders?limit=50&cursor=<opaque>
```

```json
{
  "meta": {
    "next_cursor": "eyJrIjoxODQyLCJ0IjoiMjAyNi0wOS0yNlQxMjowMDowMFoifQ",
    "has_more": true
  }
}
```

- `limit` defaults to **50** and is capped at **500**. A larger request is rejected, not silently clamped.
- `cursor` is **opaque**. The frontend must not parse, construct, or persist it as a meaningful value; it is a server-owned ordering token.
- Ordering is **deterministic and stable**, with a unique tiebreaker, so a cursor cannot skip or repeat a row between pages.
- An empty result returns `next_cursor: null` and `has_more: false` — not a 404.

Rate limiting is a **server-side** control, and exceeding it returns `429` with a `Retry-After` header. Clients back off exponentially **with jitter**, never in a synchronised retry storm. A rate-limited drop is logged as dropped with `reason=rate_limited` and is never silently discarded. `AGENTS.md` §3.8

### 7.3 Common request headers

```text
Authorization: Bearer <oidc-access-token>
X-Broker: deriv
X-Correlation-Id: <request-id>
Idempotency-Key: <required on every write>
```

> **Amended in Amendment 2.** Two changes:
>
> 1. The bearer token is the **OIDC access token** validated per §4.1, not an opaque session token.
> 2. `Idempotency-Key` is now required on **every** `POST` / `PATCH` / `PUT` / `DELETE`, not only trade writes. An earlier revision scoped it to trades, which left mode switches, strategy arming, kill, resume, and configuration writes able to double-apply on retry. Each key is a `uuid5` derived from the originating `correlation_id`, so a retried request is deduplicated rather than re-executed. `AGENTS.md` §3.2

`X-Role` remains a local-demo compatibility mechanism only, and is ignored entirely once OIDC validation is configured. §4.1

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
| Sessions | `GET /api/v1/market/sessions` | Viewer/Trader/Admin | Proposed |
| Candles | `GET /api/v1/market/candles` | Viewer/Trader/Admin | Proposed |
| Live ticks | WebSocket `market.ticks.*` | Viewer/Trader/Admin | Proposed |
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
| News | `GET /api/v1/news` | Viewer/Trader/Admin | Proposed |

> **Added in Amendment 2.** `GET /api/v1/news` was missing from every earlier revision. The M2.12 news pipeline already produces the exact payload this route would serve, but it exists only as a **build-time script** that writes a snapshot consumed by the frontend at build time. It is **not** a backend service, and no endpoint serves it yet. `docs/specs/m2.12_news_ingest.spec.md` `financial_analyst_app/scripts/verify_m2.12_news_snapshot.py`
>
> News responses must carry the licence disposition per item. Items from commercial publishers are **headline + link only**; only items explicitly marked redistributable may include body text. `redistribute_text` is a per-item field, not a response-level flag.
>
> **Contract gap:** there is **no** `shared/contracts/news.py`. The news payload shape and its `redistribute_text` rule exist only as literals inside the build-time script, so the shape the frontend already consumes is **not frozen and not covered by `contract_tests`**. Before `GET /api/v1/news` is served to a browser, the payload must be promoted to a frozen contract with the licence rule expressed as a validated field rather than a script convention. `financial_analyst_app/scripts/verify_m2.12_news_snapshot.py:19` `financial_analyst_app/scripts/verify_m2.12_news_snapshot.py:256`
>
> The frontend already renders this data. Adding the route moves the data source from *build time* to *request time*; it does not change the payload shape the frontend already consumes. `frontend/app/NewsSection.tsx`

#### 7.4.1 Endpoints with no contract at all

Three endpoints in the inventory above are **more than proposed routes** — no contract exists anywhere in the codebase for them, so they cannot be implemented without first writing one:

| Endpoint | Missing contract | Consequence |
|---|---|---|
| `GET /api/v1/news` | No `shared/contracts/news.py` — payload and `redistribute_text` rule exist only as script literals | The route cannot serve a browser until the payload is a frozen, snapshot-tested contract. The frontend already renders the data, so the shape must not drift underneath it. |
| `GET /api/v1/audit` | No audit record contract, and no audit-emitting code exists in the backend | Audit is required for the Admin surface and for §3.12 immutability. This is a genuine gap, not just a missing route. |
| `GET /api/v1/configuration` | No `config_versions` contract | The Admin configuration surface can read and display, but cannot be versioned or audited without this. |
| `GET /api/v1/risk/kill` | No `KillState` contract — the kill switch currently exists only as the action string `risk.kill_switch` | A kill state is safety-critical. It must be a frozen contract, never an ad-hoc boolean, so that `UNKNOWN` can default to the safe assumption. `AGENTS.md` §3.3 |

These are recorded as blockers in §14. They are listed here so that no one reads "Proposed" as "ready to build".

#### 7.4.2 Build order of the inventory

The inventory is **not** a build order. §12 governs that. Within the gateway, the order that unblocks the most dashboards with the least new machinery is:

| Tier | Routes | Unblocks | New machinery required |
|---|---|---|---|
| **1** | `/health`, `/market/instruments`, `/news`, existing `/account/status` | Admin System, asset selector, Markets grid, News | Envelope, error mapping, correlation-id middleware, lifespan |
| **2** | `/market/candles`, tick WebSocket | Charts, all 12 market dashboards | PIT retrieval service, multi-instrument ingestion, multiplexed WS |
| **3** | `/analytics`, `/signals` | Research, signal stream | M4.1 runner (currently absent) |
| **4** | `/portfolio`, `/orders`, `/executions`, `/risk` | Private trading surfaces | **M5, M6, M7 — not started** |
| **5** | `/trades/*`, `/risk/kill`, `/risk/resume` | Execution | M5 + M6, human review mandatory |

Tier 1 is the cheapest meaningful step: it feeds four surfaces and touches no unbuilt module. Tier 4 cannot start regardless of gateway readiness.

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

> **Narrowed by Amendment 2 (human decision).** An earlier revision specified a per-tenant gap-free pipe carrying portfolio state. The decision is now **REST for dashboards on navigate, WebSocket for live ticks only**. The per-tenant sequence, replay, and `RECONNECTING` machinery is **deferred, not cancelled** — see the discrepancy note below.

Proposed WebSocket envelope:

```json
{
  "type": "ticks",
  "topic": "market.ticks.FX.EURUSD.1m",
  "broker": "deriv",
  "sequence": 1843,
  "as_of": "2026-09-26T12:00:00Z",
  "schema_version": "1.0",
  "correlation_id": "uuid",
  "payload": {}
}
```

#### 7.7.1 Transport shape

**One multiplexed WebSocket per browser**, not one socket per tenant or per dashboard. The client subscribes to the topics it needs and unsubscribes when a view closes.

Topic grammar:

```text
market.ticks.{instrument_id}.{timeframe}
```

**Tick topics are public and carry no `user_id`.** This is deliberate and is not an oversight: market data is stored **once per `provider + instrument`** with no `user_id`, and is fanned out to every tenant of that broker. A per-user tick topic would imply private market data that does not exist, and would tempt the implementation into per-tenant fan-out it must not do. `AGENTS.md` §4.3

Consequences of that decision:

- Tick subscription requires **no** authorization. It is public reference data.
- An **unauthenticated** Viewer may subscribe to ticks.
- Private state — portfolio, orders, risk, kill — is **not** carried on this socket. It is fetched over `REST` on navigate, per the decision above.

#### 7.7.2 Frame semantics — full replace, not JSON-Patch

A tick frame carries the **absolute latest value** for the instrument and timeframe, plus a monotonically increasing `sequence`. It is a **full replace**, not a JSON-Patch document.

> **Conflict resolved.** The bible's M10.1 description implies JSON-Patch-style deltas, while the earlier M10.1 draft specified *"Day-1 full replace — not JSON-Patch"*. **Full replace wins.** Applying a patch to a price is ambiguous when frames are lost, and full replace is idempotent: receiving the same tick twice is harmless, whereas applying a patch twice is not.

#### 7.7.3 Sequence and ordering rules

1. On subscribe the client receives the current absolute state for the topic.
2. `sequence` increases **per topic**, so two different instruments never interfere.
3. A frame whose `sequence` is **not greater** than the last applied value is **discarded**, not applied. This makes reconnection and out-of-order delivery safe.
4. The client **never** merges or averages tick frames into a candle. Candle construction belongs to the backend via `GET /api/v1/market/candles`. `AGENTS.md` §4.2
5. If a gap is detected, the client requests a fresh snapshot for that topic rather than requesting a replay from the server. History is a REST concern.

#### 7.7.4 Deferred: the per-tenant gap-free pipe

**Discrepancy logged against the bible.** M10.1 is described as *"The Gap-Free Pipe (Per-Tenant)"* with per-tenant sequence, replay, and `RECONNECTING` semantics. `System_Development_Manual/modules/10_M10_Experience.md:29-46` The decision taken here **narrows** M10.1 to a tick stream for Day-1 use.

This is a **recorded reduction in promised scope, not a silent omission.** The per-tenant machinery is still required before any private surface can stream live state, and it must be built before the portfolio, order, or risk surfaces can rely on push updates. Until then those surfaces use `REST` on navigate.

M10.1 is additionally **draft-parked** at `ACTION 24` in the status file and has neither tests nor implementation, so it cannot be consumed as-is regardless. `BUILD_STATUS.md`

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

Admin-only surface. It includes:

- feed health;
- module health;
- latency;
- queue depth;
- data quality;
- broker state;
- strategy health;
- audit/activity stream;
- redacted configuration status.

Configuration editing requires versioned, audited Admin APIs. Direct browser YAML editing is not permitted. Every privileged action displays its target scope and produces an audit record.

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

> **This phase is where the current work sits.** Amendment 2's decision was to freeze contracts before writing gateway code, so Phase 0 is the current phase and Phases 1+ do not start until it closes.

Backend must deliver:

- the `Principal` contract and OIDC/JWT tenant middleware;
- role/permission response;
- the common error envelope, reusing the frozen `ErrorCode` values;
- `schema_version` policy, pagination, and rate-limit contract (§7.2.1, §7.2.2);
- `openapi.json` snapshot, generated and CI-enforced (§11.1);
- market/analytics DTOs;
- news payload promoted to a frozen contract (§7.4.1);
- capability endpoint;
- health endpoint;
- tick-stream WebSocket contract, with the per-tenant pipe explicitly deferred (§7.7);
- trade preview/confirm;
- kill/resume;
- redacted configuration contract.

**Exit condition for Phase 0:** the spec is written, the contracts are frozen, the human has signed off, and **no gateway handler code has been written yet**. `AGENTS.md` §2 Steps 1–4

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

> **Narrowed by Amendment 2.** An earlier revision listed *"sequence tracking, replay, reconnect"* here. Those belong to the per-tenant gap-free pipe, which is **deferred** (§7.7.4). Phase 8 now delivers **ticks only**.

Deliver after the tick-stream contract is frozen:

- one multiplexed WebSocket per browser;
- topic subscribe and unsubscribe;
- tick frames as absolute latest value + per-topic `sequence`;
- discard-on-`sequence` regression, so reconnect is safe without a replay buffer;
- command palette and read commands.

Deferred out of this phase:

- per-tenant sequence tracking;
- server-side replay;
- gap-driven `RECONNECTING` state;
- trade/confirm command flow (blocked on M6).

### Phase 9 — Future intelligence

Deliver only after separate contracts:

- AI;
- calendar;
- research;
- backtests;
- alerts;
- advanced workspace persistence.

> **News moved out of this phase by Amendment 2.** News is no longer hypothetical: a working build-time pipeline and a rendered frontend section already exist. What remains is promoting the payload to a frozen contract and serving `GET /api/v1/news`. `financial_analyst_app/scripts/verify_m2.12_news_snapshot.py` `frontend/app/NewsSection.tsx`

---

## 11. Frontend Testing and Acceptance

Required tests:

- TypeScript typecheck;
- production Vite build;
- API-client contract tests;
- role and permission tests;
- Viewer/Trader/Admin visibility tests;
- 401/403/409/422 handling;
- token expiry and refresh;
- tenant isolation;
- WebSocket tick-frame tests, including sequence regression discard;
- stale/disconnected state tests;
- no-secret exposure tests;
- no-client-business-calculation tests;
- live confirmation safety tests;
- kill/resume safety tests;
- write idempotency tests;
- accessibility and keyboard tests;
- browser tests for chart, tables, and workspace interactions;
- human review of execution-critical screens.

> **Removed by Amendment 2:** *"WebSocket gap/replay/reconnect tests"*. Those test the deferred per-tenant pipe (§7.7.4), not the tick stream.

#### 11.1 OpenAPI as the contract of record — enforced

Earlier revisions listed OpenAPI compatibility as a required test but did not say what enforces it, and did not say whether the flow is even locked. Both are now explicit.

**The bible poses this as an open question, not a settled requirement:** *"Frontend `types` generation via `openapi.json` — lock this flow?"* `System_Development_Manual/modules/10_M10_Experience.md:135`

**Amendment 2 answers it: yes, lock it.** A hand-written frontend type can drift from the backend contract silently, and a drifting dashboard is worse than a missing one because it looks correct. Locking the flow makes the drift detectable.

- **`openapi.json` is the contract of record.** It is generated from the FastAPI app and committed. No frontend type is written by hand.
- **Frontend types are generated** from `openapi.json`, not transcribed. A hand-written type that does not match the spec is a defect.
- **CI enforces a snapshot diff.** Regenerating `openapi.json` and finding a difference fails the build. This is the mechanism that prevents silent contract drift, and it is the same discipline as the frozen-contract snapshot in `AGENTS.md` §5.
- **The generator runs in CI**, so a backend contract change that is not regenerated into the frontend types cannot merge.
- **No `openapi.json` exists yet.** It becomes a Tier-1 deliverable alongside the envelope, because until it exists there is no machine-checkable definition of the API.
- **The bible checkbox at `:135` should be marked resolved** when the M10.2 spec is approved, so the manual and this proposal agree.

The frontend is not complete when it merely renders cards. It is complete when every displayed state is traceable to a backend contract and every write follows the backend authorization and safety path.

---

## 12. Backend Integration Order

> **Replaced by Amendment 2.** The order below was previously written as *"M10.2 auth/session/tenant foundation → … → M10.1 → M4 → M5 → M6 → M7 → M10.3 → M10.4"*, which placed the gateway **first**. That contradicted recorded human directive **A65**, which places the API gateway **last**. The earlier chain is retained below as a **sub-mapping** so nothing is lost.

### 12.1 Governing order — A65, verbatim

> 9 steps (human): canonical instrument identity → shared timeframe registry → provider interface → requirement planner → chronological PIT retrieval → feature engine → strategy engine → validator → **API gateway LAST**. `shared/timeframes.py` exists (A35); provider interface + planner = the queued M3.1 slice above; **no step may be skipped (AGENTS §1)**.

`BUILD_STATUS.md:89` `USER_ACTIONS.md:239`

Current state against those nine steps:

| # | A65 step | State |
|---|---|---|
| 1 | Canonical instrument identity | **Complete** (M2b) |
| 2 | Shared timeframe registry | **Complete** (A35) `shared/timeframes.py` |
| 3 | Provider interface | **Complete** (M3.1 Provider Port, A69) `BUILD_STATUS.md:41` |
| 4 | Requirement planner | **Complete** (M3.1 Provider Port) `BUILD_STATUS.md:41` |
| 5 | Chronological PIT retrieval | **Complete** (M3.1 Provider Port) `BUILD_STATUS.md:41` |
| 6 | Feature engine | **Complete** (M3.1) `BUILD_STATUS.md:38` |
| 7 | Strategy engine | **Blocked** — M4.1 spec approved, tests approved, module absent; backend suite cannot complete collection. `BUILD_STATUS.md:42` |
| 8 | Validator | **Partial** — M4.3 approved. `BUILD_STATUS.md:45` |
| 9 | **API gateway** | **Not started. Must not begin before 7 and 8.** |

**Therefore the gateway is not the next thing to build.** Steps 7 and 8 are the current frontier. Designing and freezing the gateway contract is legitimate and is what §7 of this proposal now does; *implementing* it ahead of the strategy engine is not authorised by A65.

### 12.2 Sub-mapping — A65 steps onto gateway phases

The earlier chain is not discarded; it is re-expressed as the order of work **within** step 9, once steps 7 and 8 are complete.

| A65 step | Gateway work that depends on it | May start |
|---|---|---|
| 1–2 | `GET /api/v1/market/instruments` — reads canonical instrument identity and the timeframe registry | After A65 step 2 ✔ |
| 3–5 | `GET /api/v1/market/candles` — served **only** through chronological PIT retrieval, never by direct store access | After A65 step 5 ✔ |
| 6 | `GET /api/v1/analytics` — reads the frozen `AnalyticsResult` produced by the feature engine | After A65 step 6 ✔ |
| 7 | `GET /api/v1/signals` — reads validated signals from the strategy engine | After A65 step 7 |
| 8 | Signal quality, staleness, and rejection reasons surfaced in the response | After A65 step 8 |
| 9 | The gateway itself | Last |

### 12.3 Ordering within the gateway

Once A65 steps 7 and 8 are complete, the gateway is built in the tiers set out in §7.4.2, under the standing rules:

1. **Spec and contract freeze first** — no gateway code before the M10.2 spec and contracts are approved. This is the Amendment 2 decision.
2. Thin handlers only — each route validates input, resolves auth, resolves tenant, calls a module **through a frozen contract**, and returns the envelope. `System_Development_Manual/modules/10_M10_Experience.md:53`
3. One commit per endpoint group, each diff small enough to read line-by-line. `AGENTS.md` §3.11
4. Risk, execution, and ledger endpoints require **mandatory human review** regardless of test status. `AGENTS.md` §2.8

The exact order remains subject to the repository gate order and human approval. This proposal does not authorize backend implementation or frontend implementation by itself.

---

## 13. Open Amendments

This proposal is intentionally amendable. The following decisions require human review:

| Decision | Current proposal | Status |
|---|---|---|
| Authentication | OIDC / OAuth2 Authorization Code + PKCE; backend validates JWT and builds the `Principal` | **Decided 2026-09-27** (§4.1) |
| Identity provider | **Not named.** Issuer, audience, client id, JWKS policy are configuration | **Open — does not block the freeze** |
| Session shape | OIDC access token, not a bespoke session token | **Decided 2026-09-27** (§7.3) |
| `X-Role` header | Local demo only; ignored entirely once OIDC is configured | **Decided 2026-09-27** (§4.1) |
| Viewer private data | Public viewer cannot access private tenant data | Recommended |
| Role source | Backend-derived role only | Recommended |
| Role label | `Admin` is canonical; `Owner/Admin` is a deprecated alias | **Decided 2026-09-27** (§4) |
| Admin privilege | Highest application privilege across authorized tenants and system controls | Amended 2026-09-26 |
| Admin secrets | Secrets, vault contents, credentials, and tokens remain redacted | Amended 2026-09-26 |
| Account scope | MODE/AUTO scoped to user + broker | **Conflict — needs an M1.3 change proposal** (§14) |
| API error format | Frozen `ErrorCode` enum, `lower_snake`; the envelope is the single shape | **Decided 2026-09-27** (§7.2) |
| `BaseAppError.to_dict()` | Third error shape; must be aligned to the envelope or retired | **Open — must close before the first route** |
| `schema_version` | `patch` / `minor` / `major`, two versions supported, `Sunset` header | **Decided 2026-09-27** (§7.2.1) |
| Pagination | Cursor-based, `limit` 50 default / 500 max, opaque cursor, stable ordering | **Decided 2026-09-27** (§7.2.2) |
| Rate limiting | Server-side, `429` + `Retry-After`, client backs off with jitter | **Decided 2026-09-27** (§7.2.2) |
| API prefix | `/api/v1/...` | **Decided 2026-09-27** — already hardcoded; no longer open |
| CORS | Credentialed, locked origins; the `"null"` `file://` origin is removed with the legacy page | **Decided 2026-09-27** |
| `Idempotency-Key` | Required on **every** write, not only trade writes | **Decided 2026-09-27** (§7.3) |
| Health routes | `/api/v1/health`, `/api/v1/health/{module}` | Open |
| News route | `GET /api/v1/news`; payload must first be promoted to a frozen contract | **Added 2026-09-27 — contract gap** (§7.4.1) |
| Sessions route | `GET /api/v1/market/sessions` | Added 2026-09-27 |
| Trade preview | `POST /api/v1/trades/preview` | Open — blocked on M5/M6 |
| Trade confirmation | `POST /api/v1/trades/{id}/confirm` | Open — blocked on M5/M6 |
| Kill route | `POST /api/v1/risk/kill` | Open — blocked on M5 |
| Resume route | `POST /api/v1/risk/resume` | Open — blocked on M5 |
| Audit route | `GET /api/v1/audit` | **No contract exists** (§7.4.1) |
| Configuration route | `GET /api/v1/configuration` | **No `config_versions` contract** (§7.4.1) |
| Kill state read | `GET /api/v1/risk/kill` | **No `KillState` contract** (§7.4.1) |
| WebSocket route | **One multiplexed socket per browser**, topic subscribe, public tick topics | **Decided 2026-09-27** (§7.7.1) |
| WebSocket grammar | `ticks` frame carrying absolute latest value + per-topic `sequence` | **Decided 2026-09-27** (§7.7.2) |
| Tick topics and tenancy | `market.ticks.{instrument_id}.{timeframe}`, **no `user_id`** — market data is global | **Decided 2026-09-27** (§7.7.1) |
| Per-tenant gap-free pipe | Sequence, replay, `RECONNECTING` for private state | **Deferred by decision 2026-09-27** (§7.7.4) |
| Ledger contract | New frozen M7 contract required | Open — blocked on M7 |
| Configuration editing | Versioned Admin API, no direct YAML browser edit | Amended 2026-09-26 |
| Strategy control | Admin global; Trader approved strategies for own broker/account | Amended 2026-09-26 |
| Public analytics | Viewer allowed only for non-private/global data | Recommended |
| Future AI/news/backtests | Planned/read-only until separately contracted | Recommended |
| Trading workspace | One shared Trading Workspace; execution panel switches between MANUAL and AUTO | Amended 2026-09-26 |
| Environment state | `RESEARCH`, `PAPER`, and `LIVE` remain independent from MANUAL/AUTO | Amended 2026-09-26 |
| Automation ownership | Backend scheduler and M5/M6 decide firing; browser may only request arm/pause/disarm | Amended 2026-09-26 |
| Trader automation scope | Trader may arm approved strategies for own broker/account; Admin owns global controls | Amended 2026-09-26 |
| Command registry scope | Read-only; `/trade` and `/confirm` deliberately absent until M6 exists | **Recorded 2026-09-27** |
| Legacy vanilla-JS page | Being removed — superseded by the React app, and the only working integration | **Amended 2026-09-27** (§6.1) |
| Backend API location | `modules/experience/api/v1/` per bible; `app/main.py` is composition root only | **Confirmed 2026-09-27** |
| Frontend filename | This proposal is `FRONTEND_BACKEND_INTEGRATION_PROPOSAL.md` | Amendable |

---

## 14. Current Blockers

Before full frontend development, the repository needs:

1. A real authentication/session/tenant contract. **Partially resolved** — the mechanism is decided (§4.1); the `Principal` contract does not yet exist.
2. A standard frontend-consumable API error envelope. **Resolved in this revision** (§7.2); not yet implemented.
3. Frozen market-data and analytics API DTOs. The underlying contracts are frozen; the **API DTOs** are not.
4. A capability read endpoint. M3.0 is complete; the endpoint is not.
5. A health endpoint. Not built. Cheapest first win — see §7.4.2 Tier 1.
6. ~~A frozen M10.1 WebSocket sequence/replay contract.~~ **Amended 2026-09-27:** the per-tenant sequence/replay contract is **deferred by decision** (§7.7.4). What is now required before realtime work is a frozen **tick-stream** contract.
7. Frozen M5/M6/M7 API contracts for trading, risk, execution, ledger, and reconciliation. **Blocked** — those modules have not started.
8. A resolution for the kill/resume route conflict.
9. A resolution for the current process-global account-gate behavior versus per-user tenant behavior. `AccountGateService` holds a **single process-wide** mode and auto state, so it cannot represent two tenants. This is an M1.3 change proposal, not a gateway detail.

**Added as blockers by Amendment 2:**

10. **No `Principal` contract.** Nothing in `shared/contracts/` models an authenticated caller, so no handler can accept one as a typed input.
11. **No PIT query-serving read path.** `GET /api/v1/market/candles` must be served through chronological PIT retrieval, and no such read service exists yet.
12. **`.importlinter` tenant isolation is a placeholder.** The contract that would enforce *"all DB access goes through tenant context"* is self-described as un-enforced, and the gateway will depend on exactly that rule. `financial_analyst_app/.importlinter:17-19`
13. **The API location is unguarded by CI.** `scripts/check_structure.py` does not list `app/main.py` or `modules/experience/api/v1/` in its required set, so the API can move or disappear without any check failing.
14. **No import-linter contract governs `modules/experience`.** Nothing prevents a future gateway router from importing another module's internals instead of going through a frozen contract. `AGENTS.md` §5
15. **No news contract.** The payload the frontend already renders is unfrozen (§7.4.1).
16. **The identity provider is unnamed.** Does not block the contract freeze; blocks OIDC implementation.
17. **M4.1 is not implemented**, so `GET /api/v1/signals` has no producer, and the backend test suite cannot complete collection. A65 step 7. `BUILD_STATUS.md:42`
18. **After the legacy page is removed there is no working frontend-to-backend integration.** This is accepted knowingly: the page is a dependency-free vanilla-JS artifact superseded by the React app, and `GET /api/v1/account/status` remains available for direct verification. The integration is restored properly by the Tier-1 gateway work in §7.4.2, not by keeping a second frontend.

---

## 15. Proposal Status

**Open for amendment. Amendment 2 applied 2026-09-27.**

Amendment 2 resolved six decisions, withdrew one, narrowed one, added §7.2.1, §7.2.2, §7.4.1, §7.4.2, §7.7.1–§7.7.4, §11.1, and §12.1–§12.3, and **replaced §12**, which had placed the API gateway first in direct contradiction of recorded directive A65. Seventeen blockers are now recorded in §14, up from ten.

The three decisions that most constrain everything downstream:

1. **The gateway is last.** A65 step 9 of 9. Steps 7 (strategy engine) and 8 (validator) are the current frontier. `BUILD_STATUS.md:89`
2. **Errors have one dialect.** The frozen `ErrorCode` enum, `lower_snake`. The competing `UPPER_SNAKE` form is withdrawn.
3. **Ticks are public, full-replace, and per-topic sequenced.** The per-tenant gap-free pipe is deferred, and that reduction in promised scope is recorded rather than hidden.

This document may be revised as backend contracts are written, tests are approved, and human decisions are recorded. It must not be treated as a frozen specification until the human explicitly approves the relevant sections and `BUILD_STATUS.md` is updated.

**Created:** 2026-09-26
**Amended:** 2026-09-26 (Amendment 1), 2026-09-27 (Amendment 2)
**Owner:** Builder proposal for human amendment
**Related:** `frontend/specs/frontend_spec.md`, `System_Development_Manual/modules/10_M10_Experience.md`, `docs/specs/m3.1_feature_engine.spec.md`, `docs/specs/m2.9_hot.spec.md`, `docs/specs/m2.12_news_ingest.spec.md`, `docs/specs/m4.1_strategy_registry.spec.md`
