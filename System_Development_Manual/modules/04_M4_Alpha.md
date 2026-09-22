# M4 — ALPHA (Strategy + Signal Merged) — EXPANDED DISCUSSION
### The ONLY place alpha lives. Proposes intent, gates it, emits Validated Signal. Per-tenant intent, shared logic.

**Module Type:** Stateful — Strategy registry + Signal state_machine. Per-tenant signals, shared strategy code.
**Owns State:** `signal_store` (per user_id+broker_id) + `strategy_registry` (which families enabled)
**Input:** `AnalyticsResult` + `MarketData` (per-tenant)
**Output:** `TradePlan(entry/SL/TP/timing, trade_plan_id, per-tenant)` via `M4.5` → M5 (Validated Signal is internal `M4.4→M4.5` only)

> **Expanded for M4** — each file simple + pseudocode + connection. This patches into master §9-§10.

---

## 1. Purpose in One Sentence
> **M4 converts math (`RSI 72`) into intent (`EURUSD LONG strength 0.76, valid 60s`) and proves the intent is clean before risking money.** No sizing, no broker, no order — just *should we?* and *how strongly?*


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution is mandatory <500ms order→ack, <50ms tick→parquet, <20ms analytics, <10ms risk. No calculation error, file handling error, or abnormality is tolerated — every I/O is async `async def` + `await` + `try/except + retry with jitter + dead-letter`, every pure calc is sync `def` + frozen `BaseContract` + golden test, every state change is event-driven via `event_bus` not poll. Code must be well-commented WHY, not just WHAT.*

## 2. The 5 Sub-Modules — BEST APPROACH (M4.5 Trade Plan Inside)

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M4.1** | Strategy Registry | Base class, discovery, versioning, shadow_mode | Add new strategy family |
| **M4.2** | Strategy Families (11, Tier 1 first) | 11 families, each `class Strategy(BaseStrategy)` → `Signal` | Add/change a family |
| **M4.3** | Signal Validation | Checks tradable, dedup, freshness, latency | Change freshness window |
| **M4.4** | Signal Orchestration | Conflict, priority, provenance, state_machine | Change prioritization |
| **M4.5** | **Trade Plan (NEW — BEST APPROACH)** | `entry/stop/target/timing` → `TradePlan` contract, auditable SL/TP | Change entry/SL/TP model |

## 3. M4.1 — STRATEGY REGISTRY — The Framework

### `strategy/base_strategy.py` — The Template
**Does:** Says every strategy MUST implement `generate(analytics: AnalyticsResult, market: MarketData) → Signal | None`.
**Pseudocode:**
```python
class BaseStrategy(ABC):
    name: str = "ema_trend"
    version: str = "2.1"
    def generate(self, analytics: AnalyticsResult, market: MarketData) -> Signal | None:  # sync pure: no I/O, no broker, deterministic, explainability, well-commented WHY condition
        raise NotImplementedError
    # Example inside a concrete strategy:
    #   if analytics.features["ema_fast"] > analytics.features["ema_slow"] and analytics.features["rsi_14"] > 60:
    #       return Signal(symbol=market.symbol, direction="long", strength=0.7, valid_until=clock.now()+60s)
```
**Connects:** Every `strategy/*/*.py` inherits this. `M4.1` is the law for `M4.2`.

### `strategy_registry.py` — The Discoverer
**Does:** `@register_strategy("trend_following")` auto-discovers all `strategy.py` files at startup.
**Pseudocode:**
```python
registry = {}
def register_strategy(name):
    def deco(cls): registry[name] = cls; return cls
    return deco
# In trend_following/strategy.py:
#   @register_strategy("trend_following")
#   class TrendStrategy(BaseStrategy): ...
# On start: for file in strategy/*: import file → registry fills
```
**Connects:** `M1 lifecycle` calls `registry.load()`, `M4 AlphaEngine` loops `for strat in registry.values(): signal = strat.generate(analytics)`

### `lifecycle.py`, `versioning.py`, `parameter_registry.py`, `shadow_mode.py`
**Does:** `lifecycle` enables/disables per `user_id` (`user 42 enables mean_reversion, user 99 disables`), `versioning` stores `trend@v2.1 → v2.2` migration, `parameter_registry` loads `config/strategies/trend_following.yaml` (`ema_fast:10, ema_slow:20`), `shadow_mode` (async: runs new params in paper, tags shadow=True, never blocks live, well-commented WHY shadow) but tags `Signal(shadow=True)` → `M4.3` drops it (not sent to M5).
**Pseudocode:**
```python
# parameter_registry
params = load_yaml("config/strategies/trend_following.yaml")  # {ema_fast:10, ema_slow:20}
strategy = TrendStrategy(params=params)

# shadow_mode
if strategy.version not in allowlist[env]:  # paper allows all, prod only allowlist
    signal.shadow = True  # M4.3 will not forward to M5
```
**Connects:** `M10 Experience` `POST /strategies/{id}/enable` toggles `lifecycle`; `M12 Canary` promotes `shadow → canary → prod`.

---

## 4. M4.2 — STRATEGY FAMILIES — The 11 Brains (Each Parallelizable)

**Why 11:** Each family answers different question, same interface `generate() → Signal`. One person per family, no conflicts, because each file is pure `AnalyticsResult → Signal`.

**Example Families — Simple + Pseudocode:**

#### `trend_following/strategy.py` — EMA Cross
**Does:** `EMA 10 crosses above EMA 20 + RSI>60 → LONG` (trend).
**Pseudocode:**
```python
class EmaTrend(BaseStrategy):
    def generate(self, a: AnalyticsResult, m: MarketData):
        if a.features["ema_fast"] > a.features["ema_slow"] and a.features["rsi_14"] > 60:
            return Signal(symbol=m.symbol, direction="long", strength=0.7, 
                          valid_until=clock.now()+60s, explainability={"ema_cross":0.5, "rsi":0.5})
        return None
```
**Connects:** Reads `M3` `ema_fast, ema_slow, rsi`.

#### `momentum/strategy.py` — RSI Momentum
**Does:** `RSI 70+ → short (overbought), RSI 30- → long (oversold)` + `returns` filter.
**Pseudocode:** `if a.features["rsi_14"] > 70: return Signal(direction="short", strength=0.6)`
**Connects:** `M3 momentum`.

#### `mean_reversion/strategy.py` — Z-Score
**Does:** `zscore = (price - SMA20)/std20`; `zscore >2 → short (revert down), <-2 → long`.
**Pseudocode:** `z = (m.payload.price - a.features["sma_20"]) / a.features["std_20"]; if z>2: Signal("short")`
**Connects:** `M3 mean_reversion`.

#### `statistical_arbitrage/strategy.py` — Pairs
**Does:** `spread = EURUSD - 0.8*GBPUSD`; `zscore(spread) >2 → short spread (short EURUSD, long GBPUSD hedge)`.
**Pseudocode:** `spread = a.features["spread_eurusd_gbpusd"]; if abs(spread.zscore)>2: Signal("short", strength=min(|z|/3,1.0))`
**Connects:** `M3 cross_asset` `hedge_ratio, spread_builder, stationarity`.

#### `cross_sectional_momentum/universe_ranker.py` — Rank 10, Buy Top 3
**Does:** Rank `universe=[EURUSD,GBPUSD,...]` by `momentum` → `buy top 3, short bottom 3`.
**Connects:** `M3 cross_asset`.

#### Other families (same pattern):
- `mean_reversion_grid` — grid levels + inventory_control (persists `grid_state` via `M5` — only exception where strategy touches state)
- `microstructure` — `order_book_imbalance >0.6 → long` (needs `M3.4`)
- `liquidity_event` — `liquidity_depletion → short` (needs `M3.4`)
- `systematic_macro` — `rates differential + macro_regime risk_off → reduce crypto risk` (needs `M3.6`)
- `event_driven` — `CPI surprise + sentiment → long` (needs `M3.6`)

**Build Independence for M4.2:**
```bash
pytest modules/alpha/strategy/test_trend --fixtures=analytics_trending.json
# expects Signal(long) — no Risk, no Execution, no broker. One family per test file, parallel.
```

---

## 5. M4.3 — SIGNAL VALIDATION — The Bouncer

### `signal/validator.py`
**Does:** Is `symbol` in `M2b instrument_master` and `active`? Is `qty_step` respected? If not, Drop + audit.
**Pseudocode:**
```python
def validate(signal: Signal):
    if not reference.exists(signal.symbol, signal.broker): return Drop("unknown_symbol")
    spec = reference.get_spec(signal.symbol, signal.broker)
    if signal.symbol in ["frxEURUSD"] and spec.has_depth==False and signal.needs_depth: Drop
    return signal
```

### `deduplicator.py`, `freshness.py`, `latency_budget_validator.py`
**Does:** `deduplicator`: same `strategy_id+symbol+direction` within 60s → Drop duplicate. `freshness`: `now > valid_until` → Drop stale. `latency_budget`: `now - analytics.timestamp > 100ms` (microstructure) or `>60s` (macro) → Drop (alpha decayed).
**Pseudocode:**
```python
if signal.signal_id in seen_within(60s): Drop("duplicate")
if clock.now() > signal.valid_until: Drop("stale_signal")
if clock.now() - analytics.timestamp > budget[signal.strategy_family]: Drop("stale_alpha")
```

---

## 6. M4.4 — SIGNAL ORCHESTRATION — The Prioritizer

### `conflict_detector.py`, `queue_prioritizer.py`, `provenance.py`, `state_machine.py`
**Does:** `conflict_detector` (sync pure, deterministic, well-commented WHY priority): `momentum long EURUSD` vs `mean_reversion short EURUSD` within window → tag both `conflict=True` (M5 will net). `queue_prioritizer`: `exit` signals > `enter` high strength > low strength. `provenance`: attach `analytics_ids, market_watermark, config_hash` so `M7` can audit `tick → ledger`. `state_machine`: `generated → validated → queued → dispatched → expired`.
**Pseudocode:**
```python
def prioritize(signals: list[Signal]):
    return sorted(signals, key=lambda s: (s.direction=="exit", s.strength), reverse=True)
```

**How M4 Connects to Whole System:**
```
M3 AnalyticsResult + M2 MarketData → M4.1 Registry (load 11 families) → M4.2 Families (each generate) → Signal (unvalidated) → M4.3 Validation (drop bad) → M4.4 Orchestration (prioritize, tag) → Validated Signal → M5 Portfolio & Risk
```

**Build Independence:**
```bash
pytest modules/alpha --given=AnalyticsResult --expect=Signal
# mocks: no Risk, no Execution, no broker. Shadow signals not forwarded.
```

## 7. M4.5 — TRADE PLAN — The How (BEST APPROACH — Inside Alpha, Not Separate Engine)

> **Why inside M4, not `backend/engines/trade_plan/`?** Research's separate engine would be new Module with health/DB/RPC. We keep atomic: `M4.4 Validated Signal → M4.5 → TradePlan → M5`. No new process, `~0.3ms`.

### `trade_plan/engine.py` — The Translator
**Does:** Takes `Validated Signal{ direction, strength, valid_until }` + `AnalyticsResult{ atr, rsi }` + `MarketData{ price }` + `M2b spec` + `M3.0 capability` → builds `TradePlan{ entry, stop_loss, take_profit, timing, validity }` with `calculation_meta` preserved.
**Pseudocode:**
```python
def build_trade_plan(signal: ValidatedSignal, analytics: AnalyticsResult, market: MarketData, user_id, broker) -> TradePlan:  # sync pure: no I/O, deterministic entry/SL/TP, preserved calculation_meta, well-commented WHY model
  # Strategy declared preferred models via params: e.g., trend_following prefers entry=BREAKOUT, stop=ATR, target=RR
  params = parameter_registry.get(signal.strategy_id)  # {entry_model: BREAKOUT, stop_model: ATR_2x, target_model: RR_2}
  entry = entry_engine.build(signal, market, params.entry_model)  # e.g., BREAKOUT → price=1.085 + buffer
  stop = stop_engine.build(signal, market, analytics, params.stop_model)  # ATR_2x → 1.0835, invalidation = swing_low
  target = target_engine.build(signal, market, analytics, params.target_model)  # RR_2 → 1.088
  timing = timing_engine.build(signal)  # valid_from=now, valid_until=now+60s, entry_window=1m
  # validation: must have SL/TP, RR>0, not expired
  if not stop or not target: raise InvalidPlan("missing SL/TP")
  if (target.price - entry.price)/(entry.price - stop.price) < 1.0: raise InvalidPlan("RR <1")
  return TradePlan(
    trade_plan_id=uuid4(), signal_id=signal.signal_id, strategy_id=signal.strategy_id,
    instrument_id=signal.symbol, broker=broker, user_id=user_id,
    direction=signal.direction, entry=entry, stop_loss=stop, take_profit=target, timing=timing,
    data_quality=market.quality, capability_status=analytics.capability_status,
    calculation_meta={"stop_model": params.stop_model, "atr": analytics.features["atr_14"]},
    status="PENDING_RISK"
  )
```
**Connects:** `M4.4` → `M4.5` (same process, no queue) → `M5.3 Risk` (now validates `TradePlan`, not `Signal`). `M7` later can replay `TradePlan.calculation_meta`.

### `entry_engine.py`, `stop_engine.py`, `target_engine.py`, `timing_engine.py`
**Entry models:** `MARKET (at 1.085), LIMIT (1.0848), BREAKOUT (donchian_high+2pips), PULLBACK (ema20), STRUCTURE, INDICATOR_TRIGGER`
**Stop models:** `STRUCTURE_BASED (recent_swing_low - buffer), ATR_2x (price - 2*ATR), RECENT_SWING, FIXED_DISTANCE, STRATEGY_SPECIFIC` — preserves `reason: thesis invalidation at swing_low`
**Target models:** `RISK_REWARD (RR 2:1), STRUCTURE (recent_high), ATR_3x, PREVIOUS_EXTREME, TARGET_ZONE`
**Timing:** `created_at, valid_from, valid_until (60s), maximum_wait_time, entry_window, timeframe, expiration_condition` — if `now > valid_until → Drop` before M5.

**Example TradePlan JSON:**
```json
{
  "trade_plan_id": "...", "strategy_id": "momentum_v3@3.2.1", "instrument_id": "FX.EURUSD",
  "direction": "LONG",
  "entry": {"type": "MARKET", "reference_price": 1.085, "valid_from": "...", "valid_until": "..."},
  "stop_loss": {"type": "ATR_2x", "price": 1.0835, "invalidation": "swing_low", "buffer": "2*ATR"},
  "take_profit": {"type": "RR_2", "price": 1.088, "rr": 2.0},
  "timing": {"valid_until": "10:31:20", "maximum_wait_time": "60s"},
  "data": {"required_features_available": true, "data_quality": "VALID"}
}
```

### `validation.py` + `entry_models/*`, `stop_models/*`, `target_models/*`
**Does:** `validation`: `has SL & TP, RR>threshold, not expired, instrument tradable via M2b`. Each model file is pure `compute(price, analytics) → level`.
**Build Independence:** `pytest modules/alpha/trade_plan --fixtures=validated_signal.json --expect=TradePlan` — no Risk, no Execution.

**How M4.5 connects:**
```
M4.2 → Signal → M4.3/M4.4 → Validated Signal → M4.5 TradePlanEngine → TradePlan → M5 Risk (validates TradePlan) → RiskDecision
```

---

## 8. How M4 Connects to Whole System (Updated — TradePlan)

```python
# New law
M3 AnalyticsResult + M2 MarketData → M4.1 Registry → M4.2 Families → Signal → M4.3/4.4 → Validated Signal → M4.5 TradePlan → TradePlan → M5.3 Risk (checks TradePlan.valid_until, spread, RR, data_quality) → RiskDecision → M6
```

**High-speed:** `M4.5` `<0.5ms` pure, no I/O, per-tenant.

---

## 9. Strategy Families — Tier 1 Priority for Deriv (No Volume, Best Approach)

> **No strategy guarantees 99.8% wins.** Win% without `expectancy = win%*avg_win - loss%*avg_loss` is harmful. Our law: target `expectancy>0 after fees, max_drawdown < X, Sharpe > Y, automation 99.8% uptime`. Tier 1 below are price-only `FULLY_SUPPORTED` with Deriv ticks.

**Tier 1 — Build FIRST (price-only, backtestable on M8 tick_replay, 8 families):**
| Family | Uses | Note |
|--------|------|------|
| `trend_following` (EMA cross) | `ema_fast/slow, adx, choppiness` | HH/HL structure SL via M4.5 |
| `momentum` (RSI) | `rsi_14, returns` | overbought 70/short |
| `mean_reversion` (zscore BBands) | `sma, std, zscore` | `z>2 → revert` |
| `volatility_breakout` | `atr, vol_regime` | `DERIVED` ATR from tick range |
| `pullback` (ema pullback) | `trend + mean_reversion` | M4.5 pullback entry |
| `cross_sectional_momentum` (rank 10, buy top3) | `correlation, relative_strength` | needs 2+ symbols, fully supported |
| `statistical_arbitrage / spread` | `spread_builder, hedge_ratio, stationarity` | `EURUSD vs GBPUSD` forex pair |
| `regime_based` | `market_regime` | switches Tier1 logic |

**Tier 2 — After Tier 1, `PROXY_LIMITED` ⚠️ (must warn UI):**
`tick_activity (tick_rate, inter_tick_time, clustering), exhaustion, execution_features` — tick_count as liquidity proxy, **never present as volume**.

**Tier 3 — `CURRENTLY_UNAVAILABLE` — DISABLED via M3.0:**
`order_book_imbalance, vpin, book_slope, queue_dynamics, absorption, volume_profile, market_impact, toxicity` — needs `bid_levels/volume`. `M3.0` returns `UNAVAILABLE` → `M4.1` keeps strategy `DISABLED`, `M10` shows badge.

**Order matters:** P22 builds `trend_following::ema_trend` first as proof, then add 7 Tier1 families in parallel (one dev per family), no shared state.

---

## 10. Open Questions for M4

- [ ] Phase 1 ship only `trend_following::ema_trend` as proof, or also `momentum`?
- [ ] Latency budgets: `100ms` micro vs `60s` macro — adjust?
- [ ] `M4.2` 11 families parallel — assign one dev per family?
- [ ] Shadow period `N` days before promotion — what `N`?
---


---

## Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P22** | M4.1 Strategy Registry | `base_strategy, registry, shadow_mode` | `registry.load()` |
| **P23** | M4.2 Strategy Families (P1) | `trend_following::ema_trend` first family (Tier1) | `Signal LONG` |
| **P24** | M4.3 Signal Validation | `validator, deduplicator, freshness` | `Drop stale` |
| **P25** | M4.4 Signal Orchestration | `conflict_detector, queue_prioritizer` | `prioritized` |
| **P26** | **M4.5 Trade Plan (NEW)** | `trade_plan/engine, entry/stop/target/timing` → `TradePlan` | `RR>1 && valid_until` |

**After M3 (incl. M3.0):** Alpha starts after `M3` (M3.0 gates `UNAVAILABLE`). `P22` repeats 11× for each family (Tier1 first 8, then backlog). `P25` must be after `P24` (needs Validated Signal).

