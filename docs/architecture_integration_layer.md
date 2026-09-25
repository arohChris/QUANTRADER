# Architecture: Integration Layer — Queries In, Streams Out, Broker Gateway

> **Status: DESIGN 2026-09-25** (human-directed: "design our logic that will later talk to the
> frontend… and talking to Deriv endpoints for account info and trade executions").
> **This is a design document, not a spec** — no code is authorized by it; each slice still needs
> AGENTS §2 (spec → tests → LGTM → code). Pattern precedent: `docs/architecture_analysis_layer.md`.
> Sources of truth: bible §3 canonical tree, `System_Development_Manual/modules/{06,10,11}_*.md`,
> `frontend/FRONTEND_BACKEND_INTEGRATION_PROPOSAL.md` §7 (proposal — not frozen), AGENTS gates.

## 1. Scope — three planes, one correlation_id

```text
 FRONTEND (browser only)                    BACKEND (financial_analyst_app/)
 ┌───────────────────┐   queries    ┌───────────────────────────────────────────┐
 │ services/*_api.ts │ ───────────▶ │ QUERY PLANE  modules/experience/api/v1/  │
 │ realtime/ (seq)   │ ◀─────────── │ STREAM PLANE modules/experience/         │
 └───────────────────┘   updates    │   synchronization/ + WS bridge            │
                                    │ CHAIN  M3 analytics → M4 signal → M5 risk│
                                    │ GATEWAY modules/execution/ + deriv feed  │
                                    └────────────┬──────────────────────────────┘
                                                 │ private token (vault) / platform token
                                                 ▼
                                    Deriv endpoints: account info + trade execution
```

One `correlation_id` threads every plane: query → (optional) stream topic → broker call → ledger
entry, so any trade can be reconstructed from logs without asking anyone (AGENTS §3.12).

## 2. Query plane — receiving frontend queries

- **Home:** `modules/experience/api/v1/` (extracted 2026-09-25 — `router.py` account gates are the
  first residents). Thin law: validate → service → JSON. **No business math, no strategy, no risk
  decisions in routers** — they call M5/M3/M7 services via frozen contracts.
- **Envelope (freeze at M10.2):** `{data, meta{correlation_id, as_of, schema_version, sequence,
  next_cursor}, error{code, retryable, details}}`. Frontend consumes stable `error.code` — never
  FastAPI `detail` strings. Today's account routes predate the envelope (spec-frozen responses);
  envelope lands as an additive middleware when M10.2 session slice opens.
- **Auth progression:** `X-Role` (local demo only, today) → `GET /api/v1/session` returning
  `{authenticated, user_id, role, permissions, available_brokers, active_broker, tenant_scope}`.
  Backend derives identity from validated session/JWT on **every** request; frontend may hide UI by
  role but **re-authorization is always server-side**.
- **Tenant scope:** private queries run `WHERE user_id + broker_id` (M1.4 ContextVar). Viewer gets
  public/global data only — never portfolio/orders/risk/ledger.
- **Endpoint inventory:** proposal §7.4 table is the working list. Each row freezes (OpenAPI
  snapshot published) **before** the frontend wires it; frontend CI asserts its TS types against the
  snapshot so drift fails CI, not production.
- **Writes:** carry backend-issued `Idempotency-Key` (correlation/plan id) — retries reuse it.

## 3. Stream plane — streaming market updates back

- **Source chain:** M2.1 Deriv feed (per-broker platform singleton, platform token) → normalize →
  M2.9 HOT (latest + forming candles) and lake history → publishers. Frontend never reads these
  stores directly.
- **Bus (M10.1, currently parked — A24):** in-process `event_publisher(seq)` →
  `subscription_manager` → `state_snapshot` (connect-time) / `state_delta` → `replay_manager`.
  Spec: `frontend/specs/m10.1_synchronization.spec.md`.
- **WS bridge (M10.2+):** one authenticated per-tenant endpoint; envelope
  `{type: snapshot|delta|replay|heartbeat|error, topic, sequence, as_of, schema_version,
  correlation_id, payload}`.
- **Sequence law (client + server):** snapshot on subscribe → accept only next contiguous sequence
  → on gap **stop applying**, request replay from `last_seq+1` → show `RECONNECTING/STALE` until
  caught up → never mix snapshots. Server side re-checks role per subscription (hiding ≠ authz).
- **Topics:** public `market.candles.{instrument}.{tf}`, `capabilities`, `health`; private
  `portfolio.*`, `risk.*`, `orders.*`, `fills.*` (tenant-scoped, never delivered cross-tenant).
- **Forming vs closed:** stream payloads carry `lifecycle` + `quality` + `availability_time`; UI
  renders forming/analytics as provisional (badges) — never as final (AGENTS §3.4 / frontend law).
- **Retention:** streams derive from HOT + candle + Decision lineage; **no per-tick AnalyticsResult
  cold writes** (`check_retention.py`).

## 4. Broker gateway — Deriv endpoints for account info + trade execution

- **Two channels, two tokens (M2.4 vault law):**
  | Channel | Token | Used by | Exists today? |
  |---|---|---|---|
  | Public market feed | platform `app_id` (`config/brokers/deriv.yaml`) | M2.1 | yes |
  | Private account + trading | per-user `vault.get(user_id, "deriv")` | M6/M7/M5 only | no (M6 later) |
  The **browser never holds a broker token**; frontend talks only to our API.
- **Home:** `modules/execution/` per bible §3 — `execution/connectivity/{ws,rest}/`,
  `execution/broker_adapters/deriv/{client,execution,models}/`. **Only the adapter** knows Deriv
  native (`EURUSD → frxEURUSD`, proposal payloads) — core M6 never string-builds broker symbols.
- **Account info path:** Deriv `balance`/`portfolio`/`open_trades` calls via gateway → typed
  contracts → M7 reconciliation compares broker state vs ledger; after any outage, **broker state is
  reconciled before trading resumes** (AGENTS §3.9). Frontend renders via `portfolio_api`/`ledger_api`.
- **Execution path (state machine, at-least-once):**
  ```text
  TradePlan → M5 RiskDecision(approved)            # structural gate — unreachable if rejected
    → M6 Order{client_order_id = uuid5(correlation)} # idempotent by construction
    → deriv adapter translate (no sizing, no risk)   # broker-native only here
    → send → ambiguous response? GET order status BEFORE retry   # never assume
    → ack → partially_filled → filled | rejected | cancelled     # explicit, logged, typed
    → Fill → M7 Ledger (Decimal, append-only)        # hand-calc parity at G1
  ```
  Concurrent sends per `(user_id, broker, instrument)` serialized by row lock. `429` → exponential
  backoff + jitter. Capability interlock: M3.0 gate rejects unsupported order types pre-trade.
  Kill switch + live-arm + MODE/AUTO re-checked server-side (never from client claims).
- **GATE LAW:** no M6 execution **code** before **G0 exit** (out-of-sample expectancy > 0 after
  fees, recorded in `BUILD_STATUS.md`). This design does not authorize it.

## 5. Build order vs design (what happens when)

| Now (authorized) | After current slice | After G0 / gates |
|---|---|---|
| M3.1 Feature Engine (A36 → Step 5) | M3.2 indicators → M4.1 registry (P22) | M6 gateway code (G0 exit) |
| Frontend M10.4.x (developer, hand-off `frontend/HANDOFF.md`, mock seam) | M10.2 session + envelope (frontend Phase-2 dependency) | WS bridge live (M10.1 un-parked, A24) |
| M10/M11 folders extracted; this design | m2.10 resume → §5 ws_url → m2.11 (existing queue) | M5/M7 contracts before trading UI (proposal phases 6–7) |

## 6. Folder homes (post-extraction 2026-09-25)

```text
financial_analyst_app/
  app/main.py                    # composition root ONLY (CORS + singleton + include_router + uvicorn)
  modules/experience/            # M10: api/v1/router.py (extracted) + synchronization/ + command/ (future)
  modules/intelligence/          # M11: deferred, zero code, README only (never trades)
  modules/execution/             # M6: future home of gateway + deriv adapter (post-G0)
  shared/core/{account_mode,permissions}.py  # STAYED — M1.3 foundation (bot + API + commands use them;
  #                                           spec'd location, structure guard requires shared/core/permissions.py)
frontend/                        # browser-shipped code only (+ frontend/tests/ its tests, HANDOFF.md)
```

## 7. Open decisions (human freezes before implementation)

1. Envelope adoption + `ErrorCode` mapping for HTTP (proposal §7.2 Open).
2. Session mechanism: server session vs JWT (proposal §13 Open).
3. WS route + per-tenant subscription auth (proposal §13 Open).
4. Kill/resume route conflict resolution (proposal blocker #8).
5. Process-global vs per-user MODE gate (proposal blocker #9 — must fix before session slice).
6. A24: un-park M10.1 synchronization (prerequisite for the whole stream plane).
