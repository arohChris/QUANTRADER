# Strategy Library — with Analytical Requirements [v1.5 — maps to M4.2 manifests + M3.0 capability]

> **Source:** User-compiled library 2026-09-24 + research hardening. Each `IF` you wrote + **`Requires:`** block is the **frozen manifest** that `Temporal Resolver + PIT Data Service + Dependency Resolver + M3.0` enforce **before** `M4` emits `Signal` → `M4.5 TradePlan`. `period 20 hard-coded in analytic vX (static), which 20 bars as_of T resolved dynamically (availability_time ≤ T)`.

**Capability legend:** `FULLY_SUPPORTED` price-only Deriv OK · `PROXY_LIMITED` tick-count proxy warns `tick_count ≠ volume` · `UNAVAILABLE` Deriv `has_depth:false` → `INELIGIBLE` · `REQUIRES_EXTERNAL` needs feed until adapter.

**Timeframe rule:** All analytics `needs High/Low/Close` etc. are for `timeframe` in manifest — e.g., `Donchian(50) 4H` → `50×4H completed bars ending latest_completed 4H bar ≤ T`. `M3` checks `minimum_history hard gate (<n → INSUFFICIENT)` + `warmup quality gate (n < n+warmup → WARMUP_INCOMPLETE)` before calc — `M4` requires `pristine` unless `allow_warmup`.

---

## 🪙 CRYPTO — Universal (crypto-tuned thresholds)

### Moving Average Crossover — `IF EMA(fast,10-20) crosses EMA(slow,40-50) AND 24h volume > min_liquidity`
- **Requires Analytics:** `EMA_fast(14) v3`, `EMA_slow(50) v3` — each `needs Close, timeframe 4H/1H, minimum_history=period, bar_completion=completed`
- **Requires Data:** `OHLCV Close` 4H — 50 bars `as_of T` + `Volume 24h` liquidity filter
- **Capability:** `FULLY_SUPPORTED` (price) + volume filter `PROXY_LIMITED` (Deriv tick_count proxy) — if volume omitted, pure `FULLY_SUPPORTED`

### MACD Trend-Following — `IF MACD crosses signal AND histogram slope confirms`
- **Requires:** `MACD(12,26,9) v2` → `EMA12, EMA26, Signal EMA9` — `needs Close, 34 bars (26+9) minimum 4H`
- **Capability:** `FULLY_SUPPORTED`

### ADX Trend-Strength Filter — `IF ADX(14) >20 → trend regime confirmed`
- **Requires:** `ADX(14) v2` → `needs High/Low/Close, 28 bars (14*2 warmup), +DI -DI` — `4H/1D`
- **Capability:** `FULLY_SUPPORTED`

### Support/Resistance Breakout — `IF close > recent swing high AND volume spike ≥1.5× avg`
- **Requires:** `Swing High(20) v1` (`High, 20 bars 4H`) + `Volume SMA(20) v1` + `ATR spike` — Volume `PROXY_LIMITED` on Deriv
- **Capability:** `PROXY_LIMITED` with volume, `FULLY_SUPPORTED` without volume filter

### Donchian Channel Breakout — `IF close > highest(high,20) AND ATR expanding`
- **Requires:** `Donchian(20) v1` (`High/Low 20 bars`) + `ATR(14) v2` (`High/Low/Close 14 bars`) — total `20 bars 4H`
- **Capability:** `FULLY_SUPPORTED` · *Donchian is Turtle origin — same for commodities §20-55*

### RSI/Stochastic Overbought-Oversold — `IF RSI(14) <25 or >75 AND price at range boundary`
- **Requires:** `RSI(14) v3` (`Close 14 bars`) + optional `Stochastic %K(14,3)` + `Range Boundary 20 bars`
- **Capability:** `FULLY_SUPPORTED`

### Bollinger Band Mean-Reversion — `IF price touches outer band AND ADX <20`
- **Requires:** `BB(20,2) v2` (`Close 20 bars`) + `ADX(14) v2`
- **Capability:** `FULLY_SUPPORTED`

### Range Trading — `IF oscillating within S/R ≥N candles AND ATR below median`
- **Requires:** `ATR(14)` + `ATR percentile(50)` (`50 bars for median`) + `Range Detection 20 bars`
- **Capability:** `FULLY_SUPPORTED`

### Statistical Arbitrage — `IF price-ratio z-score >2 AND cointegration passes`
- **Requires:** `Correlation(60) + Hedge Ratio OLS + Cointegration Engle-Granger + Z-Score(60)` → `needs Close of pair, 60 bars same 4H, aligned as_of T, availability_time ≤ T`
- **Capability:** `FULLY_SUPPORTED` if pair exists on same adapter

### ML/Predictive Models — `IF confidence > threshold AND drift within bounds`
- **Requires:** `Model vX + Features [RSI, ATR, Volume…] + Drift + Confidence` → `minimum_history = max(feature minima)`
- **Capability:** `REQUIRES_EXTERNAL` model artifact + price features `FULLY_SUPPORTED`

### Ichimoku Cloud — `IF price above cloud AND Tenkan>Kijun AND future cloud bullish`
- **Requires:** `Ichimoku(9,26,52) v1` → `needs High/Low/Close, 52 bars 4H`
- **Capability:** `FULLY_SUPPORTED`

### VWAP Trading — `IF price reclaims/rejects VWAP with volume confirmation`
- **Requires:** `VWAP v1` → `needs High/Low/Close + Volume, 1D reset` — Deriv Volume proxy
- **Capability:** `PROXY_LIMITED`

### Fibonacci Retracement/Extension — `IF retraces to 0.618-0.786 AND reversal candle`
- **Requires:** `Swing High/Low 50 bars` + `Fib Levels` (derived) + `Reversal Candle 1 bar OHLC`
- **Capability:** `FULLY_SUPPORTED`

---

## 🪙 CRYPTO — Specific / favors crypto

### Volatility Breakout (BB squeeze) — `IF BB width <20-period low AND breakout outside band`
- **Requires:** `BB Width v1` (needs `BB20`, 20 bars) + `BB(20,2)`
- **Capability:** `FULLY_SUPPORTED`

### Relative Strength Rotation — `IF alt RS vs BTC > threshold → rotate`
- **Requires:** `Relative Strength v1` → `needs Close alt+BTC, 30 bars same 4H`
- **Capability:** `FULLY_SUPPORTED`

### Pairs Trading — `IF correlated-alt spread >2 std devs`
- **Requires:** `Spread Z-Score(60) + Cointegration` — `60 bars`
- **Capability:** `FULLY_SUPPORTED`

### Tick/1-min Scalping — `IF spread < X bps AND depth sufficient AND latency < threshold`
- **Requires:** `Spread bps (Bid/Ask tick)` + `Order Book Depth (bid_levels/ask_qty tick)` + `Latency`
- **Capability:** `UNAVAILABLE` on Deriv `has_depth:false` → `INELIGIBLE` until MT5 book — Tier 3 park

### Market Making — `IF spread ≥ min_profitable AND inventory within limits`
- **Requires:** `Spread + Inventory + Book Imbalance` → needs depth
- **Capability:** `UNAVAILABLE` on Deriv

### Triangular Arbitrage — `IF (BTC/USDT × ETH/BTC × USDT/ETH) implied ≠1 beyond fees`
- **Requires:** `Cross Rate v1` → `needs Bid/Ask 3 pairs same tick, sync as_of T`
- **Capability:** `FULLY_SUPPORTED` if 3 pairs exist on same adapter else `INCOMPATIBLE`

### Funding-Rate Arbitrage — `IF funding > threshold AND basis covers fees`
- **Requires:** `Funding Rate + Basis Spot vs Perp` → `needs perp funding feed, spot, futures`
- **Capability:** `UNAVAILABLE` Deriv no funding → `INELIGIBLE`

### Grid Trading Bots — `IF ADX<20 AND grid spacing > fee×2`
- **Requires:** `ADX14 + Grid Levels` (High/Low 14 bars)
- **Capability:** `FULLY_SUPPORTED`

### DCA Bots — `IF interval reached AND macro not confirmed downtrend`
- **Requires:** `Schedule` + optional `Macro Trend 50 bars`
- **Capability:** `FULLY_SUPPORTED`

### On-chain/Whale Wallet Tracking — `IF large wallet inflow/outflow > threshold`
- **Requires:** `On-chain Flow v1` → external whale feed
- **Capability:** `REQUIRES_EXTERNAL`

### Delta-Neutral Funding Harvesting — `IF long spot + short perp AND funding positive AND delta≈0`
- **Requires:** `Funding Rate + Delta` → needs `spot+perp positions`
- **Capability:** `UNAVAILABLE`

### Sentiment/Social Analysis Trading — `IF sentiment spike > threshold AND price not reacted`
- **Requires:** `Sentiment Score` → `social volume+polarity external`
- **Capability:** `REQUIRES_EXTERNAL`

### Cash-and-Carry Basis Trading — `IF futures − spot > cost-of-carry + fees`
- **Requires:** `Futures Curve` → `spot + futures term`
- **Capability:** `REQUIRES_EXTERNAL` until futures adapter

---

## 💱 FOREX — Universal (forex-tuned) / Favors / Specific

*Universal 12 share analytical Requires as crypto-universal above, thresholds differ via `config/forex.yaml` vs `crypto.yaml` (ADX 25 vs 20 is config, not new analytic).*

### Parabolic SAR / Trailing Trend — `IF SAR flips AND ADX confirms`
- **Requires:** `PSAR(0.02,0.2) v1` (`High/Low 20 bars`) + `ADX14`
- **Capability:** `FULLY_SUPPORTED`

### News/Economic Calendar Trading — `IF high-impact event within window AND volatility armed`
- **Requires:** `Economic Calendar` (`NFP/CPI feed`) + `ATR14` — `REQUIRES_EXTERNAL` event feed

### Carry Trade — `IF rate diff > threshold AND vol regime low`
- **Requires:** `Interest Rate Differential` (rates feed) + `Volatility Regime 20 bars`

### Triangular Arbitrage — `IF cross-rate implied ≠ quoted beyond spread/fee`
- **Requires:** `Cross Rate 3 pairs tick` — same as crypto triang

### COT Report Positioning — `IF CFTC extreme net positioning`
- **Requires:** `COT v1` → `CFTC feed weekly` — `REQUIRES_EXTERNAL`

### Smart Money Concepts / Order Block / FVG (ICT) — `IF price returns to unmitigated block/FVG AND HTF bias aligns`
- **Requires:** `Order Block + FVG` → `High/Low/Close 50 bars 4H + HTF Close 50 bars 1D`
- **Capability:** `FULLY_SUPPORTED` price action

*Other forex universal (MA 20/50, MACD, S/R round-number+spread, Donchian 20-55 Turtle, RSI 30/70, BB+ADX, Range Asian+ATR, Stat Arb EUR/USD vs GBP/USD, ML, Ichimoku, Fib 0.5-0.618) — Requires as crypto-universal, Donchian Turtle 20-55 needs 55 bars 4H.*

---

## 🛢️ COMMODITIES — Universal / Favors / Specific

*Universal 12 same as above, Donchian 20-55 Turtle → `Donchian(55) 55 bars 4H/Daily` — origin.*

### Parabolic SAR / Trailing Trend — same `PSAR+ADX`

### Pairs Trading WTI/Brent — `Spread Z-Score 60 bars` same Stat Arb

### News/Inventory Trading — `IF EIA/USDA within window` → `Economic Calendar` external

### VWAP Trading — `VWAP+Volume` → `PROXY_LIMITED`

### Seasonal Trading — `IF calendar window matches seasonal pattern AND win-rate > threshold N years`
- **Requires:** `Seasonality v1` → `needs Close 5 years daily` — `minimum_history = 5y`
- **Capability:** `FULLY_SUPPORTED` but long history gate

### Contango/Backwardation Curve Trading — `IF front vs back month spread signals contango/bearish or backwardation/bullish`
- **Requires:** `Futures Curve` → `front+back month futures` — `REQUIRES_EXTERNAL` until futures adapter

### COT Report Positioning — same `COT external`

---

**Annex — Adding to M4.2:** Create `strategies/<name>/manifest.yaml` with `requires_analytics, timeframe, minimum_history, warmup, bar_completion, freshness_limit` exactly as Requires above. `M3.0 CapabilityRegistry.is_strategy_eligible(strategy_id, broker)` + `Temporal Resolver` + `PIT Data Service as_of T` enforce before `Signal` → `M4.1 disables families` `UNAVAILABLE` → UI badge `PROXY_LIMITED ⚠️` vs `INELIGIBLE`. `M8` replays same path with `SimulatedClock` — backtest ≡ live.

*This file is companion to System_Development_Manual v1.5 §8b + §6a — do not duplicate logic, import manifests.*
