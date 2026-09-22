# M6 — EXECUTION (Order + Execution Merged) — EXPANDED DISCUSSION
### Builds broker-ready Order and talks to brokers. The ONLY module that talks to brokers for orders. Per-tenant orders, shared adapters.

**Module Type:** Stateful (order lifecycle) + I/O heavy. Per-tenant orders, shared broker adapters.
**Owns State:** `orders` + `order_state` (`pending→filled/unknown`) per `user_id+broker_id`, `child_orders`
**Input:** `RiskDecision(approved, qty, trade_plan_id)` + `TradePlan(entry/SL/TP/timing, per-tenant)` (from M5/M4.5)
**Output:** `ExecutionResult(fills, latency, slippage, trade_plan_id)` → M7 (links `TradePlan → Order → Fill`)

> **Expanded for M6** — each file simple + pseudocode + connection. This patches into master §13-§14.

---

## 1. Purpose in One Sentence
> **M6 turns `TradePlan(buy 0.4 EURUSD entry 1.085 SL 1.0835 TP 1.088 valid 60s)` + `RiskDecision(approved, qty)` into `Order(client_order_id=uuid5(trade_plan_id), qty 0.4, bracket SL/TP)` and gets it filled — retry-safe, idempotent, per-tenant.**


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution is mandatory <500ms order→ack, <50ms tick→parquet, <20ms analytics, <10ms risk. No calculation error, file handling error, or abnormality is tolerated — every I/O is async `async def` + `await` + `try/except + retry with jitter + dead-letter`, every pure calc is sync `def` + frozen `BaseContract` + golden test, every state change is event-driven via `event_bus` not poll. Code must be well-commented WHY, not just WHAT.*

## 2. The 5 Sub-Modules

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M6.1** | Order Core | Builder, validator, state_machine, idempotency | Change order lifecycle |
| **M6.2** | Routing | Pin or choose venue (`smart_router`) | Add routing rule |
| **M6.3** | Connectivity | WS/REST/FIX transports | Add FIX |
| **M6.4** | Broker Adapters | Deriv/MT5/Headway translators (per-broker parallel) | Add broker |
| **M6.5** | Execution Intelligence | Algos (market/limit/twap), costs, metrics | Add TWAP |

## 3. M6.1 — ORDER CORE — The Lifecycle Owner

### `order/order_builder.py`
**Does:** `RiskDecision(approved, qty=0.4) + Signal → Order(order_id=uuid4, client_order_id=uuid5(correlation_id), symbol, side, qty, state=pending, broker_capability_checked=True)`. `client_order_id` deterministic → retry same signal yields same ID → broker dedupes.
**Pseudocode:**
```python
def build(risk: RiskDecision, trade_plan: TradePlan, user_id, broker):  # sync pure: deterministic client_order_id uuid5(trade_plan_id), well-commented WHY idempotent
    # BEST APPROACH: Order from TradePlan, not Signal — preserves entry/SL/TP
    cid = str(uuid5(NAMESPACE, str(trade_plan.trade_plan_id)))  # deterministic per TradePlan
    order = Order(order_id=uuid4(), client_order_id=cid, symbol=trade_plan.instrument_id, 
                  side="buy" if trade_plan.direction=="long" else "sell",
                  qty=risk.approved_qty, order_type=trade_plan.entry.type, price=trade_plan.entry.reference_price,
                  stop_loss=trade_plan.stop_loss.price, take_profit=trade_plan.take_profit.price,
                  state="pending", user_id=user_id, broker=broker, trade_plan_id=trade_plan.trade_plan_id)
    return order
```

### `order_validator.py`, `order_capability_check.py`
**Does:** `validator` checks `qty % step ==0`, `qty >= min_qty` via `M2b`; `capability_check` checks `order_type in broker_capabilities[broker][symbol]` (e.g., `Volatility 75` Deriv has no `stop`).
**Pseudocode:** `if qty % spec.qty_step !=0: Reject("qty_step")`; `if order_type not in cap: Reject("capability_not_supported")`

### `state_machine.py` — ONLY Owner of Transitions
**Does:** Valid: `pending→submitted→acked→partially_filled→filled`, `pending→rejected`, `any→unknown`, `unknown→filled/rejected` (via M7). Invalid: `filled→cancelled` → error.
**Pseudocode:**
```python
TRANSITIONS = {
    "pending": ["submitted","rejected","unknown"],
    "submitted": ["acked","rejected","unknown"],
    "acked": ["partially_filled","filled","cancelled"],
}
def transition(order, new_state):  # sync pure: valid transitions map, no I/O, well-commented WHY unknown→filled allowed
    if new_state not in TRANSITIONS[order.state]: raise IllegalTransition
    order.state = new_state; db.save(order)  # WHERE user_id+broker_id
```

### `idempotency.py`, `provenance.py`, `partial_fill_manager.py`
**Does:** `idempotency` stores `client_order_id → Order hash` — resubmit same `cid` returns existing Order, no duplicate. `provenance` links `Order → RiskDecision → Signal → Analytics → MarketData watermark` for audit. `partial_fill_manager` tracks `fill 0.2/0.4 → partially_filled`, decides when to close.
**Pseudocode:** `if db.exists(client_order_id): return existing # not resubmit`

---

## 4. M6.2 — ROUTING — Where to Send?

### `routing/router.py`, `smart_router.py`, `routing_policy.py`
**Does:** `router` (sync pure, deterministic): `if TradePlan.broker=="deriv" → deriv` (pin). `smart_router` (sync pure, deterministic, well-commented WHY cheapest venue): `for symbol=EURUSD canonical, pick cheapest {deriv, mt5} by spread+liquidity+commission` from `M3` + `M2b execution_profiles` + `M6.5 costs`.
**Pseudocode:**
```python
def route(order: Order, user_id):
    if order.broker: return order.broker  # pin
    # smart: choose per-tenant per-symbol
    costs = {b: transaction_costs.estimate(order.symbol, b) for b in ["deriv","mt5"]}
    spreads = {b: analytics.spread(order.symbol, b) for b in costs}
    return min(costs, key=lambda b: costs[b] + spreads[b])
```
**Connects:** Reads `M3 micro` `spread`, `M2b` `execution_profiles`, `config/execution/routing.yaml` (`EURUSD: primary mt5, fallback deriv`).

---

## 5. M6.3 — CONNECTIVITY — The Transports (No Trading Logic)

### `connectivity/interface.py`, `websocket/`, `rest/`, `fix/`, `dma/`
**Does:** `interface` says `send_order(order) → broker_ack`, `cancel(order_id)`, `get_status(client_order_id)`. `websocket/client` handles Deriv WS, `rest/client` MT5 REST, `fix/session` handles FIX `35=D` (New Order), `sequence_manager` tracks `34=123` gaps.
**Pseudocode (WS):**
```python
async def send_order(order: Order, user_id, broker):  # async: per-tenant vault token, WS send, await ack, timeout, retry, well-commented WHY idempotency
    ws = vault.get_ws(broker, user_id)  # per-tenant token
    await ws.send({"action":"order", "client_order_id":order.client_order_id, "symbol": to_native(order.symbol, broker), "qty":order.qty})
    ack = await ws.recv(timeout=5s)
    return ack
```
**Connects:** `M6.1` calls `connectivity.send_order`, `M1.4 vault` provides per-tenant token.

---

## 6. M6.4 — BROKER ADAPTERS — The Thin Translators (Per-Broker Parallel, ISOLATED)

### `broker_adapters/deriv/{client,execution,models}`, `mt5/...`, `headway/...`
**Does:** Translates `Order(EURUSD, buy, 0.4, market)` → `Deriv native {"symbol":"R_75","buy":0.4}` and `Deriv fill {"price":1.08,"qty":0.4}` → `Fill(price=1.08, qty=0.4)`. **No sizing, no risk.** One folder per broker, no cross imports.
**Pseudocode (Deriv):**
```python
# execution.py
def to_broker(order: Order):
    return {"symbol": reference.to_native(order.symbol, "deriv"),  # EURUSD → frxEURUSD
            "amount": order.qty, "order_type": order.order_type, "client_order_id": order.client_order_id}
def from_broker(broker_fill):
    return Fill(fill_id=broker_fill["id"], price=broker_fill["price"], qty=broker_fill["qty"])
```
**Connects:** `M6.3` sends via `client`, `M6.1` state_machine validates result.

---

## 7. M6.5 — EXECUTION INTELLIGENCE — How to Work the Order

### `algorithms/{base,market,limit,twap,vwap,iceberg,order_slicer}`, `market_impact/`, `transaction_costs/`, `execution_metrics/`
**Does:** `market` → immediate, `twap` → slice `0.4` into `4×0.1` over `4m`, `order_slicer` splits parent → child orders, `market_impact` estimates `temp impact = k*sqrt(qty)`, `transaction_costs` sums `spread+commission+slippage+financing`, `metrics` tracks `latency, slippage, fill_quality`.
**Pseudocode (TWAP):**
```python
class Twap(BaseAlgo):
    def execute(self, order: Order):
        slices = slicer.slice(order, num=4, interval="1m")  # 0.4 → 4×0.1
        for child in slices:
            fill = broker.send(child); tracker.add(fill)
            await sleep(60)
        return ExecutionResult(fills=tracker.fills, avg_price=tracker.avg)
```
**Connects:** `config/execution/algorithms.yaml` picks algo; `M6.2` may choose `twap` for large `qty`.

---

## 8. How M6 Connects to Whole System

```
M5 RiskDecision(approved, qty, trade_plan_id, per-tenant) + M4.5 TradePlan(entry/SL/TP) → M6.1 Order Core (build bracket Order per-tenant) → M6.2 Routing → M6.3 Connectivity (WS/REST per-tenant token) → M6.4 Broker Adapter (translates entry+SL/TP) → Broker → Fill → M6.5 Algos/Metrics → ExecutionResult(trade_plan_id) → M7 Trust (links TradePlan→Order→Fill)
```

**Idempotency example:** `Signal 123 → M6.1 client_order_id=uuid5(123)=abc` → broker timeout → retry same `Signal 123` → same `abc` → broker returns `duplicate → existing fill` not new order.

**Build Independence:**
```bash
pytest modules/execution --order=RiskDecision --mock-broker
# mocks BrokerAdapter returns fake Fill, no M5/M7 needed, per-tenant isolation mocked as dict
```



---

## Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P32** | M6.1 Order Builder | `order_builder, state_machine, idempotency uuid5(trade_plan_id)` — builds `Order(entry, SL, TP)` from `TradePlan` | `Order` has `trade_plan_id` |
| **P33** | M6.2 Router | `router, smart_router` | `venue` chosen |
| **P34** | M6.3 Connectivity | `ws_client, rest_client, fix_client` per-tenant token | `reconnect` |
| **P35** | M6.4 Broker Adapters | `deriv_adapter` first, then `mt5, headway` — translates `TradePlan` SL/TP to native | `Deriv` fill |
| **P36** | M6.5 Algos | `twap, market_impact, slippage_model` | `TWAP` |

**After M5 (P31):** Execution starts after `M5` approves `TradePlan`. `M6.1` now builds bracket `Order` (`entry+SL+TP`) from `TradePlan` (not Signal). `M6.4` per-broker diff preserved.

## 9. Open Questions for M6

- [ ] Routing Phase 1: `pin` (`Signal.broker` pass-through) vs `smart_router` (spread-based)?
- [ ] Algos: `market+limit` only or also `twap` Day 1?
- [ ] FIX needed now or WS/REST sufficient?
---
