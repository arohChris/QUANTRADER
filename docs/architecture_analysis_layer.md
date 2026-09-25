# Architecture: Analysis Layer — Analysis Engine, Snapshots, Strategy

> Captured 2026-09-25 from the human (session message, closed with "lgtm").
> Status: **LGTM'd design guidance** — not a §2 spec. Governs future M3.x spec work
> (Feature Engine, indicators, Analysis Snapshots, Strategy consumption).
> Does **not** change Current Gate G0. **QUEUE UPDATED (human D3, 2026-09-25):**
> "let move to analysis now... come back to m2.10, write everything it's supposed to
> do down and mark it as deferred" → Analysis layer is now FIRST; m2.10 WARM is
> DEFERRED (scope captured in `docs/specs/m2.10_warm.spec.md`); resume order:
> Analysis → m2.10 → §5 ws_url → m2.11.
> Bible mapping note (flagged, not silently resolved): QUANTRADER2 numbering is **M5 = Risk,
> M6 = Execution**; the pipeline row "TradePlan → P29 → M5" below maps to
> TradePlan → M5 (risk validate/reserve) → M6 (execute). Reconcile exactly when the
> M4/M5/M6 specs are written.

## Pipeline

```text
MARKET EVENTS → M2.1 Market Data → M2.4 Candle/Timeframe Engine → ANALYSIS ENGINE
  → Indicator State / Analysis Snapshot → STRATEGY ENGINE → Signal → TradePlan
```

## 1. Analysis owns calculations

Analysis calculates SMA/EMA/WMA, RSI, Stochastic, MACD, ADX, ATR, Bollinger Bands,
support/resistance, trend state, volatility, breakout levels, and other reusable
indicators. Strategy must **not** recalculate these.

```text
Analysis:  EMA(20,5m)=102.43  EMA(50,5m)=101.87  RSI(14,5m)=68.2  ADX(14,5m)=27.4
Strategy:  if EMA20>EMA50 and RSI>threshold and ADX>threshold → signal
```

## 2. Every analysis has a data context — formal `AnalysisContext`

An indicator is never just `RSI(14)`; it is:

```text
AnalysisContext = instrument, timeframe, candle_state, calculation_mode,
                  as_of_timestamp, data_sequence/version
```

e.g. `BTC | 5m | RSI(14) | CLOSED` is a different analytical object from `BTC | 1h | RSI(14) | CLOSED`.
This is a formal object in the architecture.

## 3. Timeframe belongs to the analysis request

Strategy requests dependencies, not constructions:

```text
Strategy: "Give me RSI(14) for this instrument on 5m"   (NOT "build a 5m candle then calc RSI")
Also: EMA(20) 5m, EMA(50) 5m, RSI(14) 5m, ADX(14) 15m, ATR(14) 1h
```

The Analysis layer knows how to obtain the correct data.

## 4. Demand-driven — do NOT calculate everything for everything

100 instruments × 20 timeframes × 20 indicators must not be blindly computed.
Strategies declare their required set; the Analysis Engine computes exactly that set
and maintains it efficiently.

## 5. Incremental state — no recalc-from-scratch every tick

RSI(14) must not reload 14 candles per tick. On `5m candle finalized` → update
EMA/RSI/MACD/ATR/ADX state incrementally → publish new Analysis Snapshot.

## 6. Closed vs forming must be explicit — `calculation_mode`

Every analysis calculation carries a calculation mode: `CLOSED` or `LIVE_FORMING`.
Values differ between modes; strategies must never accidentally mix them.

## 7. Closed-candle analysis is the default

`5m candle closes → Analysis Engine updates → RSI/MACD/EMA/ADX → Strategy evaluates`.
Intrabar needs must explicitly request `LIVE_FORMING`. Safer than indicators changing
on every tick by default.

## 8. Live ticks still matter — two event types

```text
CANDLE_UPDATED      — forming candle changed → LIVE analysis may update
CANDLE_FINALIZED    — candle closed → FINALIZED snapshot → closed-candle signal
```

Ticks update current 1m/5m/15m candles, live price, live-forming indicators, execution logic.

## 9. AnalysisSnapshot

```text
AnalysisSnapshot:
  instrument=EURUSD, timestamp=..., timeframe=5m, candle_state=CLOSED, sequence=184923
  EMA20, EMA50, RSI14, MACD, MACD_SIGNAL, ADX14, ATR14,
  BOLLINGER_UPPER/MIDDLE/LOWER, trend_state, volatility_state
```

Strategy receives this snapshot and does not care how values were produced.

## 10. Strategy becomes a decision layer

| Strategy | Consumes from Analysis | Strategy logic |
|---|---|---|
| MA Crossover | EMA20, EMA50 | crossover → signal |
| RSI OB/OS | RSI14 | enters configured zone → signal |
| MACD trend | MACD, signal, histogram | defined relationship → signal |
| ADX filter | ADX14, +DI, -DI | trend-strength condition → signal |
| Bollinger reversal | upper/middle/lower, price position | reversal condition → signal |
| Breakout | range high/low, current price, confirmation | breakout condition → signal |

## 11. Multiple timeframes

One strategy may request several contexts (e.g. 5m: EMA20/EMA50/RSI14; 15m: ADX14/EMA50;
1h: EMA200). The Analysis Engine maintains them independently per
`instrument → {timeframe → indicator states}` and the strategy combines results.

## 12. Time does not trigger recalculation — market events do

> A market-data event changes the state of the relevant timeframe, which triggers the
> relevant analysis updates.

`10:04:59` forming → `10:05:00` 5m finalizes → 5m analysis state advances → snapshot →
strategy evaluates → wait for next relevant event.

## 13. Indicators update on their own timeframe's events

1m indicators on 1m candle events, 5m on 5m, 15m on 15m, 1h on 1h. Live-forming updates
only when explicitly requested. No 1h recomputation per 1s tick.

## 14. Dependency-driven architecture

```text
Strategy → declares analysis dependencies → Analysis Dependency Manager
         → Analysis Engine → Timeframe/Candle Engine
```

Five strategies requiring `5m EMA20` share **one** EMA20 state — never five copies.

## 15. Analysis registry — shared keys

```text
AnalysisKey = instrument + timeframe + indicator + parameters + calculation_mode
e.g. EURUSD | 5m | EMA | period=20 | CLOSED
```

Identical requests reuse the existing calculation.

## 16. Indicator lifecycle

```text
CONFIGURED → WARMING_UP → READY → UPDATING → STALE/INVALID
```

Strategy must know `READY` vs `NOT_READY`; it never silently receives an invalid value
(e.g. EMA20 before warmup satisfied).

## 17. Data version / sequence

Every snapshot carries `instrument, timeframe, sequence, candle_timestamp`
(e.g. `EURUSD 5m seq=1821 ts=10:05`) so a strategy cannot mix new 5m RSI with old 5m EMA.

## 18. Concurrency rule — atomic snapshots

Strategy never independently queries ten changing indicators (would mix
10:05:01 RSI with 10:05:03 MACD). It evaluates one coherent `Analysis Snapshot #1821`
where every value comes from state #1821 — atomically.

## 19. Canonical architecture

```text
M2.1 Market Events → M2.4 Candle + Timeframe Engine → Analysis Engine
  → Analysis Snapshots → Strategy Engine → TradePlan → [risk validate] → M5/M6
```

| Layer | Responsibility |
|---|---|
| M2.1 | Receive and normalize market events |
| M2.4 | Build/finalize candles and timeframes |
| Analysis | Calculate reusable indicators and market-state measurements |
| Strategy | Interpret analysis and generate signals |
| TradePlan | Convert signal into executable trade intent |
| P29 / M5 | Validate/reserve risk (QUANTRADER2: M5 = Risk) |
| M6 | Execute (QUANTRADER2: M6 = Execution) |

### The most important rules

> **Analysis calculates what the market data says. Strategy decides what that information means for a particular strategy.**

> **Every analysis value must have explicit instrument, timeframe, parameters, calculation mode,
> timestamp/sequence, and readiness state. Analysis is event-driven and stateful — it does not
> blindly recalculate everything on every tick. Strategies consume coherent Analysis Snapshots
> rather than independently querying changing indicators.**

## 20. M2.9 HOT cache placement + cache-vs-truth law [LGTM 2026-09-25]

```text
              MARKET PROVIDER → M2.1 → ┬─ M2.4 Candle Engine ─┬
                                       └─ M2.9 HOT (latest tick, forming candle)
                                                ↓
                                    CANONICAL MARKET STATE → ANALYSIS ENGINE
                                       → AnalysisSnapshot → STRATEGY → BOT
                                       → TradePlan → risk gate → M5/M6
```

- **M2.9 is a cache, not a pipeline authority.** Key existence ≠ valid market state.
  `cache_available=true` NEVER implies `market_state_valid=true` — validity comes from the
  authoritative M2.1/M2.4 stream. M2.9 down → Analysis keeps running on the authoritative
  stream; state goes STALE/INVALID per consumer policy. **Valkey is never a hidden
  trading dependency** (fail-open tests lock this).
- **Bots never query M2.9 directly** — they consume `AnalysisSnapshot`
  (`instrument_id, sequence, as_of_timestamp, timeframe, candle_state, values={...}`).
  M2.9 may serve low-latency current state underneath.
- **Forming = mutable state (M2.9 caches, overwrites expected, per-timeframe isolated);
  finalized = immutable history (M2.4 → Timescale/WARM, never stored in M2.9).**
- **Staleness metadata only:** cache provides data + timestamp + watermark; it MUST NOT
  judge age — `fresh/stale/invalid` is the consumer's policy (scalper 500ms vs swing 30s).
- **LIVE_FORMING vs CLOSED:** forming updates only move LIVE analysis; CLOSED strategy
  state advances solely on `CANDLE_FINALIZED` → new snapshot sequence → evaluate.
- **Canonical namespace:** `resolve_source_namespace(md)` = provider-or-broker, and key
  builders are the single place keys are formed (no read/write mismatch).

M2.9 scope lock — DOES: latest/forming cache, TTL, ser/deser, key isolation, validation,
cache health, fail-open, stale-data metadata. Does NOT: indicators, signals, trade/risk
decisions, source of truth, historical storage, order execution.

## 21. M2.9 review outcome (A33, 2026-09-25 — "correct foundation" + lgtm, amendments applied)

- **Canonical-source statement (strengthened per human):** *M2.4 produces canonical
  market-state events; the durable finalized 1-minute candle store (WARM/Timescale) is
  the canonical persisted historical dataset. M2.9 is only a transient acceleration
  layer.* (M2.4 is an engine — not itself the permanent source of historical truth.)

```text
Provider → M2.1 → M2.4 ──→ durable finalized candles (canonical history)
                      └──→ M2.9 HOT → fast current state → Analysis
```

- **Monotonic writes (the hardening the review demanded — now LAW in spec + code +
  tests):** M2.9 never replaces newer cached state with older — `set_latest` gates on
  `watermark` (`<` reject; `=` overwrite documented), `set_forming` gates on
  `interval_start` (older bucket rejected). **Naive/aware temporal mismatch = invalid
  temporal state → reject write + warning, never treated as permission to overwrite.**
- **HARD ARCHITECTURAL INVARIANT (M2.1/M2.9)** [A33 r2 — explicitly not an
  implementation note]: *For each provider+instrument HOT key, writes are serialized
  through one ordered writer. No independent worker may concurrently perform
  `set_latest`/`set_forming` for the same key.* If this invariant is removed or
  violated, M2.9 monotonicity **MUST** move to an atomic server-side CAS/Lua operation
  **before** multi-writer operation is enabled — otherwise a future worker silently
  breaks A33.
- **Consumer law:** M2.9 accelerates the pipeline; Strategy/Bot consume **AnalysisSnapshot
  only** — direct `get_latest → indicator → trade` loops are BANNED (they recreate the
  architecture problem this design solved).
- **analysis_mode is explicit config:** every strategy/bot declares
  `analysis_mode: CLOSED | LIVE_FORMING` — a strategy may never silently switch modes.
  **Binding on all future strategy slices (M4/P22, M5, bots).**
- **Final position:** *M2.9 accelerates the system. It never decides what the market
  means, whether a trade should happen, whether risk is acceptable, or whether an order
  should execute.*

```text
                    ┌──────────────┐
                    │     M2.1     │
                    │ Market Input │
                    └──────┬───────┘
                           ↓
                    ┌──────────────┐
                    │     M2.4     │
                    │ Candle/State │
                    └──────┬───────┘
                           ↓
              ┌────────────┴────────────┐
              ↓                         ↓
       Durable WARM                 M2.9 HOT
       TimescaleDB                    Valkey
              ↓                         ↓
              └────────────┬────────────┘
                           ↓
                    Analysis Engine
                           ↓
                  Analysis Snapshot
                           ↓
                    Strategy Engine
                           ↓
                       Bot Engine
                           ↓
                       TradePlan
                           ↓
                      P29 Risk Gate
                           ↓
                      M5 Execution
```
