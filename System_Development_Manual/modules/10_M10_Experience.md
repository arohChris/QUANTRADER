# M10 — EXPERIENCE (Delivery — Synchronization + API + Command + Frontend) — EXPANDED DISCUSSION
### Publishes state, serves API, interprets commands, renders UI. Per-tenant projection, no trading logic.

**Module Type:** Delivery — owns the *view*, not the state. Per-tenant topics + per-tenant API auth.
**Owns State:** `subscription_state`, `sequence` per tenant per topic, `command_queue`
**Input:** Read projections: `PortfolioState`, `Order`, `ExecutionResult`, `Health` — via `query_models` + events, filtered by `user_id+broker_id`
**Output:** WS `Snapshot` + `Delta` (seq + Patch) + REST JSON + Command responses (per-tenant)

> **Expanded for M10** — each file simple + pseudocode + connection. This patches into master §22+§27+§28+§32.

---

## 1. Purpose in One Sentence
> **M10 shows `user 42/Deriv` *their* portfolio *only*, lets them type `/forex EURUSD` or `GET /portfolio`, and forwards `POST /trade` through `M5-M6` — never calculates RSI/margin itself.**


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution <500ms, tick→parquet <50ms, analytics <20ms, risk <10ms. No calc/file/abnormality tolerated — async I/O + sync pure calc + event_bus + frozen contracts + golden tests + well-commented WHY.*

## 2. The 4 Sub-Modules

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M10.1** | Synchronization | WS pub/sub, sequence, replay (per-tenant) | Add topic |
| **M10.2** | API | 18 thin routers (async FastAPI, no business logic, well-commented WHY thin) (`GET /portfolio`) per-tenant | Add endpoint |
| **M10.3** | Command | Parser + 30 commands (`/forex`, `/trade`) per-tenant | Add command |
| **M10.4** | Frontend | Features, realtime, state — per-tenant view | Add UI |

### M10.1 — Synchronization — The Gap-Free Pipe (Per-Tenant)

#### `synchronization/event_publisher.py`, `subscription_manager.py`, `state_snapshot.py`, `state_delta.py`, `sequence_manager.py`, `replay_manager.py`
**Does:** `event_publisher` on `M5` fill → `publish("user_42:portfolio.deriv", delta={positions: [...]})` with `seq=1001`. `subscription_manager` tracks `user 42 subscribed to portfolio.deriv`. `state_snapshot` on connect: full `PortfolioState`. `state_delta` thereafter: `Patch(add: [...])`. `sequence_manager` monotonic `seq` per `user_id:topic`. `replay_manager` on reconnect with `last_seq=1001` → replays `1002..current`.
**Pseudocode:**
```python
# Publisher (backend)
def on_fill(fill, user_id, broker):
    seq = sequence_manager.next(f"{user_id}:{broker}:portfolio")
    delta = state_delta.diff(old_portfolio, new_portfolio)  # JSON Patch
    redis.publish(f"{user_id}:{broker}:portfolio", {"seq":seq, "patch":delta})
    # Frontend already subscribed

# Frontend reconnect
#   ws.send({"subscribe":"portfolio.deriv", "last_seq":1001})
#   server: replay_manager.replay(f"{user_id}:deriv:portfolio", from_seq=1002)
```
**Connects:** `M5-M7` emit events → `M10.1` → `M10.4 realtime` + `M12` monitoring. **Per-tenant topic** `user_42:deriv:portfolio` vs `user_42:mt5:portfolio` — Deriv user never receives MT5 topic.

---

### M10.2 — API — The Thin Translators (Per-Tenant Auth)

#### `api/v1/{router, market_data, analytics, portfolio, risk, orders, executions, health}.py`
**Does:** Each handler: `auth (user_id from JWT) → tenant_context.set(user_id, broker) → call Module via contract → return JSON + correlation_id header`. ≤30 lines, no business logic.
**Pseudocode:**
```python
@router.get("/portfolio")
def get_portfolio(request, broker: str = Query(...)):
    user_id = request.user.id  # from JWT
    tenant_context.set(user_id, broker)  # Deriv or MT5 — user picks
    state = portfolio_risk.get_state(user_id=user_id, broker=broker)  # M5, WHERE user_id+broker_id
    return state.to_json()  # PortfolioState contract
```
**Connects:** `M10.2` → `M5/M6/M7` via contracts (read), `M1.4 permissions` checks `can(user, "portfolio.read")`. `Deriv` user calling `GET /portfolio?broker=mt5` would still work but only if user *has* MT5 account — otherwise `M5` returns empty (no rows for `user 42+mt5`).

---

### M10.3 — COMMAND — The Chat Interface (Per-Tenant)

#### `command/{parser, interpreter, registry, permissions, queue_manager} + commands/{marketday, forex, risk, trade, confirm, ... 30 files}`
**Does:** `parser` `"/forex EURUSD"` → `intent=forex, params={symbol:EURUSD}`; `interpreter` resolves to `forex.py`; `registry` `@register_command("forex")`; `permissions` checks `can(user, "command:forex")`; `queue_manager` for long `deep` analysis → `job_id` + poll.
**Pseudocode:**
```python
# commands/forex.py (THIN, read-only except trade/confirm)
@register_command("forex")
def forex(params, user_id, broker):
    tenant_context.set(user_id, broker)  # Deriv or MT5
    candles = data.get_candles(symbol=params.symbol, broker=broker, user_id=user_id)  # per-tenant
    analytics = analytics.get_features(candles)  # M3, shared calc
    return {"symbol": params.symbol, "rsi": analytics.features["rsi_14"]}

# commands/trade.py + confirm.py are the ONLY writes:
#   trade → creates pending trade, asks "confirm?"
#   confirm → tenant_context.set(...) → M5 Risk → M6 Execution (real trade, per-tenant)
```
**Connects:** `M2` Lake, `M3` Analytics, `M5` Risk — all per-tenant via `tenant_context`. `M10.3` never writes DB directly.

---

### M10.4 — FRONTEND — The Face (Per-Tenant View Only)

#### `frontend/{features/*, realtime/*, state/*, services/api_client, components/*, types/}`
**Does:** `features/portfolio/` shows `PortfolioState` for `user 42/Deriv` only; `realtime/websocket_client` subscribes to `user_42:deriv:portfolio` + `sequence_tracker` checks `seq==expected` else `reconnect_manager` replays; `state/portfolio` holds client state; `services/api_client` adds `Authorization: Bearer <JWT>` + `X-Broker: deriv`; `types/` generated from `openapi.json` (never hand-written).
**Pseudocode (realtime):**
```javascript
// services/websocket
ws.on("open", () => ws.send(JSON.stringify({subscribe: "portfolio", broker: "deriv", last_seq: state.lastSeq})))
ws.on("message", ({seq, patch}) => {
  if (seq !== state.lastSeq + 1) ws.send({reconnect: true, last_seq: state.lastSeq})
  else applyPatch(patch)
})
```
**Connects:** `M10.1` WS topics + `M10.2` REST — both per-tenant. **Never calculates RSI/margin** — displays `AnalyticsResult` + `RiskDecision` from API.

**Build Independence:**
```bash
pytest modules/experience --mock=PortfolioState --expect=WsDelta
# no trading logic, only projection + sequence tests, per-tenant mocked
```



---

## 4. How Settings Use 3 Layers (Simple)

> **Plain English:** Your dashboard settings (allowed assets, alerts, dashboard prefs) follow same `Automatic → Your Override → Reset`. Same pattern as Risk.

## 5. Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Module | Sub-Module | Gate |
|-------|--------|------------|------|
| **P48** | M10.1 | Synchronization — `event_publisher (async, per-tenant seq, retry, well-commented WHY seq), sequence_manager (sync pure)` (publishes `TradePlan` status `PENDING_RISK→APPROVED`) | `seq` per tenant |
| **P49** | M10.2 | API — 18 thin routers (async FastAPI, no business logic, well-commented WHY thin) (`GET /trade_plans/{id}`) | `thin router` |
| **P50** | M10.3 | Command — `parser, registry` + first 10 commands | `parse` |
| **P51** | M10.4 | Frontend — `features/*, realtime/*` (shows `entry/SL/TP` from `TradePlan`) | `realtime` |

**Parallel after M5-M7 (incl. TradePlan):** Experience shows `TradePlan` (not naked Signal) in UI — `entry/SL/TP` auditable per `user_id+broker_id`.

## 3. Open Questions for M10

- [ ] WS transport: Redis Pub/Sub now → Kafka later — abstract via `services/messaging`?
- [ ] Commands: Phase 3 first 10 (`marketday, forex, risk, trade+confirm`) → add others later?
- [ ] Frontend `types` generation via `openapi.json` — lock this flow?
---
