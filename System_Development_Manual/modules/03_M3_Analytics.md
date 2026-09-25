# M3 — ANALYTICS (Feature Computation) — EXPANDED DISCUSSION
### Answers “What is mathematically happening?” — never decides to trade. Pure, vectorized, GLOBAL market-data inputs, shared code (per-tenant only for `PortfolioState`).

**Module Type:** Stateless pure-computation — deterministic, idempotent, cacheable. `compute(candles)→features` — same code serves Deriv and MT5 tenants.
**Owns State:** `feature_cache` (Redis LRU, ephemeral per `provider:symbol:timeframe:indicator` key) — no durable business state.
**Input:** `MarketData` (GLOBAL per `provider+instrument`, no `user_id` — AGENTS §4.3) + `PortfolioState` (per-tenant, read-only)
**Output:** `AnalyticsResult(symbol, timeframe, features={rsi:68, vol:0.018}, regime)`.

> **Expanded for M3** — each file simple + pseudocode + connection. This patches into master §8.

---

## 1. Purpose in One Sentence
> **M3 turns `MarketData` into numbers (`RSI`, `ATR`, `correlation`) so `M4` can decide `long/short`.** No `if should_buy` here — just math.


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution is mandatory <500ms order→ack, <50ms tick→parquet, <20ms analytics, <10ms risk. No calculation error, file handling error, or abnormality is tolerated — every I/O is async `async def` + `await` + `try/except + retry with jitter + dead-letter`, every pure calc is sync `def` + frozen `BaseContract` + golden test, every state change is event-driven via `event_bus` not poll. Code must be well-commented WHY, not just WHAT.*

## 2. The 7 Sub-Modules — Best Approach (Capability-First)

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M3.0** | Capability Registry (NEW — Best Approach) | Decides `FULLY_SUPPORTED / PROXY_LIMITED / UNAVAILABLE` per broker+tenant, gates M4 families | Add provider/feature, change Deriv limits |
| **M3.1** | Feature Engine & Profiles | Orchestrates + caches + picks params per asset class | Add asset class |
| **M3.2** | Per-Asset | 17 pure functions: RSI, EMA, ATR, etc. — each file parallelizable | Add indicator |
| **M3.3** | Cross-Asset | Correlation, cointegration — needs multiple symbols | Add pair logic |
| **M3.4** | Microstructure | Order book imbalance, VPIN — needs depth | Add book feature |
| **M3.5** | Portfolio Analytics (read-only) | Exposure, VaR, margin compute — reads `PortfolioState` | Add portfolio metric |
| **M3.6** | Macro & Event | Rates, event impact — daily | Add macro regime |

## 3. M3.0 — CAPABILITY REGISTRY — The Gatekeeper (BEST APPROACH — NOT a separate engine)

> **Why M3.0, not `backend/engines/data_capability/`?** Research proposes cross-cutting service called by Data/Analytics/Strategy. That couples `M2 → capability → M3 → capability` and needs DB/RPC. Our best approach keeps it **pure lookup inside M3**, per-**broker** (optional tenant), no DB, `<1ms`.

### `capability/capability_registry.py` — The Single Source
**Does:** `registry.get("order_book_imbalance", broker="deriv", user_id=42) → {status: UNAVAILABLE, reason: "needs bid_levels", required_data:[bid_levels, ask_qty], available_providers:[deriv]}`. Loaded once from `config/capabilities.yaml` (versioned).
**Pseudocode:**
```python
# config/capabilities.yaml (versioned)
features:
  order_book_imbalance: {status: UNAVAILABLE, reason: "Deriv no depth", requires: [bid_levels, ask_qty]}
  tick_rate: {status: PROXY_LIMITED, method: "tick_count_proxy", warning: "not volume"}
  rsi_14: {status: FULLY_SUPPORTED, requires: [price]}
  atr_14: {status: DERIVED, method: "tick_range_proxy"}

class CapabilityRegistry:
  def is_available(self, feature: str, broker: str) -> bool:
    return self.get(feature, broker).status in (FULLY_SUPPORTED, DERIVED)
  def is_strategy_eligible(self, strategy_id: str, broker: str) -> tuple[bool, str]:
    req = strategy_requirements[strategy_id]  # e.g., order_book_imbalance requires [bid_levels]
    for f in req:
      if not self.is_available(f, broker): return False, f"needs {f} UNAVAILABLE"
    return True, "eligible"
```
**Connects:** `M3.1` checks `registry.is_available(feature)` before `get_features()`; `M4.1 lifecycle` on `registry.load()` disables families → `strategy_health=DISABLED` + UI explains why. **Per-broker gate:** `broker="deriv"` in the YAML lookup (no `WHERE`, no DB) — Deriv tenants never see book features.

### `capability/capability_types.py`, `provider_capabilities.py`, `strategy_requirements.py`
**Types:**
```python
class CapabilityStatus(str, Enum):
  FULLY_SUPPORTED = "FULLY_SUPPORTED"      # price → rsi, deriv has it
  DERIVED = "DERIVED"                      # ATR from tick range proxy but honest
  PROXY_LIMITED = "PROXY_LIMITED"          # tick_rate as liquidity proxy — must warn UI
  CURRENTLY_UNAVAILABLE = "UNAVAILABLE"    # order_book_imbalance on Deriv
  REQUIRES_EXTERNAL_DATA = "REQUIRES_EXTERNAL" # macro CPI needs FRED
```
**Connects:** `M10 API` `GET /capabilities?broker=deriv` reads registry for UI badge `PROXY_LIMITED ⚠️`.

### `availability_status.py`
**Does:** Exposes `feature: order_book_imbalance → {status: UNAVAILABLE, available_providers:[deriv], required_data:[bid_levels...]}` for `M12` health.
**Pseudocode:** `if status==PROXY_LIMITED: analyticsResult.provenance.warning="tick_count ≠ volume"`

**Build Independence:** `pytest modules/analytics/capability --yaml=config/capabilities.yaml` — no DB, no M2, no M4.

---

## 4. M3.1 — FEATURE ENGINE & PROFILES — The Conductor

### `feature_engine.py`
**Does:** Given `symbol=EURUSD, timeframe=1m, broker=deriv`, it figures dependency graph `returns → momentum → RSI` and cache: `cache.get(deriv:EURUSD:1m:RSI_14) or compute`. **Cache key has NO `user_id`** — candles are global, so the same features serve every tenant (AGENTS §4.3).
**Pseudocode:**
```python
async def get_features(symbol, timeframe, broker):  # async: Redis + PIT I/O, pure compute sync, cache per provider+instrument
    key = f"{broker}:{symbol}:{timeframe}:RSI_14"
    if key in redis: return redis.get(key)  # <1ms
    candles = pit.get_market_state(instrument=symbol, broker=broker, as_of=T)  # Temporal Resolver — availability_time <= T (NO lake.get, NO user_id)
    params = profiles.get(symbol)  # forex → {rsi_period:14, vol_window:20}
    features = {}
    features["returns"] = returns.compute(candles)  # M3.2
    features["rsi_14"] = momentum.compute(candles, params)  # needs returns (engine knows order)
    redis.setex(key, 60, AnalyticsResult(features))
    return AnalyticsResult(symbol, timeframe, features)
```
**Connects:** Called by `M4` `strategy.generate` (one call, not 17). `M5` also calls for `vol` during sizing.

### `profiles/*.py`
**Does:** `forex_profile → {rsi_period:14, vol_window:20}`, `crypto_profile → {rsi_period:14, vol_window:30}`, `commodity_profile → {vol_window:30}`. Thin wrappers around `config/assets/*.yaml`.
**Pseudocode:** `def get_profile(asset_class): return load_yaml(f"config/assets/{asset_class}.yaml")["analytics"]`
**Connects:** `M3.2` reads params, not hard-coded. Adding `crypto` = new YAML + profile file, no code change in `per_asset`.

---

## 5. M3.2 — PER-ASSET — The 17 Pure Functions (Embarrassingly Parallel)

**Why pure:** `compute(candles: list[Candle], params: dict) → dict[str,float]` — no DB, no `clock`, no side effects. 3 devs can each own 5 indicators, no conflicts.

**Example Files — Simple + Pseudocode:**

#### `per_asset/momentum.py` — RSI
**Does:** `RSI = 100 - 100/(1+RS)` where `RS = avg_gain/avg_loss` over 14 candles.
**Pseudocode:**
```python
def compute(candles, params  # sync pure, no I/O, deterministic, WHY this formula, well-commented line-by-line={rsi_period:14}):  # sync pure: no I/O, no clock, deterministic, frozen output, golden test, well-commented WHY formula
    gains = [max(0, c.close - prev.close) for c,prev in zip(candles[1:], candles)]
    losses = [max(0, prev.close - c.close) for c,prev in ...]
    avg_gain = sum(gains[-14:])/14; avg_loss = sum(losses[-14:])/14
    rs = avg_gain / (avg_loss or 1e-9)
    rsi = 100 - 100/(1+rs)
    return {"rsi_14": rsi}  # 68.2
```
**Connects:** `M4` `price_momentum` reads `features["rsi_14"] > 70 → short`.

#### `per_asset/moving_averages.py` — EMA
**Does:** `EMA_t = price_t * k + EMA_{t-1}*(1-k)` where `k=2/(period+1)`.
**Pseudocode:** `ema = candles[0].close; for c in candles[1:]: ema = c.close*k + ema*(1-k); return {"ema_20": ema}`
**Connects:** `M4 trend_following` `ema_fast > ema_slow → long`.

#### `per_asset/volatility.py` — ATR
**Does:** `ATR = avg(True Range)` where `TR = max(high-low, |high-prev_close|, |low-prev_close|)`.
**Pseudocode:** `trs = [max(c.high-c.low, abs(c.high-prev.close), abs(c.low-prev.close)) for c in candles]; atr = sum(trs[-14:])/14`
**Connects:** `M5 sizing` `qty = risk_budget / (atr * contract_size)`.

#### `per_asset/hurst_exponent.py`, `choppiness.py`, `persistence.py` — Regime Detectors
**Does:** `Hurst >0.5 → trending`, `Choppiness <38 → trending`, `autocorr >0.2 → persistent`.
**Pseudocode (Choppiness):** `chop = 100 * log10(sum(atr(1))/(maxHigh-minLow)) / log10(n)`; `return {"choppiness": 22}  # trending`
**Connects:** `M4 regime_filter` skips `mean_reversion` when `choppiness<38` (trending).

**Other files in M3.2:** `price_structure` (HH/HL), `returns` (log returns), `trend` (ADX), `acceleration` (momentum of momentum), `mean_reversion` (BBands, zscore), `volatility_regime` (low/high via percentile), `volume` (OBV, VWAP), `spread` (bid-ask stats) — each same pure pattern.

**Build Independence for M3.2:**
```bash
pytest modules/analytics/per_asset/test_momentum --golden=eurusd_trending_1m.csv
# expects rsi ≈ 68 — no DB, no broker, no M4
# 17 files × 3 fixtures (trending, mean-reverting, flat) = parallel, no shared state
```

---

## 6. M3.3 — CROSS-ASSET — Multi-Symbol

### Files

#### `cross_asset/correlation.py`, `beta.py`, `cointegration_engine.py`, `hedge_ratio.py`, `stationarity.py`
**Does:** `correlation.py` → `rolling_corr(EURUSD, GBPUSD, window=20) = 0.85`; `cointegration_engine` → `Engle-Granger p-value 0.02 → coint True`; `hedge_ratio` → `OLS hedge = 0.8` (1 EURUSD ≈ 0.8 GBPUSD).
**Pseudocode:**
```python
def correlation(candles_a, candles_b, window=20):
    returns_a, returns_b = log_returns(candles_a), log_returns(candles_b)
    return np.corrcoef(returns_a[-20:], returns_b[-20:])[0,1]  # 0.85
```
**Connects:** `M4 statistical_arbitrage` reads `coint True + hedge 0.8 → build spread = EURUSD - 0.8*GBPUSD`.

---

## 7. M3.4 — MICROSTRUCTURE — Needs Depth (Only Where `has_depth=True`)

### Files

#### `microstructure/order_book_imbalance.py`
**Does:** `(bid_qty - ask_qty)/(bid_qty+ask_qty)` → `0.7` = buy pressure; `-0.7` = sell pressure.
**Pseudocode:** `imb = (sum(bids.qty) - sum(asks.qty)) / (sum(bids.qty)+sum(asks.qty))`
**Connects:** `M4 microstructure/order_book_imbalance` → `imb>0.6 → long`.

#### `toxicity_vpin.py`, `market_impact.py`
**Does:** `VPIN` high → toxic flow (informed traders), `Kyle lambda` → price impact per lot.
**Connects:** `M5 liquidity_check` blocks if `vpin>0.8` (toxic), `M6 smart_router` uses `lambda` to pick venue.

---

## 8. M3.5 — PORTFOLIO ANALYTICS (Read-Only, Doesn’t Own Positions)

### Files

#### `portfolio/margin.py` (compute) vs `M5 margin_check` (decide)
**Does:** `M3` *computes* `margin_used = sum(position.qty * contract_size * price / leverage)`; `M5` *decides* `if available - required < threshold → Reject`. Compute vs decide split.
**Pseudocode (M3):** `margin_used = sum(p.qty * spec.contract_size * last_price / leverage for p in portfolio.positions)`
**Connects:** `M5 pre_trade/margin_check` calls `M3 portfolio/margin.py` (compute) then decides.

**Other files:** `exposure`, `concentration`, `var_calculator`, `scenario_risk` (what if EURUSD +2%?) — all read `PortfolioState` contract, not DB.

---

## 9. M3.6 — MACRO & EVENT — Daily

### Files

#### `macro/rates.py`, `event/event_registry.py`
**Does:** `rates` fetches FED 5.5%, `event_registry` knows `CPI 2026-09-21 14:30` is high impact.
**Pseudocode:** `event = registry.get("CPI", date); impact = classifier(event)  # high/medium/low`
**Connects:** `M4 systematic_macro` reads `macro_regime=risk_off → reduce crypto / commodity risk`.

---

## 10. How M3 Connects to Whole System

```
M2 MarketData → M3.0 Capability (per-broker gate, <1ms) → M3.0 Capability (sync check) → M3.1 FeatureEngine (cache) → M3.2-3.6 pure compute (shared code) → AnalyticsResult(features, regime, capability_status, warning) → M4.1 gates families (DISABLED if UNAVAILABLE) + M4 generates → M5 Risk (reads vol/spread + capability)
```

**High-speed:** `M3.2` vectorized `numpy`, `M3.1` `Redis` cache per `broker:symbol:timeframe` (no `user_id` — global candles), p95 <20ms.



---

## Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P15** | M3.0 Capability (NEW) | `capability_registry, provider_capabilities, strategy_requirements` — `FULLY_SUPPORTED/PROXY/UNAVAILABLE` per broker | `test_order_book_unavailable_on_deriv` pass |
| **P16** | M3.1 Feature Engine & Profiles | `feature_engine, profiles` cache per tenant | `cache_hit <1ms` |
| **P17** | M3.2 Per-Asset P1 | First 5 indicators (momentum/trend) — parallel | `rsi` golden |
| **P18** | M3.2 Per-Asset P2 | Remaining 12 indicators — parallel | `hurts` golden |
| **P19** | M3.3 Cross-Asset | `correlation, cointegration, hedge_ratio` | `corr 0.85` |
| **P20** | M3.4 Microstructure | `order_book_imbalance` (capability-gated) | `UNAVAILABLE` on Deriv → skip |
| **P21** | M3.5 Portfolio (ro) ‖ M3.6 Macro | `portfolio/margin` compute ‖ `rates/event_registry` | `margin` compute vs decide split holds |

**Parallel after M2 Lake + M1:** `M3.0` must be first (gates `M3.2-M3.4`); `M3.2` 17 files embarrassingly parallel after `M3.0`.

## 10b. Data Validation — §8b Hardening [NEW v1.5]

> **[NEW v1.5]** `M3` now validates `minimum_history` hard gate vs `warmup` quality gate before `M3.2 compute`. E.g., `EMA200: <200 INSUFFICIENT (no calc), 200-249 WARMUP_INCOMPLETE (GENERATABLE with flag), ≥250 pristine`. See `System_Development_Manual §8b` for full gate table + `REQUIRED/AVAILABLE` explainability + `PIT availability_time <= T`.

## 11. Open Questions for M3

- [ ] Phase 1 cut: 5 features (returns, MA, RSI, vol, spread) vs full 17 — lock 5 as v0.3?
- [ ] Should `portfolio/margin` compute stay in `M3` or move to `M5`? Keep compute vs decide split?
- [ ] `M3.2` 17 files parallel — assign 3 devs × 5 each?
---
