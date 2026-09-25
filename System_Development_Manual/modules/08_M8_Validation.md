# M8 — VALIDATION (Backtesting + Simulation + Paper + Canary — 4 Modes, 1 Team) — EXPANDED DISCUSSION
### Answers “Is it safe?” in four ways. All share Lake + fill models; none touches live brokers directly. Per-tenant validation.

**Module Type:** Stateful (runs/reports) — 4 modes, one team owns validation.
**Owns State:** `backtest_runs`, `simulation_reports`, `paper_state` (fake positions per tenant), `canary_state` per `user_id+broker_id`
**Input:** Historical `MarketData` (GLOBAL PIT Lake per `provider+instrument`, read `as_of T` — no `user_id`, AGENTS §4.3) OR live `MarketData` (paper, global ticks fanned out) + `M3-M6` code as *libraries* (not running services)
**Output:** `ValidationReport(Sharpe, maxDD, leakage pass/fail)` → promotion `shadow→canary→prod` per tenant

> **Expanded for M8** — each file simple + pseudocode + connection. This patches into master §17-§20.

---

## 1. Purpose in One Sentence
> **M8 proves a strategy works *before* real money. 4 lenses: history (backtest), stress (simulation), live fake money (paper), live tiny money (canary) — all reuse real `M3-M6` code, only venue changes.**


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution <500ms, tick→parquet <50ms, analytics <20ms, risk <10ms. No calc/file/abnormality tolerated — async I/O + sync pure calc + event_bus + frozen contracts + golden tests + well-commented WHY.*

## 2. The 4 Sub-Modules

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M8.1** | Backtesting | Replay Lake ticks → `ValidationReport` | Change fill model |
| **M8.2** | Simulation | Shock spread/vol/broker failure | Add shock |
| **M8.3** | Paper Trading | Live ticks → paper broker (per-tenant) | Change matching |
| **M8.4** | Canary | 1% live per-tenant, rollback | Change threshold |

## 3. M8.1 — BACKTESTING — History, No Leakage

### `backtesting/data/{data_engine, tick_replay, order_book_replay, data_suitability}`
**Does:** `data_engine` loads `data/lake/market-data/provider=deriv/instrument=FX.EURUSD/date=.../*.parquet` *time-ordered* (GLOBAL market data — **no `user_id=` in the path**); `tick_replay` emits ticks with realistic `exchange_timestamp`; `data_suitability` blocks `gapped` unless overridden.
**Pseudocode:**
```python
def replay(provider, instrument, start, end, as_of=None):
    # PIT: window resolved by Temporal Resolver; availability_time <= as_of (no look-ahead)
    window = temporal_resolver.resolve(f"{instrument}:{start}:{end}", as_of=as_of)
    parquets = pit.get_market_state(instrument=instrument, broker=provider, as_of=window.end)  # GLOBAL, no user_id
    for tick in sorted(parquets, key=lambda t: t.timestamp):
        if data_suitability.ok(tick): yield tick  # else skip gapped
```

### `execution_simulation/{virtual_broker, fill_model, slippage_model, latency_model, queue_position_simulator}`
**Does:** `virtual_broker` *pretends* to be broker: `market` fills at `mid±spread`, `limit` fills if `price crossed`;
 `slippage_model` estimates;
 `latency_model` adds `50ms` delay;
 `queue_position` simulates book queue for limit.
**Pseudocode:**
```python
def fill_model(order: Order, market: MarketData):
    if order.order_type=="market": return Fill(price=market.payload.price + spread, qty=order.qty)
    if order.order_type=="limit" and market.payload.price <= order.price: return Fill(price=order.price, qty=order.qty)
    return None  # not filled yet, stays in book
```

### `validation/{leakage_detector, bias_detector, walk_forward, reproducibility}`
**Does:** `leakage_detector` **must** fail if strategy reads `candle[t+1]` in backtest (lookahead). `bias_detector` checks survivorship/snooping. `walk_forward` does `train 2024-01→03, test 04`. `reproducibility` hashes `data+params+seed → same report`.
**Pseudocode:**
```python
def leakage_detector(strategy_code):
    # instrument: wrap AnalyticsResult to track access to future timestamp
    if strategy.accessed_future_candle: raise LeakageError("t+1")
```

### `performance/{performance_calculator, attribution, reports_generator}`
**Does:** `Sharpe, Sortino, maxDD, hit_rate` from `Ledger` (simulated per-tenant).

**How Backtest Reuses Live Code (critical):**
```
PIT Lake (GLOBAL `provider+instrument`, `as_of T`) → M8 tick_replay → M3 Analytics (real code) → M4 Alpha (real code) → M5 Risk (real code) → M6 Order (real code) → M8 virtual_broker (instead of real M6 broker_adapters) → M5 Portfolio (simulated) → M7 Ledger (simulated) → Report
```
**No M6 real broker.** If backtest and paper diverge → `fill_model` bug, not strategy.

---

## 4. M8.2 — SIMULATION — Stress (“What Breaks?”)

### `simulation/{monte_carlo, spread_shock, broker_failure, regime_variation, correlation_breakdown}`
**Does:** `spread_shock`: `spread 10× → ValidationReport`; `broker_failure`: broker offline mid-order → `unknown_state` handling; `regime_variation`: test trending strategy in `choppy` regime.
**Pseudocode:**
```python
def spread_shock(backtest_run, factor=10):
    for tick in backtest_run.ticks: tick.spread *= factor  # 1 pip → 10 pips
    return run_backtest(ticks_with_shock)  # Sharpe drops?
```

---

## 5. M8.3 — PAPER TRADING — Live Data, Fake Venue (Per-Tenant)

### `paper_trading/{paper_broker, matching_engine, account_state, execution_simulator}`
**Does:** Live `M2 ticks` (GLOBAL fan-out, one platform WS per broker → all tenants of that broker) → same `M3-M6` real code → `paper_broker.matching_engine` (mid±spread, `latency_simulator 50ms`, `rejection_simulator` random) → `paper_state` (fake positions per `user_id+broker_id`). `reconciliation` for paper *should always match* (tests pipeline).
**Pseudocode:**
```python
# Same as live, only venue changes:
# live:   Signal → M5 Risk → M6 Order → M6 broker_adapters/deriv → Deriv
# paper:  Signal → M5 Risk → M6 Order → M8 paper_broker → paper fill
```

---

## 6. M8.4 — CANARY — 1% Live Per-Tenant, Rollback

### `canary/{eligibility, exposure_limits, allowlists, monitoring, rollback}`
**Does:** `eligibility`: `backtest Sharpe>1.5 + paper Sharpe>1.2 → eligible`; `exposure_limits`: `1% capital, 3 symbols max` per tenant; `monitoring`: compare `canary Sharpe vs paper Sharpe`; `rollback`: one-click `disable canary strategy` per tenant.
**Pseudocode:**
```python
def eligible(strategy_id, user_id, broker):
    bt = backtest_report(strategy_id, broker)
    paper = paper_report(strategy_id, user_id, broker)
    return bt.sharpe>1.5 and paper.sharpe>1.2

def canary_order(order: Order, user_id, broker):
    order.qty = min(order.qty, exposure_limits.canary_max(user_id, broker))  # 1%
    return broker_adapters[broker].send(order)  # real broker, tiny size
```

---

## 7. Build Independence

```bash
pytest modules/validation --lake=parquet --mock-no-live-broker
# no live WS, no M2 live, no M6 real adapter — Lake replay only, per-tenant mocked
```



---

## Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P41** | M8.1 Backtesting | `tick_replay, virtual_broker, leakage_detector` — replays `MarketData → M3.0/M4.5 TradePlan → M5 → M6` same path, no leakage | `no leakage` |
| **P42** | M8.2 Simulation | `spread_shock, gap_shock, latency_injector` | `shock` |
| **P43** | M8.3 Paper | `paper_broker` (async, simulates fill with spread/slippage, well-commented WHY same M4.5→M5 path), fake money) — simulates `TradePlan` entry/SL/TP | `paper` |
| **P44** | M8.4 Canary | `canary_router` 1% live — `TradePlan` promoted shadow→canary | `canary` |

**After M2+M3+M7 (incl. M3.0/M4.5):** Validation replays `TradePlan` path (not Signal-only) — ensures `M4.5` logic is backtested. All 4 modes share `Lake` + `virtual_broker`.

## 8. Open Questions for M8

- [ ] Backtest tick vs 1m candle replay — default tick?
- [ ] Simulation Day 1 shocks: `spread 10x`, `vol 2x`, `broker_failure` top 3?
- [ ] Canary `1%/3 symbols/2 weeks` — finalize per tenant?
---
