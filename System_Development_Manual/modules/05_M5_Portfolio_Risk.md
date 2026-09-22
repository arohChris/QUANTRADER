# M5 — PORTFOLIO & RISK (Atomic) — EXPANDED DISCUSSION
### Owns positions + RiskDecision. The gatekeeper — if M5 says no, M6 never sees an Order. Per-tenant account calc, shared engine.

**Module Type:** Stateful — STRONGEST independent Module. Owns ledger pre-condition, atomic transaction.
**Owns State:** `portfolio_state` + `positions` + `risk_limits` + `risk_state` — ONLY writer, per `user_id+broker_id`, no other Module writes.
**Input:** `TradePlan` (from M4.5, per-tenant) + `PortfolioState` (self, per-tenant) + `AnalyticsResult` (vol/spread per-tenant)
**Output:** `RiskDecision(approved, qty, rejected_reason, trade_plan_id)` → M6 (M6 builds Order from TradePlan)

> **Expanded for M5** — each file simple + pseudocode + connection. This patches into master §11-§12.

---

## 1. Purpose in One Sentence
> **M5 answers: “Is this TradePlan (entry/SL/TP/timing) safe this tenant? How much?” — before any `Order` is built.** Same `M5` code serves `user 42@deriv` and `user 99@mt5`, but risk is per-tenant `user_id+broker_id`. **BEST APPROACH:** M5 now validates `TradePlan` (not raw Signal) — checks `valid_until, SL/TP existence, RR, data_quality, spread, margin`.


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution is mandatory <500ms order→ack, <50ms tick→parquet, <20ms analytics, <10ms risk. No calculation error, file handling error, or abnormality is tolerated — every I/O is async `async def` + `await` + `try/except + retry with jitter + dead-letter`, every pure calc is sync `def` + frozen `BaseContract` + golden test, every state change is event-driven via `event_bus` not poll. Code must be well-commented WHY, not just WHAT.*

## 2. The 5 Sub-Modules

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M5.1** | Portfolio State | Keeps `positions` (qty, avg entry) per tenant per broker | Change position logic |
| **M5.2** | Allocation | Target vs actual weights, capital per strategy | Change allocation |
| **M5.3** | Pre-Trade Risk | 10 checks in parallel — the gate | Change margin/exposure rule |
| **M5.4** | Sizing & Limits | `strength 0.7 → qty 0.4 lots` via `vol` | Change sizing |
| **M5.5** | Risk Ops | Post-trade, monitoring, kill_switch, asymmetric_lock | Change emergency |

**Atomic:** `M5.1 + M5.3 + M5.4` wrapped in one `UoW` per `TradePlan` → no partial `RiskDecision`. Best approach: TradePlan already validated for expiry/SL/TP before Risk.

---

## 3. M5.1 — PORTFOLIO STATE — The Ledger’s Pre-Condition

### `portfolio/portfolio_state.py`, `position_manager.py`
**Does:** Holds `positions: {EURUSD: {qty:0.7, avg_entry:1.08, uPnL:+12}}` per `user_id+broker_id`. **Receives `TradePlan.trade_plan_id` for audit** `positions.last_trade_plan_id`. `position_manager` on `M7` fill: `qty += fill.qty`, `avg_entry = (old_qty*old_avg + fill.qty*fill.price)/new_qty`.
**Pseudocode:**
```python
async def on_fill(fill: Fill, user_id, broker):  # async: DB per-tenant WHERE user_id+broker_id, atomic, retry, well-commented WHY avg_entry formula
    pos = db.get_position(symbol=fill.symbol, user_id=user_id, broker=broker)  # WHERE user_id+broker_id
    if pos:
        new_qty = pos.qty + fill.qty
        new_avg = (pos.qty*pos.avg_entry + fill.qty*fill.price) / new_qty if new_qty!=0 else 0
        pos.qty, pos.avg_entry = new_qty, new_avg
    else:
        pos = Position(symbol=fill.symbol, qty=fill.qty, avg_entry=fill.price, user_id=user_id, broker=broker)
    db.save(pos, user_id=user_id, broker=broker)  # tenant-scoped!
```
**Connects:** `M7` calls this on verified `ExecutionResult`; `M3` reads `PortfolioState` contract for `exposure` calc (read-only).

### `portfolio/portfolio_manager.py`
**Does:** CRUD for positions, handles `corporate_actions` (split).

---

## 4. M5.2 — ALLOCATION — The Budget

### `allocation.py`, `capital_allocator.py`, `exposure_manager.py`
**Does:** `strategy_allocation: {trend:30%, mean_rev:20%}` + `capital_allocator: {deriv:60%, mt5:40%}` → `M5.3` knows `risk_budget` for sizing. `exposure_manager` tracks `gross exposure per symbol per tenant`.
**Pseudocode:**
```python
risk_budget = capital_allocator.get(user_id, broker) * strategy_allocation["trend"]  # e.g., $1000 * 30% = $300
# M5.4 will do qty = risk_budget / (ATR * contract_size)
```
**Connects:** `M5.4 sizing` reads this; `M10 API` `GET /portfolio` reads this for UI pie chart.

---

## 5. M5.3 — PRE-TRADE RISK — The Gate (10 Checks, Parallel)

**Why many files:** Each check is `check(trade_plan: TradePlan, portfolio, analytics) → Pass/Fail` — parallel, no shared state, one file per rule. **All read `TradePlan.entry/stop/timing`** (new) not `Signal`.

### `pre_trade/margin_check.py` — THE FIX (was missing in amateur review)
**Does:** `required_margin = qty * contract_size * price / leverage`; `if available - required < min_free_margin → Reject("insufficient_margin")`.
**Pseudocode:**
```python
def check(trade_plan: TradePlan, proposed_qty, portfolio: PortfolioState, user_id, broker):  # sync pure: in-mem, no I/O, <10ms, well-commented WHY reject
    spec = reference.get_spec(trade_plan.instrument_id, broker)  # per broker
    entry_price = trade_plan.entry.reference_price  # from M4.5, not market_price guess
    # Also check SL distance vs ATR for sanity
    atr = analytics.features["atr_14"]
    if abs(entry_price - trade_plan.stop_loss.price) < 0.5 * atr:
        return Reject("stop_too_tight", details={"sl": trade_plan.stop_loss.price, "entry": entry_price})
    required = proposed_qty * spec.contract_size * entry_price / leverage
    available = portfolio.margin_available[broker]  # per-tenant WHERE user_id+broker_id
    if available - required < config.risk.limits.min_free_margin:
        return Reject("insufficient_margin", details={"required": required, "available": available})
    # Timing check (NEW — from TradePlan)
    if clock.now() > trade_plan.timing.valid_until:
        return Reject("trade_plan_expired", details={"valid_until": trade_plan.timing.valid_until})
    if trade_plan.data_quality != "VALID":
        return Reject("data_quality_invalid", details={"quality": trade_plan.data_quality})
    return Pass()
```
**Connects:** Uses `M2b` + `M3` (price) + `M5.1` (available). This is the correct home — not `M6 Order`.

### `exposure_check.py`, `leverage_check.py`, `concentration_check.py`  *(now read TradePlan)*
**Does:** `exposure_check`: `post_trade_exposure = current_exposure + proposed_qty*price`; `if > max_exposure (0.1 * equity) → Reject`. `leverage`: `if post_trade_leverage >10 → Reject`.
**Pseudocode:** `if portfolio.exposure["EURUSD"] + qty*price > limit: Reject("exposure_limit")`

### `liquidity_check.py`, `spread_check.py`, `data_quality_check.py`  *(now from TradePlan)*
**Does:** `liquidity`: `if proposed_qty > 10% * M3 tick_count proxy → Reject("liquidity_proxy")` — warns `PROXY_LIMITED`. `spread`: `if M3 spread > 5 pips → Reject("spread_too_wide")` — reads `TradePlan.entry` spread at creation. `data_quality`: `if TradePlan.data_quality != VALID → Reject` — M4.5 already stamped it.
**Connects:** Reads `M3` `volume`, `spread`, `quality`.

### `strategy_limit_check.py`, `broker_limit_check.py`, `order_limit_check.py`
**Does:** `strategy daily loss > limit → Reject`, `broker max orders/sec`, `order min_qty/step`.
**Connects:** `M2b` for `min_qty/step`.

**All 10 run in parallel, any Fail → `RiskDecision(approved=False)` (no partial).**

---

## 6. M5.4 — SIZING & LIMITS — How Much?

### `sizing/position_sizing.py`, `volatility_sizing.py`, `risk_budget.py`
**Does:** `M4` gave `strength=0.76` (not qty). `M5.4` does `qty = risk_budget / (ATR * contract_size)` where `ATR` from `M3` + `risk_budget` from `M5.2`, then `strength` scales it.
**Pseudocode:**
```python
def size(trade_plan: TradePlan, analytics: AnalyticsResult, portfolio: PortfolioState):  # sync pure: deterministic qty, clamp to spec, well-commented WHY risk_budget
    # strength still from original Signal (preserved in TradePlan provenance)
    strength = trade_plan.provenance.signal_strength  # 0.76
    risk_budget = allocation.get(trade_plan.strategy_id)  # $300 from M5.2
    atr = analytics.features["atr_14"]  # 0.0012 from M3
    spec = reference.get_spec(trade_plan.instrument_id, trade_plan.broker)
    # Use TradePlan's stop distance for risk-based sizing (better than ATR alone)
    sl_distance = abs(trade_plan.entry.reference_price - trade_plan.stop_loss.price)
    if sl_distance == 0: sl_distance = atr  # fallback
    raw_qty = risk_budget / (sl_distance * spec.contract_size)  # $300 / (0.0015*100000)=2.0
    qty = raw_qty * strength  # 2.0 * 0.76 = 1.52
    qty = sizing_constraints.clamp(qty, spec)  # min, step, max
    return qty
```
**Connects:** `M4` → `M5.4` → `M5.3` (margin_check uses proposed qty).

### `sizing_constraints.py`, `limits/*.py`
**Does:** `clamp` to `min_qty`, `qty_step`, `max_qty`, margin-aware.

---

## 7. M5.5 — RISK OPS — After & Emergency

### `post_trade/*`, `monitoring/margin_monitor.py`, `emergency/kill_switches.py`, `asymmetric_lock.py`
**Does:** `post_trade`: after `M7` fill, validate `exposure` still OK. `margin_monitor` (async poll every 5s via anyio, well-commented WHY 5s not 1s for DB load), if `margin < threshold → emergency_exit`. `kill_switches` (async, idempotent, audited): `POST /risk/kill_switch {scope: "global"|"strategy:trend"|"broker:deriv", user_id:42}` → idempotent, audited, stops new entries (but allows exits). `asymmetric_lock`: if `M7` reports `mismatch` for `EURUSD/deriv/user42`, `M5` blocks new `EURUSD/deriv` entries for `user42` but allows exits.
**Pseudocode:**
```python
def kill_switch(scope, user_id, broker=None):
    # scope = "broker:deriv" → for user 42, deriv only
    lock = asymmetric_lock.get(user_id, broker)
    lock.block_new_entries = True  # exits still allowed
    audit.log(f"kill_switch {scope} by {user_id}")
```
**Connects:** `M12 Ops` + `M10 API` trigger `kill_switch`; `M7` triggers `asymmetric_lock` event → `M5`.

---

## 8. How M5 Connects to Whole System

```
M4.5 TradePlan (per-tenant, with entry/SL/TP/timing) → M5.1 PortfolioState (per-tenant) + M5.2 Allocation → M5.4 Sizing (uses TradePlan.stop distance + M3 vol) → qty → M5.3 Pre-Trade (10 checks, validates TradePlan.valid_until/SL/TP/RR/quality/spread/margin) → RiskDecision(approved, qty, trade_plan_id) → M6 (builds Order from TradePlan)
                                                                                         ↓
                                                                                  M5.5 Risk Ops ← M7 asymmetric_lock event
```

**High-speed:** `M5.3` p95 <10ms (all in-mem, no I/O).



---

## Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P27** | M5.1 Portfolio State | `portfolio_state` (holdings per tenant, tracks `last_trade_plan_id`) | `on_fill` per tenant |
| **P28** | M5.2 Allocation | `allocator` (target weights, risk_budget per tenant) | `risk_budget` |
| **P29** | M5.3 Risk Checks | `risk_manager` 10 checks (validates `TradePlan` — `valid_until, SL/TP, RR, quality, spread, margin`) | `test_risk_rejects_expired_trade_plan` |
| **P30** | M5.4 Sizing | `position_sizer` `TradePlan.stop_distance → qty` | `strength* risk_budget` |
| **P31** | M5.5 Kill Switch ‖ M5.6 Fees | `kill_switch, asymmetric_lock` ‖ `fee_model` | `kill_switch` audit |

**After M4 (M4.5 TradePlan):** Starts after `M4.5 TradePlan`. `M5.3` gates `TradePlan` (not Signal) — test `test_risk_rejects_expired_trade_plan`.

## 9. How Risk Uses 3 Layers (Simple — Same as Everywhere)

> **Plain English:** Risk has `Automatic` (from your balance/equity/leverage) + `Your Override` (where allowed, e.g. max exposure 7% vs default 10%) + `Reset to Default`. Platform hard limit (e.g. max leverage 50) can never be overridden.

Example: `Automatic 10% → You set 7% → Effective 7% → You press Reset → back to 10%`.

## 10. Open Questions for M5

- [ ] Sizing default: `vol_target` vs `risk_budget`?
- [ ] `asymmetric_lock` threshold: `%` divergence or margin level?
- [ ] Exposure limit per symbol: single or per-asset-class?
---
