## 0. How to Use This Bible

This document is **the law**. If code contradicts this bible, code is wrong.

**Audience:** Any engineer (backend, frontend, quant, DevOps) joining the project should be able to:
1. Understand where to put any new code in < 2 minutes
2. Know which engine to call and which contract to use
3. Implement an engine without reading another engine's internals

**Reading Order:**
1. **Everyone:** Sections 1-5 (Philosophy, Top-Level, Core, Contracts, Dependency Rules)
2. **Engine Implementer:** Your engine's chapter (6-22) + Sections 33-35 (Flows/Failures)
3. **Frontend:** Section 32 + 27 (API) + 22 (Synchronization)
4. **DevOps/QA:** Sections 26, 30, 31, 37

**Companion Documents (in `backend/docs/`):**
- `architecture/module_map.md` — Folder to responsibility mapping
- `architecture/dependency_rules.md` — Import allow-list with enforcement
- `architecture/data_flow.md` — End-to-end sequence diagrams
- `architecture/contracts.md` — All Pydantic schemas with fields
- `engines/<engine>/README.md` — Per-engine Purpose/Inputs/Outputs/Contracts/Failure Modes

> **Rule:** No PR may introduce a new top-level folder or cross-engine import without updating this bible.

---

## 1. Executive Summary

We are building an **institutional-grade, highly profitable, multi-asset, 99.8% automated (minimal manual) algo-trading, research & analysis platform**.

**Asset & Broker Coverage:** forex, crypto, commodities across **Deriv, MT5, Headway** (extensible via `broker_feeds/` + `broker_adapters/` — no core rewrite per broker).

**Institutional Expectations (explicitly designed):**
1. **Multi-User Platform — Strict Tenant Isolation:** Every user = isolated tenant (`user_id` + `broker_id` composite). A Deriv user sees/stores/processes ONLY Deriv streams, Deriv account, Deriv risk/account-info calculations. An MT5 user sees ONLY MT5. No cross-broker or cross-user leakage (`ContextVar[user_id, broker_id]` + row-level `WHERE user_id=:uid AND broker_id=:bid` + per-tenant lake partition). Same `Shared Kernel` + same calculation Modules serve all tenants — no per-broker rewrite.
2. **High-Speed Calculation & Operation — 99.8% Automation:** P95 budgets: Ingestion→Storage <50ms, MarketData→AnalyticsResult <20ms, RiskDecision <10ms, Order→BrokerAck <500ms (broker-bound). Vectorized `numpy/pandas` + `feature_engine` caching + `Redis` + async ingestion. Target 99.8% algo-handled (minimal manual = only `emergency/kill_switch` + `unknown_state` human triage).
3. **Multi-Broker, DBs Remain Diff, Calc Shared:** Raw & normalized data remain **diff per broker, per user** (`data/lake/brokers/{deriv,mt5,headway}/` + DB `WHERE broker_id` + per-user namespace). *Canonical* `MarketData` + `AnalyticsResult` are broker-agnostic, so `M3 Analytics`, `M4 Alpha`, `M5 Risk`, `M6 Execution Intelligence` (algos/costs) are **written once, reused for every broker** via `M2b Reference` mapping (`frxEURUSD` → `EURUSD`, `frxXAUUSD` → `Gold` + `contract_specs` per broker). Account management (`M5 PortfolioState`, `M7 Trust`) is per-`user_id+broker_id` — risk/account-info calc is per-tenant but engine code is shared.


**The platform is NOT a monolith.** It is a **Modular Monolith** composed of **Bounded Engines**.

- **Engines** = Stateful processing systems with a lifecycle (`start`/`stop`/`health`/`process`). They own computation.
- **Services/Modules** = Stateless support (schemas, registries, validators, adapters). They own knowledge.

**10 Truly Isolatable Modules (Isolation Boundary):**
```
M1 FOUNDATION ————————————————————————————————————————————————————————
 [shared/core + shared/contracts (Shared Kernel, jointly owned)]

M2 DATA ────────────────→ M3 ANALYTICS ────────────────→ M4 ALPHA ────────────────→ M5 PORTFOLIO & RISK ────────────────→ M6 EXECUTION ────────────────→ M7 TRUST
 (ingestion→storage)       (pure features)                (Strategy+Signal)          (Portfolio+Risk, atomic)               (Order+Execution, only           (Reconciliation+Accounting,
  broker_feeds/*            per_asset/cross_asset/         11 families +              owns positions + RiskDecision         broker talker)                  append-only ledger)
                            microstructure/macro/event      validator/dedup/freshness  owns ledger pre-condition
M2b REFERENCE (read-only cache, used by M2,M3,M5-M7) ───────────────────────────────────────────────────────────────────────────────────────────────────────┘

Off-main-path (own cadence, still isolatable):
M8 VALIDATION (Backtesting + Simulation + Paper + Canary) — answers “is it safe?”
M9 RESEARCH (Datasets + Experiments) — never imports Execution
M10 EXPERIENCE (Synchronization + API + Command + Frontend) — read-only projections
M11 INTELLIGENCE (AI narrator, allowlist only: analytics.query, portfolio.query) — never trades
M12 OPS (Monitoring + Testing Suites) — reads health from all
```

**Supporting Kernel:**
```
Shared Kernel: contracts/* (8 frozen versioned Pydantic contracts) + core/* (clock, lifecycle, health, broker_context)
— the ONLY coupling. See docs/architecture/isolated_modules.md for Build Independence & Airplane Test.
```

**Success Criteria:** You can replace the entire Data ingestion (e.g., switch Deriv WS to FIX) and Analytics/Strategy/Risk never notice because they only ever see `MarketData` and `AnalyticsResult` contracts.

---

## 2. Design Philosophy — The 10 Commandments + Institutional Hedge Fund Grade

> **HEDGE FUND MANDATE — Speed is mandatory, errors are prohibited, code must be institutional:**
> *Every I/O is `async def + await` (never block), every pure calc is `sync def` (deterministic, frozen), every state change is event-driven via `event_bus` (never poll), every file/module handling is atomic + checksum + idempotent + retry with jitter + dead-letter + well-commented WHY (not just WHAT). P95: `tick→parquet <50ms, analytics <20ms, risk <10ms, order→ack <500ms`. No silent fallback, no calculation error, no abnormality tolerated. Code must be well-written, clean design, async, event-driven, well-commented.*


1. **Contracts Over Code.** Engines never import each other. They communicate only via versioned contracts in `backend/contracts/`. If you need data from another engine, you import its contract, not its implementation.

2. **Directionality Is Law.** Dependency arrow only points forward on the main path. Reverse imports are rejected in CI (`import-linter`). See Section 33.

3. **AI Never Trades.** `backend/intelligence/` is `explain / summarize / retrieve / answer`. It has NO import path to `order` or `execution`. Guarded by `tool_policy.py`.

4. **Internal State ≠ Broker Truth.** Every execution must pass through `reconciliation`. Only reconciliation can mark a fill as truth.

5. **Risk Is Separate From Strategy.** Strategy proposes `Signal( intent=BUY, strength=0.8 )`. Risk decides `RiskDecision( approved=True, approved_qty=0.4, reason="exposure_limit")`. Sizing lives in Risk, not Strategy.

6. **Engine Interface Uniformity.** Every engine implements `BaseEngine` from `backend/app/core/engine_interface.py`:
```python
class BaseEngine(ABC):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def health(self) -> HealthStatus: ...
    # process signature is engine-specific but must accept & return contracts
```

7. **No Business Logic in API.** `backend/app/api/v1/*.py` is a thin router: validate request → call engine → return contract. No calculations in routers.

8. **Configuration Over Code.** No hard-coded symbol, lot size, limit, or broker capability. All in `backend/config/*.yaml` validated by `pydantic-settings` + `parameter_validator.py`.

9. **Failure Is a First-Class State.** Every engine must document and handle: `data_gap`, `broker_disconnect`, `unknown_state`, `stale_signal`, `rejected_order`. See Section 35.

10. **Test Mirrors Prod.** If you create `engines/execution/routing/router.py`, you must create `testing_suite/unit_tests/execution/test_router.py` + `contract_tests/engine_contracts/test_execution_contract.py`.

---

## 2b. Three Configuration Layers + Override Model (Simple: Automatic → Your Override → Reset)

> **Plain English:** Every setting you can change follows one rule: platform gives you a smart default, you can override it, and you can always press *Reset to Default* to go back. Safety limits can never be overridden.

**The 3 Layers:**

1. **Build-time (engineering) — you never touch:** Code, DB schemas, broker adapters, risk engine. Only developers change this.
2. **Platform Runtime (admin) — platform team:** Supported brokers/assets, default risk policies, feature flags, which strategies are active. Admin controls.
3. **Customer Runtime (you) — you control:** Your broker connection, which account (demo/live), allowed assets, alerts, dashboard prefs.

**How override works — same everywhere (risk, alerts, dashboard):**
```text
Automatic Default = 5%  (platform calculates)
If you haven't set anything → Effective = 5%

You set User Override = 3% → Effective = 3% (yours wins, platform does NOT silently overwrite)

You press Reset to Default → User Override removed → Effective = 5% again
If platform later changes Automatic to 4% → Effective = 4% (since you have no override)

Rule: IF user_override EXISTS → effective = user_override ELSE effective = automatic
Hard safety boundaries (e.g. max leverage 50) can NEVER be overridden.
```

This model lives inside `M1.3 Config + M12 Ops` — no new Module.

## 3. Top-Level Architecture — The Canonical Tree (v1.3 BEST APPROACH — 19 Logical Systems → 12 Isolatable Modules, 57 Sub-Modules, 3-Box Storage, 3 Config Layers)

**Isolation Rule:** *Module = build/test/deploy independently. Engine = internal component inside a Module (not independently deployable).* See `docs/architecture/isolated_modules.md` for proofs (Airplane Test, No Shared Mutable State).

```text
financial_analyst_app/               # ← regenerated AFTER v1.1 lock (currently System_Development_Manual/ holds the manual only)
│
├── shared/                           # SHARED KERNEL — jointly owned, versioned VERY slowly
│   ├── contracts/                    # 8 frozen Pydantic contracts (MarketData, AnalyticsResult, Signal, RiskDecision, Order, ExecutionResult, ReconciliationReport, PortfolioState)
│   │   ├── base.py, market_data.py, analytics.py, signal.py, portfolio.py, risk.py, order.py, execution.py, reconciliation.py, errors.py
│   │   └── migrations/               # v1.0 → v1.1 migrations
│   └── core/                         # Foundation library (clock, lifecycle, health, logging, broker_context, engine_interface)
│       ├── config.py, clock.py, lifecycle.py, health.py, logging.py, exceptions.py, feature_flags.py, engine_interface.py
│       └── permissions.py, retry_policy.py
│
├── modules/                          # 10 ISOLATABLE MODULES — each: src/ + tests/ + README + version + health
│   │
│   ├── data/                         # M2 DATA — owns Data Lake + watermark
│   │   ├── ingestion_engine/, broker_feeds/{deriv,mt5,headway}/, normalization_engine/, validation_engine/
│   │   ├── transformation_engine/, order_book_engine/, data_quality_engine/, recovery_engine/, storage_engine/
│   │   └── reference/                # M2b REFERENCE — read-only cache (instrument_master, symbol_mapping, contract_specs)
│   │
│   ├── analytics/                    # M3 ANALYTICS — pure features, no durable state
│   │   ├── profiles/, per_asset/, cross_asset/, portfolio/, microstructure/, macro/, event/
│   │   └── feature_engine.py, data_quality_inputs.py
│   │
│   ├── alpha/                        # M4 ALPHA — Strategy + Signal merged
│   │   ├── strategy/{momentum,trend_following,cross_sectional_momentum,mean_reversion,mean_reversion_grid,statistical_arbitrage,cross_asset_relative_value,microstructure,liquidity_event,systematic_macro,event_driven}/
│   │   ├── signal/{validator, deduplicator, freshness, latency_budget_validator, conflict_detector, queue_prioritizer}/
│   │   ├── base_strategy.py, strategy_registry.py, lifecycle.py, versioning.py, shadow_mode.py
│   │   └── README.md                 # + signal_store (internal state)
│   │
│   ├── portfolio_risk/               # M5 PORTFOLIO & RISK — atomic (owns positions + RiskDecision)
│   │   ├── portfolio/{portfolio_manager, position_manager, allocation, portfolio_state}/
│   │   ├── risk/{pre_trade/{margin_check,exposure_check,...}, post_trade/, sizing/{volatility_sizing}, limits/, monitoring/, emergency/{kill_switches, asymmetric_lock}}/
│   │   └── README.md
│   │
│   ├── execution/                    # M6 EXECUTION — Order + Execution merged, ONLY broker talker
│   │   ├── order/{builder, state_machine, idempotency, provenance}/
│   │   ├── execution/{routing/{router,smart_router}, connectivity/{ws,rest,fix,dma}, broker_adapters/{deriv,mt5,headway}, algorithms/{market,limit,twap,vwap}, market_impact/, transaction_costs/}/
│   │   └── README.md
│   │
│   ├── trust/                        # M7 TRUST — Reconciliation + Accounting merged, owns ledger
│   │   ├── reconciliation/{broker/{order,position,fill}, state/{comparator, unknown_state_handler}, policies/{asymmetric_lock}}/
│   │   └── accounting/{ledger, pnl, fees, financing, valuation}/
│   │
│   ├── validation/                   # M8 VALIDATION — Backtesting + Simulation + Paper + Canary (4 modes, 1 team)
│   │   ├── backtesting/{data/tick_replay, execution_simulation/virtual_broker, validation/{leakage_detector}, performance/}/
│   │   ├── simulation/{monte_carlo, spread_shock, broker_failure, regime_variation}/
│   │   ├── paper_trading/{paper_broker, matching_engine}/
│   │   └── canary/{eligibility, exposure_limits, rollback}/
│   │
│   ├── research/                     # M9 RESEARCH — isolated lab
│   │   └── datasets/, features/{feature_registry}, experiments/{experiment_runner}, attribution/
│   │
│   ├── experience/                   # M10 EXPERIENCE — delivery (no trading logic)
│   │   ├── api/v1/{router, market_data, analytics, signals, portfolio, risk, orders, executions, health}/
│   │   ├── synchronization/{event_publisher, subscription_manager, state_delta, sequence_manager, replay_manager}/
│   │   ├── command/{parser, interpreter, commands/{forex, risk, trade, confirm, ...}}/
│   │   └── frontend/{features/*, realtime/*, state/*}  # or top-level frontend/ that imports from this module via openapi.json
│   │
│   ├── intelligence/                 # M11 INTELLIGENCE — read-only narrator
│   │   └── context_builder.py, prompt_manager.py, factual_validator.py, response_guard.py, tool_policy.py, audit.py
│   │
│   └── ops/                          # M12 OPS — cross-cutting
│       ├── monitoring/{metrics, alerts, health, runbooks/}/
│       ├── testing_suite/{unit_tests, integration_tests, failure_tests, contract_tests}/
│       ├── deployment/{Dockerfile, docker-compose.yml, k8s/}/
│       └── scripts/{seed, migrate}/
│
├── config/                           # YAML overlays (owned by Foundation, consumed by all)
│   ├── assets/{forex,crypto,commodities}.yaml
│   ├── brokers/{deriv,mt5,headway}.yaml
│   ├── strategies/{...}.yaml, execution/{routing,algorithms}.yaml, risk/{limits,sizing}.yaml, system/{system,feature_flags}.yaml
│   └── environments/{development,testing,paper,canary,production}.yaml
│
├── data/lake/                        # Owned by M2, read by M8/M9
│   ├── brokers/{deriv,mt5,headway}/{raw_ticks,candles,order_books,normalized,execution}/
│   └── canonical/{normalized_market_data, reference_data, cross_broker}/
│
└── docs/                             # Manual + per-module READMEs (this folder)
```

**Mental Model (v1.1):**
- `shared/*` = **The Law** (contracts + foundation — changes slowest, 3-team sign-off)
- `modules/data → analytics → alpha → portfolio_risk → execution → trust` = **The Trading Chain** (directional, contracts-only, airplane-testable)
- `modules/validation / research / intelligence / experience / ops` = **The Safety & Delivery Ring** (own cadence, read projections)
- `config/ + data/lake/` = **The Ground** (versioned YAML + replayable parquet)

**Why 10, not 16 flat:** Merges that were chatty or needed atomicity are now *inside* one Module (Alpha=M4, Portfolio&Risk=M5, Execution=M6, Trust=M7, Validation=M8, Experience=M10). This is the isolation boundary where *build independence* is proven (see `isolated_modules.md` §3 — Airplane Test). Engines remain documented in Sections 6-22 as *internal components*.

---

## 4. Core Infrastructure — `shared/core` + `shared/contracts` + `config` — EXPANDED (M1 Foundation, v1.3 BEST APPROACH Postgres+Timescale + 19→12 + Institutional)

# M1 — FOUNDATION (Shared Kernel Foundation Library) — EXPANDED DISCUSSION
### `shared/core` + `shared/contracts` + `config` — The Law That Every Other Module Imports

**Module Type:** Library — **Multi-Tenant Foundation — Institutional** — never deployed alone, imported by M2-M12.
**Owns:** Nothing durable except schemas + in-memory registries. Owns the *law*. If you change law here, all modules pin new version.
**Tenant:** `user_id + broker_id` via `ContextVar` — Deriv tenant never sees MT5, user 42 never sees user 99.

> **This is the EXPANDED discussion for M1.** As we discuss, this will be patched back into `System_Development_Manual.md` Section 4 and replaces the short `01_M1_Foundation.md`. Each sub-module + each file is explained as simply as possible, with pseudocode where it helps, and how it connects to the whole system.

---

## 1. Purpose in One Sentence

> **M1 is the rulebook.** It does NOT trade, does NOT fetch data, does NOT calculate RSI. It tells *how* every other Module should behave: how to start/stop, how to tell time, how to log, how to stay isolated per user+broker, and what a `MarketData` or `Signal` *looks like*.

If M1 is wrong, the whole system is wrong. If M1 is stable, 10 teams can build M2-M12 in parallel without arguing.

---


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution is mandatory <500ms order→ack, <50ms tick→parquet, <20ms analytics, <10ms risk. No calculation error, file handling error, or abnormality is tolerated — every I/O is async `async def` + `await` + `try/except + retry with jitter + dead-letter`, every pure calc is sync `def` + frozen `BaseContract` + golden test, every state change is event-driven via `event_bus` not poll. Code must be well-commented WHY, not just WHAT.*

## 2. The 4 Sub-Modules — What Each Does (Simple)

| Sub | Name | One-Line Job | When You Touch It |
|-----|------|--------------|-------------------|
| **M1.1** | **Contracts (Shared Kernel)** | Defines what `MarketData`, `Signal`, `Order` *look like* (frozen Pydantic schemas). | You add/remove a field that crosses Module boundaries |
| **M1.2** | **Core Runtime** | Gives every Module `start()/stop()/health()`, a `clock`, and `health` aggregation | You change lifecycle, time, or logging |
| **M1.3** | **Config & Environment** | Loads `config/*.yaml + .env` → one `Settings` object, validates it | You add a YAML key (e.g., new broker) |
| **M1.4** | **Security & Isolation** | Ensures `user 42/Deriv` never sees `user 99/MT5` + who is allowed to do what + retry | You touch permissions, broker tokens, or isolation |

**Build order:** `M1.1 → M1.2 → M1.3 → M1.4` — M1.1 is the root; nothing builds without contracts.

---

## 3. M1.1 — CONTRACTS (Shared Kernel) — The Most Important

### Why it exists
10 Modules are written by different people. How does `M2 Data` give `M3 Analytics` a tick without them sharing code? **Answer:** They both import the *same* `MarketData` type from `shared/contracts`. Contracts are the *only* coupling. Change a contract = change the agreement between Modules.

### Files — Simple + Pseudocode + Connection

#### `shared/contracts/base.py` — The Grandparent
**Does:** Every contract inherits this. It says: *“You are frozen (cannot be changed after creation), you have a version, a correlation_id to track the trade end-to-end.”*
**Why:** So a `Signal` created in `M4` can be traced through `M5 → M6 → M7` with the same `correlation_id`.
**Pseudocode:**
```python
# base.py
class BaseContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")  # frozen = no accidental mutation
    version: str = "1.0"                                     # bump when schema changes
    correlation_id: UUID = uuid4()                           # one trade = one id, forever
    trace_id: UUID = uuid4()
    timestamp: datetime = now_utc()
```
**Connects to:** Everything. `M2` creates `MarketData(correlation_id=123)`, `M3` creates `AnalyticsResult(correlation_id=123)`, `M4` creates `Signal(correlation_id=123)` — audit can `SELECT * WHERE correlation_id=123`.

#### `shared/contracts/market_data.py`
**Does:** Defines `MarketData(symbol=EURUSD, broker=deriv, kind=tick, payload=Tick(price=1.08), quality=pristine, watermark=1001)`. `watermark` is a counter per symbol+broker to detect gaps.
**Why:** `M2` is the ONLY writer of this. `M3` trusts `quality` to decide “compute or skip”.
**Pseudocode:**
```python
class MarketData(BaseContract):
    symbol: str          # canonical "EURUSD", not "frxEURUSD"
    broker: str          # "deriv" | "mt5" | "headway"
    kind: Literal["tick","candle","order_book"]
    payload: Tick | Candle  # the actual data
    quality: Literal["pristine","gapped","suspect"]
    watermark: int        # 1000, 1001, 1002 ... gap if 1001 missing
```
**Connects:** `M2 → M3`, `M2 → M8` (backtest replays same type).

#### `shared/contracts/analytics.py` — `AnalyticsResult`
**Does:** `M3` says: “For EURUSD 1m, RSI=68, vol=0.018, regime=trending”.
**Pseudocode:** `class AnalyticsResult(BaseContract): symbol, timeframe, features: dict, regime`
**Connects:** `M3 → M4` (strategy reads `features["rsi_14"]`, never computes RSI itself).

#### `shared/contracts/signal.py` — `Signal`
**Does:** `M4` says: “Strategy trend@v2.1 thinks EURUSD should go LONG, strength 0.76, valid for 60 seconds, here’s why”.
**Key fields:** `strength 0.0-1.0` (NOT qty), `valid_until` (freshness), `shadow` (if True, not traded).
**Pseudocode:**
```python
class Signal(BaseContract):
    strategy_id: str
    symbol: str
    direction: Literal["long","short","exit"]
    strength: float  # 0.76
    valid_until: datetime  # now + 60s — M5 rejects if expired
    explainability: {reason: "EMA crossed", feature_weights: {rsi:0.6}}
```
**Connects:** `M4 → M5` — M5 is the first to see it; M5 converts `strength` → `qty`.

#### `shared/contracts/portfolio.py` — `PortfolioState` + `Position`
**Does:** `M5` says: “User 42/Deriv owns 0.7 lots EURUSD avg 1.08, margin_used 400, available 600”.
**Connects:** `M5 → M3` (M3 reads for portfolio analytics, read-only), `M5 → M6` (M5 gives state to Risk), `M7 → M5` (M7 compares this vs broker truth).

#### `shared/contracts/risk.py` — `RiskDecision`
**Does:** `M5` says: “Signal 123 → APPROVED, qty 0.4 lots, or REJECTED because insufficient_margin”.
**Pseudocode:**
```python
class RiskDecision(BaseContract):
    signal_id: UUID
    approved: bool
    approved_qty: float | None  # None if rejected
    rejected_reason: str | None  # "insufficient_margin"
    limits_checked: {"margin": True, "exposure": False}
```
**Connects:** `M5 → M6` — M6 never builds an Order if `approved=False`.

#### `shared/contracts/order.py` — `Order` + `OrderIntent`
**Does:** `M6` builds `Order(client_order_id=uuid5(correlation_id), symbol, side=buy, qty=0.4, state=pending)`. `client_order_id` is deterministic from `correlation_id` → retrying same signal yields same ID → broker deduplicates.
**Connects:** `M6 → M7` — M7 checks `ExecutionResult` against this Order.

#### `shared/contracts/execution.py` — `ExecutionResult` + `Fill`
**Does:** `M6` says: “Order abc123 filled 0.4 at 1.0843, latency 120ms, slippage 1.2bps”.
**Connects:** `M6 → M7` → `M7` verifies vs broker.

#### `shared/contracts/reconciliation.py` — `ReconciliationReport`
**Does:** `M7` says: “User 42/Deriv: internal positions vs broker positions → matched / mismatched (diff: {internal:0.7, external:0.5}) + resolution: asymmetric_lock”.
**Connects:** `M7 → M5` (lock) + `M10` (UI shows matched/mismatched).

#### `shared/contracts/errors.py` — `ErrorCode`
**Does:** Single list of strings: `insufficient_margin`, `stale_signal`, `unknown_state`, etc. — not free text.
**Connects:** Every rejection uses `ErrorCode` → frontend can translate, alert can route.

#### `shared/contracts/migrations/` — Version Bumps
**Does:** When `MarketData` adds a field `quality_v2`, you put `migrations/v1_to_v2.py` with `def migrate(old_dict) → new_dict` + snapshot test.
**Why:** So old parquet `MarketData v1.0` stored in Lake can still be read after code is `v1.1`.

**M1.1 Build Independence:**
```bash
pytest shared/contracts --snapshot
# no DB, no broker — just schema frozen check. If you add a field without bumping version, test fails.
```

---

## 4. M1.2 — CORE RUNTIME — The Heartbeat

### Why it exists
Every Module needs to `start`, `stop`, report `health`, tell time, and log the same way. M1.2 provides those *once* so `M2 Data` and `M6 Execution` don’t each invent their own clock.

### Files

#### `shared/core/engine_interface.py` — The Contract All Modules Implement
**Does:** Says: “If you are a Module, you MUST have `name`, `start()`, `stop()`, `health()`”.
**Pseudocode:**
```python
class BaseEngine(ABC):  # async lifecycle, well-commented WHY each method
    # WHY async? So start/stop never blocks ingestion. WHY ABC? So every Module must implement health.
    async def start(self):  # async: setup connections, per-tenant vault
        pass
    async def stop(self):  # async: graceful drain, no lost ticks
        pass
    async def health(self) -> Health:  # async: check DB + Redis + external, never block
        pass
    name: str
    def start(self) -> None: ...   # open WS, warm cache, validate config
    def stop(self) -> None: ...    # drain queue, close WS
    def health(self) -> HealthStatus: ...  # returns HEALTHY/DEGRADED/OFFLINE

class HealthStatus(BaseModel):
    engine: str
    status: Literal["healthy","degraded","unhealthy","offline"]
    latency_ms: float | None
    details: dict
```
**Connects:** `M2-M12` each `class DataEngine(BaseEngine)`, `class AlphaEngine(BaseEngine)` — `shared/core/lifecycle.py` can loop `for mod in modules: mod.start()` without knowing which module.

#### `shared/core/lifecycle.py` — The Conductor
**Does:** On app startup: `M1.1 → M1.2 → M1.3 → M1.4 → M2 → M3 → M4 → M5 → M6 → M7` in order; on shutdown: reverse. Handles `SIGTERM`.
**Pseudocode:**
```python
def startup():
    for mod in [foundation, data, analytics, alpha, portfolio_risk, execution, trust]:
        try: mod.start()
        except: mod.health = UNHEALTHY; alert()
def shutdown():
    for mod in reversed(modules): mod.stop()  # execution stops before data, so no new ticks
```
**Connects:** `app/main.py` (FastAPI lifespan) calls this. It’s why `M6 Execution` never starts before `M2 Data` has a clock.

#### `shared/core/clock.py` — The Single Source of Time
**Does:** `clock.now()` → UTC `datetime`. Two implementations: `SystemClock` (real `time.time()`) for live, `SimulatedClock` (fast-forward) for `M8 Backtesting`.
**Why not `datetime.now()` directly?** Because `M8` needs to replay 2024-01-01 ticks *as if* it’s 2024-01-01, and tests need deterministic time.
**Pseudocode:**
```python
class SystemClock:
    def now(self): return datetime.now(timezone.utc)
class SimulatedClock:
    def __init__(self, start): self.t = start
    def now(self): return self.t
    def advance(self, delta): self.t += delta
# All modules do:
#   def __init__(self, clock: Clock): self.clock = clock
#   def on_tick(self): ts = self.clock.now()  # never datetime.now()
```
**Connects:** `M2` timestamps `received_at`, `M3` `AnalyticsResult.timestamp`, `M4` `valid_until` — all via `clock`.

#### `shared/core/state_tracker.py` + `shared/core/health.py`
**Does:** `state_tracker` holds enum `initializing|healthy|degraded|unhealthy|shutting_down` per Module. `health.py` aggregates `mod.health()` from all Modules into `GET /health` and `GET /health/{module}`.
**Pseudocode:**
```python
def get_health():
    return {
        "status": "degraded" if any(m.health().status=="degraded" else "healthy",
        "modules": {m.name: m.health() for m in modules}
    }
```
**Connects:** `M12 Ops` reads this for alerts; `M10 Experience` shows green/yellow/red badges.

#### `shared/core/logging.py` — One JSON Log Line
**Does:** `log.info("signal_generated", engine="alpha", correlation_id=123, trace_id=456)` → JSON `{"timestamp":"...","level":"info","engine":"alpha","correlation_id":"123","msg":"signal_generated"}`.
**Why:** So `grep correlation_id=123` finds the *entire* `tick → ledger` trail across 10 Modules.
**Pseudocode:**
```python
import structlog
logger = structlog.get_logger()
logger.info("risk_decision", signal_id=..., approved=True, qty=0.4, correlation_id=corr)
# never print(), never logger.info(f"qty {qty}") without correlation_id
```
**Connects:** Every Module. `M12` ships logs to Loki/ELK.

---

## 5. M1.3 — CONFIG & ENVIRONMENT — The YAML That Is the Truth

### Why it exists
No hard-coded `if symbol=="frxXAUUSD"` or `rsi_period=14` or `max_exposure=0.1` in code. All numbers live in `config/*.yaml`, loaded once, validated at startup. Change YAML, not code.

### Files

#### `shared/core/config.py` — The Loader
**Does:** Reads `config/**/*.yaml` + `.env` → one `Settings` object via `pydantic-settings`. Validates: “`strategies/momentum.yaml` missing `threshold` → fail fast with loud error, don’t boot half-broken”.
**Pseudocode:**
```python
class Settings(BaseSettings):
    assets: dict = load_yaml("config/assets/forex.yaml")  # {eurusd: {pip:0.0001}}
    brokers: dict = load_yaml("config/brokers/deriv.yaml")
    risk: RiskLimits = RiskLimits(**load_yaml("config/risk/limits.yaml"))  # validates max_exposure 0.1

settings = Settings()  # singleton, imported as `from shared.core.config import settings`

def load():
    try: settings = Settings()
    except ValidationError as e: 
        print(f"CONFIG ERROR: {e} — missing {e.missing_field} in config/..."); sys.exit(1)
```
**Connects:** `M2b` reads `assets/forex.yaml`, `M3` reads `forex_profile` via `M2b`, `M5` reads `risk/limits.yaml`, `M6` reads `execution/routing.yaml`. One import, not 10.

#### `shared/core/environment.py` + `config/environments/*.yaml`
**Does:** `Environment = Literal["development","testing","paper","canary","production"]`. `config/environments/production.yaml` *overlays* on top of base (deep-merge): `production: {risk: {max_exposure:0.05}}` is stricter than `development`.
**Why:** `M8 Validation` runs with `environment=paper` (fake venue), `M12` canary with `canary` (1% exposure). No `if env=="prod"` scattered in code — it’s `settings.env`.
**Pseudocode:**
```python
env = os.getenv("ENVIRONMENT", "development")
base = load_yaml("config/risk/limits.yaml")
overlay = load_yaml(f"config/environments/{env}.yaml")
settings.risk = deep_merge(base, overlay)  # production overrides base
```

#### `shared/core/feature_flags.py` — `config/system/feature_flags.yaml`
**Does:** `feature_flags.is_enabled("enable_stat_arb")` → `false` in prod, `true` in paper. Checked by `M4 Alpha.strategy_registry` and `M6 smart_router`.
**Why not code flag?** Changing a flag should not require redeploy — reload via `SIGHUP` or `scheduling`.
**Pseudocode:**
```python
flags = load_yaml("config/system/feature_flags.yaml")  # {enable_stat_arb: false, enable_headway: true}
def is_enabled(name): return flags.get(name, False)
# M4:
#   if not feature_flags.is_enabled("enable_stat_arb"): return  # skip stat_arb strategy
```

#### `config/` itself (the YAML files)
**Does:** The *truth* for: `assets/*` (which symbols exist), `brokers/*` (which brokers + rate limits), `strategies/*` (params like `rsi_period`), `execution/*` (routing rules), `risk/*` (limits), `system/*` (base_currency, logging level).
**Connection example:** Add new broker `ctrader` → create `config/brokers/ctrader.yaml` + `M2b Reference` reads it → `M2 broker_feeds/ctrader/` and `M6 broker_adapters/ctrader/` appear — no code change in `M3-M5`.

**M1.3 Build Independence:**
```bash
pytest shared/core/config --fixtures=bad_yaml
# expects: startup fails with "missing threshold in strategies/momentum.yaml"
```

---

## 6. M1.4 — SECURITY & ISOLATION — The Guard

### Why it exists
Institutional requirement: `user 42/Deriv` must never see `user 99/Deriv` or `user 42/MT5`. Also: `viewer` role cannot hit `POST /risk/kill_switch`, `AI` cannot call `order`. And broker tokens must never appear in logs.

### Files

#### `shared/core/tenant_context.py` (was `broker_context.py`, now `user_id+broker_id`)
**Does:** Holds two `ContextVar`s: `user_id` and `broker_id`. Set *once* per request/engine job via middleware. Every repository reads them.
**Pseudocode:**
```python
from contextvars import ContextVar
user_ctx = ContextVar("user_id", default=None)
broker_ctx = ContextVar("broker_id", default=None)

# Middleware (API/WS/engine):
def on_request(request):
    user_ctx.set(request.user.id)      # 42
    broker_ctx.set(request.broker)     # "deriv"
    try: yield
    finally: user_ctx.set(None); broker_ctx.set(None)

# Repository (used by M5 portfolio_state, M6 orders, M7 ledger):
def get_positions(db):
    uid = user_ctx.get(); bid = broker_ctx.get()
    if uid is None or bid is None: raise IsolationError("No tenant context — query blocked!")
    return db.query("SELECT * FROM positions WHERE user_id=:uid AND broker_id=:bid", uid, bid)
```
**Connects:** `M2` Lake partitioning (logical `user_id` in `data_catalog`), `M5` `positions`, `M6` `orders`, `M7` `ledger` — all partitioned. `Deriv user` query automatically injects `broker_id=deriv`, so even if code forgets `WHERE broker_id`, the guard *still* injects it.

#### `shared/core/permissions.py` + `services/auth/*` (conceptually inside M1.4)
**Does:** `permissions.can(user, "risk.kill_switch") → True/False` via `RBAC` table: `viewer` can `GET /portfolio`, `trader` can `POST /orders/preview`, `admin` can `POST /risk/kill_switch`. `services/auth/broker_vault.py` stores `deriv_token` encrypted (Fernet/AES), never logs it.
**Pseudocode:**
```python
ROLE_PERMISSIONS = {
    "viewer": ["portfolio.read","market_data.read"],
    "trader": ["portfolio.read","risk.preview","order.preview"],
    "admin":  ["*"]
}
def can(user, action): return action in ROLE_PERMISSIONS[user.role]
# API handler:
#   if not permissions.can(request.user, "risk.kill_switch"): raise 403
```
**Connects:** `M10 Experience` (`api/risk.py` checks `can`), `M10 Command` (`command_permissions.py`).

#### `shared/core/exceptions.py`
**Does:** One hierarchy: `BaseAppError → DataGapError, RiskRejectedError, UnknownStateError` each with `error_code` from `shared/contracts/errors.py`. So `M7` `unknown_state` and `M5` `insufficient_margin` are *typed*, not strings.
**Pseudocode:**
```python
class RiskRejectedError(BaseAppError):
    error_code = ErrorCode.insufficient_margin  # from contracts
    def __init__(self, required, available): self.details = {"required": required, "available": available}
```
**Connects:** `M5` raises `RiskRejectedError`, `M10` translates to `HTTP 422 {error_code, details}`, `M12` alerts on `error_code`.

#### `shared/core/retry_policy.py`
**Does:** `retry_with_backoff(fn, max=3, jitter=True, idempotency_key=client_order_id)` — used by `M2 recovery` (fetch gap) and `M6 execution` (send order).
**Pseudocode:**
```python
def retry(fn, key):
    for attempt in 1..3:
        try: return fn()
        except RetryableError:
            if is_duplicate(key): return get_existing(key)  # idempotency
            sleep(2**attempt + random_jitter)
    raise
# M6:
#   retry(lambda: broker.send_order(order), idempotency_key=order.client_order_id)
```
**Connects:** `M2.7` (recovery throttling), `M6.1` (order retry safe).

#### `services/auth/broker_vault.py` (logically M1.4) — per-user PRIVATE tokens ONLY
**Does:** `vault.get_token(user_id=42, broker=deriv)` → decrypts from DB/Secret Manager (prod). Never returns token in log. **Market-data ticks are NOT via vault** — `M2.1` uses platform singleton feed (one Deriv WS reads to us, fans out to all UIs). Vault is for **private, per-user** operations: `M6 Execution` (`broker.send_order` with YOUR token), `M7` private account stream (`balance/positions` via `vault.get(user_id, broker)`), and `M5 Risk` account-info reads (balance/leverage → different margin/pnl per user even though `FX.EURUSD` + RSI are identical).
**Connects:** `M6 broker_adapters/deriv` and `M7 account_stream` call it at `start()`; `M2.1 market-data` does **NOT** (uses `config/brokers/deriv.yaml` platform token).

**M1.4 Build Independence:**
```bash
pytest shared/core/test_tenant_isolation --expect=blocked
# test: set user_ctx=42,broker=deriv, query with no context → raises IsolationError
# test: user 42/deriv cannot read user 42/mt5 rows
```

---

## 7. How M1 Connects to the Whole System (The “Law” Flow)

```
M1.1 Contracts (MarketData, Signal, RiskDecision...)
  ↑ imported by
M2 Data → creates MarketData(quality, watermark) → M3 Analytics
M3 Analytics → creates AnalyticsResult(features) → M4 Alpha
M4 Alpha → creates Signal(strength, valid_until) → M5 Portfolio & Risk
M5 → creates RiskDecision(approved, qty) → M6 Execution
M6 → creates Order + ExecutionResult → M7 Trust
M7 → creates ReconciliationReport → M5 (asymmetric_lock) + M10 (UI)

M1.2 Core Runtime: M2-M12 all do `class DataEngine(BaseEngine)` — lifecycle loops them.
M1.3 Config: M2b reads assets/forex.yaml, M5 reads risk/limits.yaml, M6 reads execution/routing.yaml — one loader.
M1.4 Isolation: Every DB query in M5 (positions), M6 (orders), M7 (ledger) auto-injects `WHERE user_id=:uid AND broker_id=:bid` via tenant_context — Deriv tenant isolation without each Module remembering.
```

**If you change M1.1 `Signal.valid_until` type from `datetime` to `int`, you must bump `shared/contracts` to `1.1`, add `migrations/v1_to_v1_1.py`, get sign-off from M4+M5+M6, and `contract_tests` snapshot fails until all pin new version.** That’s the isolation guarantee.

---

## 8. Build Independence — The Airplane Test for M1

```bash
# Needs NOTHING except fixtures
pytest shared/contracts --snapshot          # frozen schema
pytest shared/core --fixtures=bad_yaml     # config validation
pytest shared/core/test_tenant_isolation   # user+broker isolation
pytest shared/core/test_clock               # SimulatedClock advances deterministically

# No broker WS, no DB, no M2-M12 running — yet M1 can be tagged v0.1-foundation.
```

This is why **M1 is Phase P01-P04, Days 1-10, must be first** — everyone else `pip install`s it.

---

## 9. Tests & Definition of Done for M1

- [ ] `M1.1` — `contract_tests` snapshot for each of 8 contracts + migration file if bumped + 3-team sign-off
- [ ] `M1.2` — `health()` returns healthy/degraded/offline correctly; `clock` test with SimulatedClock
- [ ] `M1.3` — `bad_yaml` test: missing key → startup fails loud with field name; `.env.example` lists all required keys
- [ ] `M1.4` — `test_cross_tenant_query_blocked` + `test_broker_isolation` + `test_ai_cannot_call_order` (tool_policy) must pass
- [ ] No `shared/core` file imports `modules/*` — verified by `grep shared/core --include="*.py" -r "from modules"` = 0

---



---

## Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P01** | M1.1 Contracts | `shared/contracts/*.py` 8 frozen schemas + migrations + snapshot tests | `pytest contract_tests` pass |
| **P02** | M1.2 Core Runtime | `BaseEngine`, `lifecycle`, `clock`, `health`, `logging` | `GET /health` HEALTHY with mocked Module |
| **P03** | M1.3 Config & Env | `config/**/*.yaml` + `feature_flags` | Startup fails loud with missing YAML key |
| **P04** | M1.4 Security & Isolation | `broker_context`, `permissions`, `vault`, `retry_policy` | `test_cross_broker_query_blocked` pass |

**Milestone:** `v0.1-foundation` — tag after P04. M1 must be first; everyone `pip install`s it.

## 10. What M1 Also Covers Logically (19 Systems → 12 Modules)

> **Plain English:** Research lists 19 systems, but you build 12. `M1` logically *covers* 3 of those systems without new code:
> - **User Management** — identity, login, roles (`Owner/Admin/User`) → `M1.4` + `M10 API` `POST /users` + `Postgres users` table.
> - **Session & Security** — tokens, MFA, secrets (never in logs), vault per-tenant → `M1.4`.
> - **Configuration Management** — 3 layers `Build-time (code) → Platform Runtime (admin defaults) → Customer Runtime (your override)` + `Reset to Default` → `M1.3 Config` stores versioned YAML in Postgres `config_versions`.

Same code, just logical grouping — no new Module.

## 11. Open Questions / Proposed Changes for M1 — RESOLVED v1.4 (2026-09-22)

> **Status: 5/5 RESOLVED per user review — locked as decisions below. No longer open.**

- [x] **M1.1 Contracts — `MarketData.user_id` explicit vs implicit — RESOLVED:** Keep explicit field `MarketData.user_id: UUID | None` in contract, **persist explicitly in parquet/catalog row**, but **not required for shared canonical tick-level market-data** if using global shared-copy storage. Market-data ticks stored once globally (`provider/instrument` key, no user_id duplication) — `user_id` may be `None` for those rows. Private data (`ledger/positions/orders/signals`) keeps `user_id` explicit always. **M2.8 conflict settled:** plain-English box ("One copy of EURUSD Deriv ticks serves 1000 users" `market-data/provider=deriv/instrument=FX.EURUSD/...`) was correct; technical pseudocode `brokers/deriv/user_id=42/...` was the error — fixed in v1.4 to global path. **Bump: minor (add effective_at handling later if needed, but user_id field already exists).**
- [x] **M1.2 Clock — `clock.sleep()` — RESOLVED:** Add `clock.sleep(seconds)` to `Clock` interface. `SystemClock.sleep()` → `await asyncio.sleep()`, `SimulatedClock.sleep()` → advances virtual time instantly (no wall-clock), `await asyncio.sleep(0)` to yield. Needed for **M8 backtesting** to simulate network latency without blocking wall-clock. **Already implemented in `shared/core/clock.py` — verified.**
- [x] **M1.3 Config — feature_flags hot-reload via SIGHUP — RESOLVED:** **Yes, hot-reload.** `feature_flags` must support `SIGHUP` → `reload()` without redeploy so risk-driven disables (kill switch, strategy disable) take effect immediately. Implementation: `shared/core/feature_flags.py` installs `signal.SIGHUP` handler → `reload_flags()` + `lifecycle` propagates to `M4` registry; also file watcher fallback. A flag that needs redeploy defeats its purpose.
- [x] **M1.4 Isolation — composite `user_id+broker_id` vs single `tenant_id` — RESOLVED:** **Keep composite `user_id+broker_id` (ContextVar pair).** You need to query `all of user 42's brokers` and `all Deriv traffic across users` independently (M12 ops, M7 reconciliation) — single UUID would require a join table anyway and loses that direct queryability. Composite is explicit and auditable.
- [x] **M1.4 Vault — broker token storage — RESOLVED:** **Secret Manager per user in production** (rotation + audit logging), **DB-encrypted (Fernet) for paper/dev** (as currently implemented in `shared/core/broker_vault.py`). Prod path: `broker_vault.get()` first checks Secret Manager (`user_id/broker` secret), fallback to DB row for migration; rotation via `vault.rotate()` + audit.

> Previous open questions below archived for history — above are now law for P01-P04. New proposals should use same `Change / Why / Impact / Bump` format.

---

**End of M1 Expanded Discussion — This section is now inline in `System_Development_Manual.md` Section 4 and is the discusson copy for `modules/01_M1_Foundation.md`. When this discussion is accepted, it becomes the law for M1.**

## 5. Contracts — `backend/contracts/` — The Most Important Layer

**If you get contracts wrong, you get the whole platform wrong.**

Every engine communicates via **frozen, versioned Pydantic models**. No dicts, no raw floats crossing boundaries.

```text
backend/contracts/
├── __init__.py
├── base.py                 # BaseContract with `version: str = "1.0"` and `correlation_id: UUID`
├── market_data.py          # From Data -> Analytics/Strategy
├── analytics.py            # From Analytics -> Strategy
├── signal.py               # From Strategy -> Signal -> Portfolio/Risk
├── portfolio.py            # From Portfolio -> Risk
├── risk.py                 # From Risk -> Order   (RiskDecision)
├── order.py                # From Order -> Execution (OrderIntent, Order)
├── execution.py            # From Execution -> Reconciliation (ExecutionResult, Fill)
├── reconciliation.py       # From Reconciliation -> Accounting
└── errors.py               # Rejection reasons, error codes
```

**Canonical Contracts (simplified):**

```python
# market_data.py
class MarketData(BaseContract):
    symbol: str                 # canonical: "EURUSD", "BTCUSD", "Gold"
    broker: str                 # "deriv" | "mt5" | "headway" | "canonical"
    timestamp: datetime         # exchange timestamp, UTC
    received_at: datetime       # ingestion timestamp, UTC
    kind: Literal["tick","quote","trade","candle","order_book"]
    payload: Tick | Quote | Candle | OrderBookSnapshot | TradePrint
    quality: DataQuality        # pristine | delayed | gapped | suspect
    watermark: int              # monotonic sequence per symbol+broker for gap detection

# analytics.py
class AnalyticsResult(BaseContract):
    symbol: str; broker: str; timestamp: datetime
    timeframe: str              # "1m","5m","1h","1d"
    features: dict[str, float]  # e.g., {"rsi_14": 68.2, "vol_20": 0.018}
    regime: str | None          # "trending" | "choppy" | "high_vol"
    provenance: Provenance      # which engines/models/params produced it

# signal.py
class Signal(BaseContract):
    signal_id: UUID; strategy_id: str; version: str
    symbol: str; broker: str; direction: Literal["long","short","exit","hedge"]
    strength: float             # 0.0 - 1.0 (conviction, not size)
    timeframe: str
    valid_until: datetime       # freshness window — Risk rejects if expired
    analytics_refs: list[UUID]  # links to AnalyticsResult
    explainability: Explainability  # why this signal fired, feature weights
    provenance: Provenance

# trade_plan.py  — NEW v1.3 BEST APPROACH (M4.5 → M5)
class TradePlan(BaseContract):
    trade_plan_id: UUID; signal_id: UUID; strategy_id: str; version: str
    instrument_id: str; broker: str; user_id: UUID  # per-tenant
    direction: Literal["long","short"]
    entry: EntrySpec            # {type: MARKET|LIMIT|BREAKOUT|PULLBACK, reference_price, valid_from, valid_until}
    stop_loss: StopSpec         # {type: STRUCTURE|ATR_2x|SWING, price, invalidation, buffer}
    take_profit: TargetSpec     # {type: RR|STRUCTURE|ATR, price, rr}
    timing: TimingSpec          # {valid_from, valid_until, entry_window, timeframe, expiration_condition}
    data_quality: DataQuality; capability_status: CapabilityStatus
    calculation_meta: dict      # {stop_model: "atr_2x", atr: 0.0012, reference: "swing_low"}
    status: Literal["PENDING_RISK"]
    provenance: Provenance      # signal_strength, analytics_ids, config_hash

class EntrySpec(BaseContract): type: str; reference_price: float; valid_from: datetime; valid_until: datetime
class StopSpec(BaseContract): type: str; price: float; invalidation: str; buffer: str
class TargetSpec(BaseContract): type: str; price: float; rr: float | None
class TimingSpec(BaseContract): valid_from: datetime; valid_until: datetime; entry_window: str; timeframe: str

# signal.py
    signal_id: UUID; strategy_id: str; version: str
    symbol: str; broker: str; direction: Literal["long","short","exit","hedge"]
    strength: float             # 0.0 - 1.0 (conviction, not size)
    timeframe: str
    valid_until: datetime       # freshness window — Risk rejects if expired
    analytics_refs: list[UUID]  # links to AnalyticsResult
    explainability: Explainability  # why this signal fired, feature weights
    provenance: Provenance

# risk.py
class RiskDecision(BaseContract):
    signal_id: UUID; decision_id: UUID
    approved: bool; approved_qty: float | None  # None if rejected
    rejected_reason: str | None  # e.g., "exposure_limit", "spread_too_wide", "data_quality_suspect"
    sizing_method: str           # "vol_target" | "fixed_fractional" | "risk_budget"
    limits_checked: dict[str, bool]
    latency_budget_ms: float

# order.py
class OrderIntent(BaseContract):  # Risk -> Order
    signal_id: UUID; risk_decision_id: UUID
    symbol: str; broker: str; side: Literal["buy","sell"]
    qty: float; order_type: Literal["market","limit","stop"]
    limit_price: float | None; time_in_force: str

class Order(BaseContract):        # Order -> Execution
    order_id: UUID; client_order_id: str  # idempotency_key
    intent_id: UUID; symbol: str; broker: str
    side: str; qty: float; order_type: str; price: float | None
    state: Literal["pending","submitted","partially_filled","filled","cancelled","rejected","unknown"]
    broker_capability_checked: bool


**Simple schemas you will see stored (research §6-8 minimum, we extend with per-tenant):**

*Raw tick — as Deriv sent, never changed (immutable):* 
```json
{"raw_event_id": "raw_01", "provider": "deriv", "received_at": "2026-09-22T12:00:01Z", "provider_message_type": "tick", "provider_event_time": 1234567890, "schema_version": "deriv_tick_v1", "user_id": null, "payload": {"tick": {"ask":1.08501, "bid":1.08499, "epoch":1234567890, "id":"tick-id-123", "pip_size":5, "quote":1.085, "symbol":"frxEURUSD"}}}
```

*Canonical — we translate to internal language (frxEURUSD→EURUSD) + 3 times + per-tenant:* 
```json
{"event_id": "evt_01", "event_type": "QUOTE_TICK", "provider": "deriv", "provider_symbol":"frxEURUSD", "instrument_id":"FX.EURUSD", "provider_event_id":"tick-id-123", "provider_event_time":"2026-09-22T12:00:00Z", "received_time":"...120Z", "processed_time":"...130Z", "price":1.085, "bid":1.08499, "ask":1.08501, "derived":{"mid":1.085, "spread":0.00002}, "quality":{"status":"VALID"}, "user_id":"42", "broker":"deriv", "watermark":1001, "schema_version":"canonical_quote_v1"}
```

*1m Candle — built from many ticks, with quality:* 
```json
{"instrument_id":"FX.EURUSD", "interval":"1m", "interval_start":"2026-09-22T12:00:00Z", "interval_end":"2026-09-22T12:01:00Z", "open":1.08498, "high":1.0852, "low":1.0849, "close":1.0851, "tick_count":137, "average_spread":0.00002, "min_spread":0.00001, "max_spread":0.00004, "expected_duration_ms":60000, "observed_duration_ms":59800, "quality_status":"COMPLETE", "aggregation_version":"agg_v2"}
```
` No volume/VWAP/OBV unless true volume exists — Deriv ticks → UNAVAILABLE, not faked.`

*Every schema has `schema_version` (v1→v2 via migration) so history stays reproducible.*

# execution.py
class ExecutionResult(BaseContract):
    order_id: UUID; broker_order_id: str | None
    fills: list[Fill]; avg_fill_price: float | None
    state: str; latency_ms: float; slippage_bps: float | None
    raw_broker_response: dict | None  # for audit, never for logic
```

**Contract Rules:**
- All contracts are **frozen** (`model_config = ConfigDict(frozen=True)`) and **validated** at boundaries.
- Version bump (`1.0 -> 1.1`) requires migration in `backend/contracts/migrations/` and is tested in `contract_tests/`.
- Never add broker-specific fields to a canonical contract. Use `execution_profiles` in reference_data to map.
- Every contract carries `correlation_id` and `trace_id` for end-to-end audit.

---
# Architecture Bible — Part 2: Engines Deep Dive

---

## 6. Data Engine — `backend/engines/data/` — The Foundation — EXPANDED (M2 Data, v1.3 BEST APPROACH 3-Box Storage + Institutional Async)

# M2 — DATA (Market Data Platform) — EXPANDED DISCUSSION — v1.4 CORRECTED [18-point audit]

### Owns the entire tick→candle→Lake lifecycle. The ONLY module that talks to external market-data. Sync M2b lookups, async M2 I/O.

**Module Type:** Stateful — 8 Sub-Modules, strictly ordered pipeline. **Async I/O everywhere** (WS/REST/parquet), **sync pure lookups** to M2b.
**Owns State:** `data_lake` (parquet, **global shared market-data** `provider/instrument` + catalog) + `watermark_registry` (processing watermark per `instrument_id+broker`, plus `source_sequence` tracking) + `source_identity_registry`. **No private market-data per-tenant** — see §10 resolution.
**Input:** Raw broker ticks (external WS/REST **per-broker singleton** via platform feed — **NOT per-tenant**; one Deriv WS serves 1000 users. Private per-user account streams are separate via M1.4 vault/M7.)
**Output:** `MarketData(instrument_id=FX.EURUSD, reference_snapshot_id=REF-..., source_sequence, watermark, quality)` — **canonical, global, versioned lineage — fanned out to all UIs of that broker**

> **Expanded discussion for M2 v1.4** — fixes lake global-vs-tenant contradiction, watermark terminology, reference lineage. Patches into master §6.

---

## 1. Purpose in One Sentence
> **M2 fetches messy broker ticks ONCE per broker (one Deriv WS reads to us), cleans via M2b identity, tags quality, fixes gaps, stores ONE global canonical `MarketData` (instrument_id=FX.EURUSD, reference_snapshot_id) and STREAMS it to all users' UIs who use that broker — while risk/P&L/balance/ledger are computed per-user from PRIVATE account info (so same EURUSD → same RSI, but different margin/pnl per account).**

M2 knows **"What happened to FX.EURUSD at 10:31:42.123?"** (observations). M2b knows **"What is FX.EURUSD?"** (identity).

> **INSTITUTIONAL HEDGE FUND GRADE** — same as before: `tick→parquet <50ms` async, event-driven, well-commented WHY.

---

## 2. The 8 Sub-Modules — What Each Does (corrected)

| Sub | Name | One-Line Job | Key Correction v1.4 |
|-----|------|--------------|---------------------|
| **M2.1** | Ingestion | Opens **per-broker SINGLETON WS/REST** (one Deriv connection reads raw ticks to us, then fanned-out to all UIs of that broker) | **One connection per broker, storage global, fan-out to all tenants** |
| **M2.2** | Normalization | `frxXAUUSD → COMMODITY.XAUUSD`, `frxEURUSD → FX.EURUSD` via **M2b sync lookup**, stamps `reference_snapshot_id`, converts time → UTC | **Now stamps `reference_snapshot_id` + handles `instrument_id` not `symbol`** |
| **M2.3** | Validation | Drops future/negative/inverted ticks | **Uses `source_sequence` + `watermark` distinction** |
| **M2.4** | Transformation | Tick → `Candle(1m,1h)` | Unchanged |
| **M2.5** | Order Book | Keeps depth, detects **source_sequence gaps** | **Reads `has_depth` via M2b sync before expecting depth** |
| **M2.6** | Data Quality | Marks `pristine/gapped/suspect`, dedupes | Unchanged |
| **M2.7** | Recovery | Finds missing **watermarks (processing seq)**, fetches gap, backfills | **Clarified: watermark = processing seq, not broker seq** |
| **M2.8** | Storage & Lake | Writes **global** `provider=deriv/instrument=FX.EURUSD/date=.../candles.parquet` + catalog with `reference_snapshot_id` | **CRITICAL: market-data path has NO user_id** |

**Pipeline (strict + dependency):**

```
M2b REFERENCE (read-only, sync, shared (instrument,broker), versioned snapshots REF-...)
   │  instrument_id, mapping, specs(effective_at), has_depth
   ▼ (M2 reads M2b via ReferenceService — sync, no async, no circular)
External Broker WS/REST (per-BROKER singleton — ONE Deriv WS via platform feed, NOT per-tenant)
   │
   ▼
M2.1 Ingestion (per-broker singleton WS, captures source_sequence + source_event_id, fans out to all UIs)
   │
   ▼ RawDerivTick(frxXAUUSD, source_sequence=845921, source_event_id=tick-id-123)
M2.2 Normalization (sync: reference.to_canonical → FX.EURUSD, reference.get_spec(effective_at), stamp reference_snapshot_id)
   │
   ▼ MarketData(instrument_id=FX.EURUSD, reference_snapshot_id=REF-2026-09-22-014, source_sequence=845921, watermark=1001, quality=pristine)
M2.3 Validation (drops bad, checks watermark ordering)
   │
   ├──► M2.4 Transformation (ticks→1m→5m→1h, base 1m)
   └──► M2.5 Order Book (only if reference.has_depth==true)
            │
            ▼
M2.6 Quality (pristine/gapped/suspect, dedup by source_event_id + instrument_id)
   │
   ▼
M2.7 Recovery (gap in watermark 1001 missing → REST fetch, backfill)
   │
   ▼
M2.8 Storage (global Lake: provider=deriv/instrument=FX.EURUSD/.../candles.parquet — NO user_id) + catalog row (instrument_id, reference_snapshot_id, source_range, watermark_range, checksum)
   │
   ▼ Canonical MarketData (global)
   │
   ├──► M3 Analytics (reads global candles + spec via M2b effective_at=candle.timestamp)
   ├──► M5 Risk (reads global quality + spec effective_at, but portfolio state per-tenant)
   └──► M8 Replay (replays SAME global ticks with SAME reference_snapshot_id — reproducible)
```

**Dependency arrow (corrected, no circular):**

```
M2 ──depends on──► M2b (M2 imports ReferenceService via `reference` singleton)
M2b ──never depends on──► M2 (enforced: `import-linter: source_modules=m2b forbidden_modules=m2`)
M2b → M2 is the data-flow arrow (M2b provides facts to M2), but import arrow is M2 → M2b.
```

---

## 3. M2.1 — INGESTION — The Door (per-broker SINGLETON, global storage + fan-out to all tenants)

### Why
Brokers speak different languages (Deriv WS, MT5 bridge). M2.1 is ONLY place that knows broker-native protocols. Everyone else sees canonical. **One platform connection per broker** reads market-data ticks (public feed) — not one per user.

### Files — Simple + Pseudocode + Connection — v1.4 corrected per 2026-09-22 (singleton fan-out)

#### `ingestion_engine/ingestion_manager.py` — The Starter/Stopper (async, per-broker singleton)
**Does:** `start(instrument_id=FX.EURUSD, broker=deriv)` — **NO user_id** (platform singleton). One Deriv WS streams `frxXAUUSD`, normalizes once, stores once, **broadcasts to all UIs** subscribed to `deriv`.
**Pseudocode (corrected — singleton):**
```python
# Market-data feed is PUBLIC per-broker — one WS for all tenants of that broker
async def start(instrument_id: str, broker: str):  # NO user_id
    stream_cls = source_registry.get(broker)  # DerivStream
    conn = await connection_manager.get_or_create(broker)  # per-BROKER singleton WS
    stream = stream_cls(instrument_id, conn)
    await stream_manager.subscribe(stream)  # → M2.2 normalize once → fan-out
    # Fan-out after M2.8: internal pub/sub + WebSocket broadcast to UIs
    # await broadcast_manager.publish(f"market-data:{broker}:{instrument_id}", canonical_tick)
```

#### `ingestion_engine/connection_manager.py` — The Keeper (per-broker singleton, NOT per-tenant)
**Why singleton if vault is per-tenant?** **Market-data ticks are PUBLIC** — `FX.EURUSD` on Deriv is identical for user 42 and 99. Opening 1000 WS wastes 1000× connections. So M2.1 uses **platform credentials** from `config/brokers/deriv.yaml` (or shared app_id) for ONE WS. **Per-user vault is for PRIVATE operations only:** `M6 Execution` (`send_order` with your token) and `M7` account stream (`balance/positions` via `vault.get(user_id, broker)`). Separation is explicit.

```python
# per-BROKER singleton pool: {broker: ws} — NOT {(user_id, broker): ws}
async def get_or_create(broker: str):
    if broker not in pool:
        # PUBLIC feed — platform token from config, not vault.get(user_id, broker)
        ws = await connect(brokers[broker].ws_url, token=brokers[broker].platform_token)
        pool[broker] = ws
        asyncio.create_task(heartbeat(ws))
        logger.info("ingestion.connected", broker=broker, singleton=True)
    return pool[broker]

# Private per-user connections live elsewhere: M7 account_stream/connection_manager.py
# async def get_private_stream(user_id, broker): ws = await connect(..., token=vault.get(user_id, broker))
```

#### `broker_feeds/deriv/stream.py` — Captures source identity
**Does:** On WS message, captures **source identity** separately from processing watermark.
**Pseudocode (v1.4):**
```python
# models.py
class RawDerivTick(BaseModel):  # internal only
    native_symbol: str  # "frxXAUUSD"
    native_price: float
    provider_event_time: int  # epoch
    source_sequence: int | None = None  # broker seq if provided (may be None for Deriv)
    source_event_id: str  # tick-id-123 — for dedup

# stream.py
async def on_ws_message(msg):
    raw = RawDerivTick(
        native_symbol=msg["tick"]["symbol"],
        native_price=msg["tick"]["quote"],
        provider_event_time=msg["tick"]["epoch"],
        source_sequence=msg["tick"].get("seq"),  # may be absent
        source_event_id=msg["tick"]["id"],
    )
    await stream_manager.on_tick(raw)  # → M2.2 with source identity preserved
```

### Storage contradiction resolved (v1.4)

**Global market-data (shared, no user_id):**
```
market-data/provider=deriv/instrument=FX.EURUSD/date=2026-09-22/data_type=candles/interval=1m/candles.parquet
market-data/provider=deriv/instrument=COMMODITY.XAUUSD/date=2026-09-22/data_type=ticks/ticks.parquet
```
*Why global?* `FX.EURUSD` on Deriv is same for user 42 and 99 — one copy.

**Private per-tenant (WHERE user_id+broker_id):**
```
private/broker=deriv/user_id=42/date=2026-09-22/signals.parquet
private/broker=deriv/user_id=42/date=2026-09-22/trade_plans.parquet
private/broker=deriv/user_id=42/date=2026-09-22/orders.parquet
private/broker=deriv/user_id=42/date=2026-09-22/ledger.parquet
```
*Your orders/ledger are isolated via `WHERE user_id=42 AND broker_id=deriv` + path.*

**Early M2 draft incorrectly wrote `brokers/deriv/user_id=42/symbol=EURUSD` for market-data** — that duplicated EURUSD 1000×. Fixed: market-data path has **no user_id**.

---

## 4. M2.2 — NORMALIZATION — The Translator (now stamps reference lineage)

### Why
`frxXAUUSD` → `COMMODITY.XAUUSD`, `frxEURUSD` → `FX.EURUSD` so M3 can write RSI once.

### Files — v1.4 with reference_snapshot_id + sync M2b lookups

#### `normalization_engine/engine.py`
**Does:** `RawDerivTick(frxXAUUSD, 123) → MarketData(instrument_id=COMMODITY.XAUUSD, reference_snapshot_id=REF-2026-09-22-014, source_sequence=845921, watermark=1001, ...)`
**Pseudocode (corrected — sync M2b, stamps lineage):**
```python
def normalize(raw: RawDerivTick, user_id: str, broker: str) -> MarketData:  # sync: M2b lookups are sync
    # M2b lookups — SYNC, no await, <1µs
    instrument_id = reference.to_canonical(raw.native_symbol, broker=broker)  # frxXAUUSD → COMMODITY.XAUUSD
    spec = reference.get_spec(instrument_id, broker=broker, effective_at=to_utc(raw.provider_event_time))  # effective-dated
    snapshot_id = reference.current_snapshot_id()  # REF-2026-09-22-014 — for lineage
    price = round(raw.native_price, spec.tick_size)
    ts = to_utc(raw.provider_event_time)
    # Watermark is M2's own processing seq — NOT from M2b, NOT from broker seq
    wm = watermark_registry.next(instrument_id, broker)  # global processing seq (not per-user)
    return MarketData(
        instrument_id=instrument_id,  # FX.EURUSD — canonical
        symbol=instrument_id.split(".")[-1].replace("XAUUSD","Gold"),  # display
        broker=broker,
        kind="tick",
        payload=Tick(price=price),
        exchange_timestamp=ts,
        received_at=clock.now(),
        reference_snapshot_id=snapshot_id,  # NEW — lineage
        source_sequence=raw.source_sequence,  # broker seq (may be None)
        source_event_id=raw.source_event_id,  # tick-id-123
        watermark=wm,  # processing watermark
    )
```
**Connects:** `M2.1 → M2.2 → M2.3`. Uses `M2b ReferenceService` (sync) + `M1 clock` + `watermark_registry` (global, not per-user — since storage is global).

### Terminology clarified (v1.4)

| Term | Who owns | Example | Used for |
|------|----------|---------|----------|
| `source_sequence` | Broker → M2.1 captures | `845921` (Deriv) | Detect broker-level gaps (if broker provides seq) |
| `source_event_id` | Broker → M2.1 captures | `tick-id-123` | Dedup (exactly-once) — hash + id |
| `watermark / processing_sequence` | **M2.7 owns & generates** | `1001, 1002...` per `instrument_id+broker` | **Gap detection & recovery** (M2.7's key), not M2b |
| `reference_snapshot_id` | M2b owns, M2 stamps | `REF-2026-09-22-014` | Historical reproducibility |

**M2b knows nothing about `source_sequence/watermark`.** It only maps `frxXAUUSD → COMMODITY.XAUUSD`.

---

## 5. M2.3 — VALIDATION — The Bouncer (uses watermark, not M2b)

Identical concept, but now validates `watermark` ordering (processing seq) separate from `source_sequence`.

```python
def validate(md: MarketData):
    if md.exchange_timestamp > md.received_at + timedelta(seconds=5):
        return Drop(reason="future_timestamp")
    # watermark gap detection is done in M2.7, not here — here just sanity
    if md.payload.price <= 0: return Drop(reason="negative_price")
    return md
```

---

## 6. M2.4 / M2.5 — Transformation / Order Book

**M2.5 now checks M2b sync before expecting depth:**

```python
def on_order_book_delta(delta, instrument_id, broker):
    caps = reference.get_capabilities(instrument_id, broker)  # sync
    if not caps.has_depth:
        logger.debug("depth not expected for %s %s", instrument_id, broker)
        return
    # ... book logic with source_sequence gap detection
    if delta.source_sequence != last_seq + 1:
        recovery_engine.trigger(instrument_id, broker)
```

---

## 7. M2.6 — DATA QUALITY — unchanged

Dedup by `source_event_id + instrument_id` (not watermark alone). `quality` still `pristine/gapped/suspect`.

---

## 8. M2.7 — RECOVERY — Watermark is processing seq (M2's)

**Pseudocode (clarified):**

```python
# watermark_registry — global per instrument+broker (not per-user, since storage global)
def next_watermark(instrument_id: str, broker: str) -> int:  # sync
    key = (instrument_id, broker)
    last = db.get(f"watermark:{key}") or 0  # global
    db.set(f"watermark:{key}", last+1)
    return last+1

def on_gap(instrument_id, broker, missing_watermarks=[1001]):
    interval = gap_analyzer.to_interval(missing_watermarks)
    source = selector.choose(instrument_id, broker)
    for c in chunks: data = await fetch(c, source); storage_engine.write_backfill(data)
```

---

## 9. M2.8 — STORAGE & LAKE — Global Market-Data + Per-Tenant Private (corrected)

### Global Lake (shared, no user_id)

```python
async def write(md: MarketData):  # async: parquet atomic write
    # GLOBAL path — no user_id!
    path = f"market-data/provider={md.broker}/instrument={md.instrument_id}/date={md.exchange_timestamp.date()}/data_type=ticks/ticks.parquet"
    # Also write canonical + candles similarly
    # Catalog row includes lineage
    catalog.insert(
        instrument_id=md.instrument_id,
        broker=md.broker,
        date=md.exchange_timestamp.date(),
        reference_snapshot_id=md.reference_snapshot_id,  # NEW — lineage
        source_sequence_range=(md.source_sequence, md.source_sequence),
        watermark_range=(md.watermark, md.watermark),
        rowcount=1,
        checksum=sha256(md),
    )
```

### Private per-tenant (WHERE user_id+broker_id) — NOT in M2.8, but in M5/M6/M7

Signals, TradePlans, Orders, Ledger are **never in global Lake** — they are per-tenant tables + private parquet:

```python
# Example: ledger — per-tenant via M1.4 tenant_context
def append_fill(fill, user_id, broker):
    db.execute("INSERT INTO ledger (user_id, broker_id, ...) VALUES (:uid, :bid, ...)", uid=user_id, bid=broker)
    # private parquet
    path = f"private/broker={broker}/user_id={user_id}/date={date}/ledger.parquet"
```

**Tenant isolation example (corrected per 2026-09-22 — singleton fan-out):**
- **Market-data:** Platform `Deriv` singleton WS → ingests `frxXAUUSD` once → global `market-data/provider=deriv/instrument=COMMODITY.XAUUSD/...` (shared) → **fanned-out via pub/sub to UIs of all users who use `deriv`** (user 42 and 99 see same tick in real-time, `tick→parquet<50ms + broadcast<20ms`)
- **Private per-user:** `user 42/Deriv` risk/PnL/balance → `WHERE user_id=42 AND broker=deriv` → `MaxRisk: margin_used = position.qty * price / leverage(42)` — **same FX.EURUSD + same RSI=68, but different `available_balance` → different `approved_qty` / PnL**
- `user 99` sees same market-data + same analytics, but his `orders/ledger` are isolated, zero rows for 42. **General calculations identical, personal results diverge by account info.**

**High-speed:** `M2.1→M2.8` async `anyio` p95 tick→parquet `<50ms`, **M2b lookups sync `<1µs`** so not in budget.

---

## 10. How M2 Connects to Whole System (corrected)

```
External Deriv WS (per-BROKER singleton via platform feed — ONE connection fans out to all tenants; private tokens only for M6/M7)
   │
   ▼
M2.1 Ingestion (singleton, captures source_sequence + source_event_id, broadcasts)
   │
   ▼ (sync M2b: frxXAUUSD → COMMODITY.XAUUSD, stamp REF-...)
M2.2 Normalization (canonical instrument_id + reference_snapshot_id + watermark)
   │
   ▼
M2.3 Validation → M2.4 Candles / M2.5 Book (M2b.has_depth sync check)
   │
   ▼
M2.6 Quality → M2.7 Recovery (watermark gaps) → M2.8 Storage (GLOBAL market-data provider/instrument, catalog with snapshot_id)
   │
   ▼ Global Canonical MarketData
   │
   ├──► M3 Analytics (reads global + spec via M2b effective_at=candle.timestamp)
   ├──► M5 Risk (reads quality via M2.6 + spec effective_at)
   └──► M8 Replay (replays same global ticks with same REF — reproducible)
```

---

## 11. Build Independence — Airplane Test for M2 (corrected)

```bash
pytest modules/data --fixtures=recorded_raw_ticks.parquet --mock-reference
# No Analytics, no Risk, no broker WS needed — recorded ticks → expect MarketData(instrument_id=FX.EURUSD, reference_snapshot_id=REF-...)
# Global storage: same ticks ingested by user 42 or 99 → same global parquet (no duplication)
# Per-broker singleton: mocked platform WS, but Lake remains global + fan-out mocked
```

---

## 12. Open Questions for M2 — Resolved v1.4

- [x] Lake partition: market-data **global** `provider/instrument` (no user_id), private `broker/user_id` for orders/ledger — **resolved**
- [x] Watermark vs source_sequence — clarified: watermark = M2 processing seq, source_sequence = broker seq, M2b knows neither
- [x] New broker `ctrader` → new `broker_feeds/ctrader/` + `config/brokers/ctrader.yaml` — `MarketData(instrument_id)` unchanged
- [x] `order_book` stay inside M2 (reads `has_depth` via M2b)
- [ ] Should `watermark` also be stored per `source` (Deriv WS vs REST backfill) or single global?

## 7. Reference Data Service — `backend/services/reference_data/` — The Knowledge Base — EXPANDED (M2b Reference, v1.3 Institutional)

# M2b — REFERENCE (Knowledge Base) — EXPANDED DISCUSSION — v1.4 CORRECTED [18-point audit]

### Read-only reference-data service with versioned state and cache (NOT stateless)

**Module Type:** Read-only reference-data service with versioned state and cache — runtime lookups are **synchronous, deterministic, pure cache lookups** (`<1µs`), state is **versioned snapshots** (`reference_snapshot_id = REF-YYYY-MM-DD-NNN`), cache is **shared across tenants** (not per-user).
**Owns State:** `reference_data` versioned snapshot table (immutable snapshots: `version 12 @ 2026-01-01`, `version 13 @ 2026-06-01`, `version 14 @ now`) — **shared truth**, not per-tenant. Runtime cache `Redis/in-memory` keyed by `(instrument_id, broker_id)` — **no user duplication**.
**Input:** `config/assets/*.yaml` + `config/brokers/*.yaml` **+ validation gate** (fail startup on duplicate/invalid) → published snapshot
**Output:** Via **single `ReferenceService` facade** (stable boundary): `reference.to_canonical(...)`, `reference.get_spec(..., effective_at)`, `reference.is_open(...)`, `reference.get_capabilities(...)` — all **sync, deterministic, read-only**

> **Why not `Module Type: Stateless registry/cache`?** That contradicts `Owns State: versioned snapshot table`. Truth: **persistent state is versioned & immutable snapshots**; **runtime lookups are stateless from caller's perspective** but **module owns versioned state**. Historical backtest must be able to `get_spec(FX.EURUSD, deriv, effective_at=2026-03-15)` → version 12, not today's 14.

---

## 1. Purpose in One Sentence
> **M2b answers "What is this instrument?" — its identity, specs, calendar, capabilities — as versioned reference facts. Never "What happened at 10:31:42.123?" (that's M2). Never trading decisions (that's M6).**

M2b prevents `if symbol=="frxXAUUSD"` scattered in code. One place knows `frxXAUUSD → COMMODITY.XAUUSD` (`display: Gold`), `tick_size=0.001`, `deriv: market/limit supported, depth unavailable`, `trading hours 00:00-23:59`.

### Canonical Identity — The Most Important Correction

**Formal definitions (single source, no ambiguity):**

| Term | Example | Used by | Meaning |
|------|---------|---------|---------|
| `canonical_instrument_id` | `FX.EURUSD`, `COMMODITY.XAUUSD`, `BTCUSD` | **All modules — primary key** | Unique, namespaced, immutable id. `FX.` = forex, bare/`CRYPTO.` = crypto, `COMMODITY.` = commodity |
| `canonical_symbol` | `EURUSD` | API display, logs | Short display, derived from instrument_id suffix (not stored separately) |
| `display_name` | `Gold`, `Bitcoin` | UI only | Human-readable |
| `broker_native_symbol` | `frxEURUSD`, `frxXAUUSD` | Only inside M2.1/M6.4 | Never leaks beyond M2/M6 translators |

**Contract fields (after v1.4):**

```python
MarketData(instrument_id="FX.EURUSD", symbol="EURUSD", broker="deriv", display_name="EURUSD", reference_snapshot_id="REF-2026-09-22-014", ...)
TradePlan(instrument_id="FX.EURUSD", ...)
get_spec(instrument="FX.EURUSD", broker="deriv", effective_at=None) # None → current
```

**Mapping table (canonical):**

| broker_native | → canonical_instrument_id | → canonical_symbol | → display_name |
|---------------|---------------------------|--------------------|----|
| `frxEURUSD` (Deriv) | `FX.EURUSD` | `EURUSD` | `EURUSD` |
| `EURUSD` (MT5) | `FX.EURUSD` | `EURUSD` | `EURUSD` |
| `frxXAUUSD` (Deriv) | `COMMODITY.XAUUSD` | `XAUUSD` | `Gold` |

No module may use `EURUSD` as instrument_id and `FX.EURUSD` interchangeably. `instrument_id` is the law; storage partition is `instrument=FX.EURUSD` (never `instrument=EURUSD` ambiguous).

---

## 2. Invariants — M2b-I01 … I08 (new, enforceable)

| ID | Invariant | Enforcement |
|----|-----------|-------------|
| **M2b-I01** | **Canonical Identity** — Every supported instrument has exactly one `canonical_instrument_id` (`FX.EURUSD`). No duplicates. | Validation gate: duplicate id → fail startup |
| **M2b-I02** | **Broker Mapping Determinism** — Every `broker_native_symbol` maps deterministically to one `canonical_instrument_id`. | `symbol_mapping` dict + test `to_canonical(to_native(id))==id` |
| **M2b-I03** | **Reference Immutability** — Published snapshots (`REF-2026-09-22-014`) are immutable, never `UPDATE`. New version = new snapshot. | DB `immutable` flag + `INSERT only` |
| **M2b-I04** | **Effective Dating** — Historical queries use reference version effective at `effective_at` timestamp. `get_spec(..., effective_at=2026-03-15) → v12` not current. | `WHERE effective_from <= :ts ORDER BY effective_from DESC LIMIT 1` |
| **M2b-I05** | **No Trading Logic** — M2b provides facts (`commission=X`), never decisions (`route to deriv`). Decisions stay in M6. | `grep M2b -r "route"` must be 0 + import-linter |
| **M2b-I06** | **Shared Truth** — Reference data shared across tenants (`(instrument_id, broker_id)` key, no user_id). Unless fact itself tenant-specific (never for specs). | Cache key is `(instrument_id, broker_id)`, not `(user_id, ...)` |
| **M2b-I07** | **Validation Before Publication** — Invalid YAML cannot become active snapshot (unknown instrument, missing `tick_size`, bad session). | `reference_validator` → `FAIL STARTUP` with field name |
| **M2b-I08** | **Deterministic Lookup** — Same `(instrument_id, broker_id, effective_at, snapshot_id)` → same result, always. | Pure sync lookup + golden test per snapshot |

---

## 3. What M2b Owns vs What M2 Owns — The Clean Boundary

| M2b — **Knows what EURUSD *is*** (identity, facts) | M2 — **Knows what *happened* to EURUSD at 10:31:42.123** (observations) |
|-----------------------------------------------------|--------------------------------------------------------------------------|
| Instrument identity (`FX.EURUSD`, asset class) | Tick, Quote, Trade, OrderBook update |
| Symbol mappings (`frxXAUUSD → COMMODITY.XAUUSD`) | Timestamp (`exchange_timestamp`, `received_at`, `processed_at`) |
| Contract specs (`contract_size`, `tick_size`, `pip`, `min_qty`, `qty_step`, `margin rules`) — **versioned, effective-dated** | Source identifiers (`source_sequence`, `source_event_id`, `broker_native_symbol`) |
| Trading calendar (`sessions, holidays, DST`) — **effective-dated** | Processing identity (`processing_sequence / watermark` — M2's internal ordering, not M2b) |
| Broker capabilities (`market/limit/stop supported, has_depth`) | Processing quality (`pristine/gapped/suspect`), `quality_status` |
| Execution reference facts (`commission, financing, typical slippage params` — descriptive) | Recovery (`gap detection via watermark`, backfill) |
| Temporal reference events (`corporate_actions`, `rollovers` — see §6) | Storage (`Lake parquet`, `catalog`) |

**Diagram:**

```
M2b REFERENCE (read-only truth, versioned snapshots)
   │   instrument_id, specs, calendar, capabilities, mapping, execution facts
   │   sync lookup <1µs, shared cache (instrument,broker)
   │
   ├──► M2.2 Mapping (frxXAUUSD → COMMODITY.XAUUSD) ──► canonical instrument_id
   ├──► M2.3 Rules (min_qty/step validation) — reads spec via M2b
   ├──► M2.5 Book — reads has_depth via M2b before expecting depth
   │
   ▼
M2 DATA (stateful, per-tenant connections, async I/O)
   M2.1 Ingestion (per-tenant WS, source_sequence) → M2.2 Normalization (stamp reference_snapshot_id) → M2.3 Validation → M2.4/2.5 → M2.6 Quality → M2.7 Recovery (watermark gaps) → M2.8 Storage (global market-data + per-tenant private)
   │
   ▼ Canonical MarketData(instrument_id=FX.EURUSD, reference_snapshot_id=REF-..., watermark=1001, source_sequence=845921, ...)
   │
   ├──► M3 (specs for pip calc, but calc is shared code)
   ├──► M5 (specs for sizing, calendar for is_open effective-dated)
   ├──► M6 (capabilities for order validation, execution facts for routing decisions)
   ├──► M7 (spec for pnl, rollover for valuation)
   └──► M8 replay (same historical snapshot_id for reproducibility)
```

**Critical:** `watermark != source_sequence`. M2 distinguishes:
- `source_sequence` — broker-provided seq (`845921` from Deriv)
- `source_event_id` — broker-provided id (`tick-id-123`)
- `processing_sequence / watermark` — M2's own monotonic counter per `instrument_id+broker` (M2.7's gap key). **M2b knows nothing about any of these.** M2b simply maps symbols.

---

## 4. ReferenceService — The Single Stable Facade (new)

**Instead of `M2/M3/M5/M6` importing 8 internal modules, they import one:**

```python
from modules.reference import reference  # singleton ReferenceService

# Identity
reference.exists("FX.EURUSD", broker="deriv") -> bool
reference.to_canonical("frxXAUUSD", broker="deriv") -> "COMMODITY.XAUUSD"
reference.to_native("COMMODITY.XAUUSD", broker="deriv") -> "frxXAUUSD"
reference.get_instrument("FX.EURUSD") -> {asset_class: "forex", display_name: "EURUSD"}

# Specs — effective-dated (MUST support historical)
reference.get_spec("FX.EURUSD", broker="deriv")  # current
reference.get_spec("FX.EURUSD", broker="deriv", effective_at=datetime(2026,3,15, tzinfo=UTC)) -> v12
reference.get_current_spec(...)  # convenience alias

# Calendar — effective-dated
reference.is_open("FX.EURUSD", timestamp, broker="deriv") -> bool  # evaluates calendar effective at timestamp
reference.get_calendar("FX.EURUSD", broker="deriv", effective_at=ts)

# Capabilities — facts only
reference.get_capabilities("COMMODITY.XAUUSD", broker="deriv") -> {order_types: [market,limit], has_depth: False}
reference.get_execution_profile("FX.EURUSD", broker="deriv", effective_at=ts) -> {commission, financing, slippage_params} # descriptive

# Temporal events
reference.get_corporate_actions("BTCUSD", effective_at=ts) -> [Action(split 4:1 @ 2024-08-31)]
reference.get_rollover("COMMODITY.XAGUSD", effective_at=ts) -> Rollover(...)
```

**Internally (not exposed):**

```
ReferenceService
  ├── InstrumentMaster (FX.EURUSD → {asset_class, brokers, active, display_name})
  ├── SymbolMapping (broker_native → canonical_instrument_id)
  ├── ContractSpecs (versioned table: (instrument_id, broker, effective_from) → spec)
  ├── TradingCalendar (effective-dated sessions/holidays/DST)
  ├── CorporateActions (temporal events)
  ├── ContractRollover (event + relationship between contracts)
  ├── BrokerCapabilities (facts)
  └── ExecutionProfiles (descriptive facts only — M6 decides routing)
```

**Why facade?** M2 doesn't need to know if M2b stores YAML or Postgres. Stable dependency boundary + mockable in tests: `mock_reference.get_spec.return_value = {tick_size: 0.00001}`.

**Sync vs Async rule (corrected):**
- **Runtime lookups are synchronous, deterministic, read-only** (`reference.get_spec(...)` — no `await`, `<1µs` dict/cache lookup). Called in hot path (M2.2 per-tick, M5 per-risk `<10ms`).
- **Loading & cache refresh are async I/O** (`reference.load_snapshot()`, `reference.refresh()`) — reads YAML/DB/S3 at startup or nightly. Only these are `async def`.

```python
# Correct — hot path sync
def normalize(raw: RawDerivTick) -> MarketData:
    instrument_id = reference.to_canonical(raw.symbol, broker="deriv")  # sync, no await
    spec = reference.get_spec(instrument_id, broker="deriv")           # sync
    # ...

# Correct — startup async
async def load_reference() -> None:  # async I/O at startup
    snapshot = await reference_loader.load_latest()  # reads YAML/DB
    reference.publish(snapshot)  # immutable, validated
```

---

## 5. Files (updated with effective dating, validation, facade)

### `instrument_master.py` — The List (now canonical)
**Does:** `master = {"FX.EURUSD": {asset_class:"forex", canonical_symbol:"EURUSD", display_name:"EURUSD", brokers:[deriv,mt5], active:True}, "COMMODITY.XAUUSD": {...}}` — keyed by `instrument_id`.
**Pseudocode (sync):**
```python
# sync, deterministic — no await
def get(instrument_id: str) -> dict:  # "FX.EURUSD"
    return master[instrument_id]
def exists(instrument_id: str, broker: str | None = None) -> bool:
    m = master.get(instrument_id)
    return m and (broker is None or broker in m["brokers"])
```

### `symbol_mapping.py` — The Translator (now to instrument_id)
**Does:** `to_canonical("frxEURUSD","mt5") → "FX.EURUSD"`, `to_canonical("frxXAUUSD","deriv") → "COMMODITY.XAUUSD"`.
**Pseudocode (sync):**
```python
maps = {"deriv": {"frxXAUUSD": "COMMODITY.XAUUSD", "frxEURUSD": "FX.EURUSD"}, "mt5": {"EURUSD": "FX.EURUSD"}}
def to_canonical(native: str, broker: str) -> str:  # sync
    return maps[broker].get(native, native)
def to_native(canonical_id: str, broker: str) -> str:  # reverse
    return reverse_maps[(canonical_id, broker)]
```

### `contract_specs.py` — The Specs (now versioned, effective-dated)
**Does:** `get_spec("FX.EURUSD","deriv", effective_at=2026-03-15) → v12 {contract_size:100000, tick_size:0.00001, ...}`. Change on 2026-06-01 creates new snapshot.
**Pseudocode (sync lookup + versioned table):**
```python
# Table: (instrument_id, broker, effective_from) → spec
specs = {
    ("FX.EURUSD","deriv","2026-01-01"): {tick_size:0.00001, contract_size:100000, min_qty:0.001, qty_step:0.001},
    ("FX.EURUSD","deriv","2026-06-01"): {tick_size:0.00001, contract_size:100000, min_qty:0.002, qty_step:0.001}, # changed!
}
def get_spec(instrument_id: str, broker: str, effective_at: datetime | None = None) -> dict:  # sync
    ts = effective_at or now_utc()
    # Find latest effective_from <= ts
    candidates = [s for (i,b,eff), s in specs.items() if i==instrument_id and b==broker and parse(eff) <= ts]
    if not candidates: raise UnknownInstrument
    return max(candidates, key=lambda s: s["effective_from"])
def get_current_spec(instrument_id: str, broker: str) -> dict:  # convenience
    return get_spec(instrument_id, broker, effective_at=None)
```

### `trading_calendar.py` — The Clock for Markets (now effective-dated)
**Does:** `is_open("FX.EURUSD", datetime(2026-09-21 10:00 UTC), broker="deriv")` evaluates **calendar version effective at that timestamp** (handles DST/holiday/session changes historically).
**Pseudocode (sync, effective-dated):**
```python
def is_open(instrument_id: str, ts: datetime, broker: str) -> bool:  # sync
    cal = get_calendar(instrument_id, broker, effective_at=ts)  # uses effective dating
    # cal.sessions for that date — DST-aware
    return ts.weekday() in cal.days and cal.open <= ts.time() <= cal.close
```

### `corporate_actions.py`, `contract_rollover.py` — Temporal Reference Events (new classification)
**Why classification?** Ordinary specs are `mostly static/versioned`. These are **events effective at a specific time** — not continuous state.
- **Instrument spec** → static/versioned (`EURUSD` contract_size)
- **Corporate action** → event (`BTCUSD split 4:1 @ 2024-08-31`)
- **Rollover** → event + relationship (`COMMODITY.XAGUSD expiry 2026-09-20 → next contract`)
**Pseudocode:**
```python
def get_corporate_actions(instrument_id: str, effective_at: datetime) -> list[Action]:  # sync
    return [a for a in actions if a.instrument_id==instrument_id and a.effective_at <= effective_at]
```

### `broker_capabilities.py` + `execution_profiles.py` — Facts vs Decisions
**Does:** `get_capabilities("COMMODITY.XAUUSD","deriv") → {order_types:[market,limit], has_depth:False}` (fact). `get_execution_profile("FX.EURUSD","deriv") → {commission:0.0001, financing:..., slippage_params:{k:0.2}}` (descriptive params).
**Why keep execution_profiles in M2b?** It's **reference facts** (`commission=X`). **Routing decision stays in M6**: `M6.smart_router` reads facts from M2b, then decides `route to mt5 because cheaper`. `grep M2b -r "route"` must be 0.
**Pseudocode (M6 side):**
```python
# M2b — fact
profile = reference.get_execution_profile("FX.EURUSD", "deriv")  # sync
# M6 — decision (not in M2b)
def route(order):  # sync pure
    costs = {b: reference.get_execution_profile(order.instrument_id, b)["commission"] for b in venues}
    return min(costs, key=costs.get)  # M6 decides
```

### `reference_service.py` — The Facade (new, not in old spec)
**Does:** Single entry point delegating to 8 internal modules. Holds `current_snapshot_id`.

### `reference_validator.py` + `reference_loader.py` — Validation Gate (new)
**Does:** At startup / before `publish(snapshot)`:
- Duplicate `canonical_instrument_id` → FAIL STARTUP
- Broker mapping → unknown instrument → FAIL
- Missing `tick_size/contract_size/pip` → FAIL
- Invalid session (`close before open`) → FAIL
- Unknown `asset_class` → FAIL
- Duplicate `effective_from` for same `(instrument,broker)` → FAIL

```python
def validate(snapshot: dict) -> None:  # sync, called before publish
    if len(canonical_ids) != len(set(canonical_ids)): raise Invalid("duplicate canonical")
    for native, canonical in mappings.items():
        if canonical not in canonical_ids: raise Invalid(f"mapping {native}->{canonical} unknown")
    # ... all rules → loud fail with field name
```

---

## 6. MarketData Lineage — reference_snapshot_id (major addition)

When **M2 creates canonical data, it stamps which reference snapshot was used:**

```python
MarketData(
    instrument_id="FX.EURUSD",          # canonical
    symbol="EURUSD",                    # display
    broker="deriv",
    price=1.08421,
    exchange_timestamp=...,
    reference_snapshot_id="REF-2026-09-22-014",  # NEW — which spec/mapping version was active
    source_sequence=845921,             # broker seq
    source_event_id="tick-id-123",      # broker id
    watermark=1001,                     # M2 processing seq
    quality="pristine",
)
```

**Why?** Six months later: "Why did system normalize `frxXAUUSD` to `COMMODITY.XAUUSD` with `tick_size 0.001` on 2026-03-15?" → replay with `REF-2026-03-10-008` (effective at that time). Essential for **auditing, replay, debugging, research, compliance, historical reproducibility**. M8 replays with **same snapshot** as ingestion time.

**Storage:** `reference_snapshot_id` stored in `MarketData` parquet + `data_catalog` row. Not nullable.

---

## 7. How M2b Connects (corrected)

```
config/assets/*.yaml + config/brokers/*.yaml
        │
        ▼ validated (reference_validator) → published immutable snapshot REF-...
        │
M2b ReferenceService (shared cache (instrument_id,broker), sync lookup, versioned table)
        │ read-only truth, never imports M2 (no circular)
        │  instrument_id, mapping, specs(effective_at), calendar(effective_at), capabilities, execution facts
        │
        ├──► M2.2 (to_canonical) + M2.3 (spec validation) + M2.5 (has_depth before expecting book)
        ├──► M3 (pip, tick_size — effective_at = candle timestamp)
        ├──► M5 (min/step for sizing + is_open(effective_at) + sizing clamp — effective-dated)
        ├──► M6 (capabilities + execution facts — M6 decides routing)
        ├──► M7 (spec for pnl, rollover)
        └──► M8 (same spec version as original ingestion — reproducible)

Dependency: M2 ──depends on──► M2b (M2 imports ReferenceService)
            M2b ──never depends on──► M2 (no circular, enforced by import-linter)
```

**Per-tenant note:** M2b cache is **shared** (`(instrument_id, broker_id)` key) — not `(user_id, ...)`. Tenants share truth (1000 users reading `FX.EURUSD` → one cache entry). **Tenant authorization** (can user `42` access `FX.EURUSD` on `deriv`?) is separate check outside M2b.

---

## 8. Build Independence

```bash
pytest modules/reference --fixtures=forex.yaml
# pure dict lookups, sync, no DB/broker — `to_canonical("frxXAUUSD","deriv")==COMMODITY.XAUUSD`
# effective-dated: get_spec("FX.EURUSD","deriv", effective_at=2026-03-15) == v12
# validation: bad YAML (duplicate) → FAIL STARTUP
```

---

## Design Phases — Module-Following (Inline, from Master §37)

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P05** | M2b.1 Instrument & Symbol | `instrument_master` (FX.EURUSD), `symbol_mapping` (frxXAUUSD→COMMODITY.XAUUSD), `ReferenceService` facade | `to_canonical("frxXAUUSD","deriv")==COMMODITY.XAUUSD` |
| **P06** | M2b.2 Specs & Capabilities | `contract_specs(effective-dated)`, `broker_capabilities`, `execution_profiles(facts only)`, `reference_validator` | `get_spec(FX.EURUSD,deriv, effective_at=2026-03-15)==v12` |
| **P07** | M2b.3 Calendar | `trading_calendar(effective-dated)`, `corporate_actions`, `contract_rollover` (Temporal Events) + `is_open(effective_at)` | `is_open(FX.EURUSD, 2026-03-15 10:00 UTC, deriv) effective` |

**Parallel after M1:** M2b can be built before M2.1. All lookups **sync**.

---

## 9. Open Questions for M2b — Resolved by v1.4

- [x] `fx.options` — handled via `config/assets/options.yaml` + same `instrument_master` (no redesign)
- [x] `execution_profiles` — keep **facts** in M2b, **decisions** in M6
- [x] `Lake user_id` — **M2b is shared**; M2 market-data Lake is **global shared** (`provider/instrument` no user_id), only private per-tenant is `signals/trade_plans/orders/ledger` (`WHERE user_id+broker_id`)
- [ ] Should `reference_snapshot_id` also be stored on `AnalyticsResult`/`TradePlan` for full lineage? (Proposal: yes, propagate)

## 8. Analytics Engine — `backend/engines/analytics/` — EXPANDED (M3 Analytics, v1.3 — BEST APPROACH M3.0 Capability + Institutional Async)

# M3 — ANALYTICS (Feature Computation) — EXPANDED DISCUSSION
### Answers “What is mathematically happening?” — never decides to trade. Pure, vectorized, per-tenant inputs but shared code.

**Module Type:** Stateless pure-computation — deterministic, idempotent, cacheable. `compute(candles)→features` — same code serves Deriv and MT5 tenants.
**Owns State:** `feature_cache` (Redis LRU, ephemeral per tenant `user_id+broker_id:symbol:timeframe` key) — no durable business state.
**Input:** `MarketData` (per-tenant per-broker, quality-tagged) + `PortfolioState` (per-tenant, read-only)
**Output:** `AnalyticsResult(symbol, timeframe, features={rsi:68, vol:0.018}, regime)`.

> **Expanded for M3** — each file simple + pseudocode + connection. This patches into master §8.

---

### 1. Purpose in One Sentence
> **M3 turns `MarketData` into numbers (`RSI`, `ATR`, `correlation`) so `M4` can decide `long/short`.** No `if should_buy` here — just math.


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution is mandatory <500ms order→ack, <50ms tick→parquet, <20ms analytics, <10ms risk. No calculation error, file handling error, or abnormality is tolerated — every I/O is async `async def` + `await` + `try/except + retry with jitter + dead-letter`, every pure calc is sync `def` + frozen `BaseContract` + golden test, every state change is event-driven via `event_bus` not poll. Code must be well-commented WHY, not just WHAT.*

### 2. The 7 Sub-Modules — Best Approach (Capability-First)

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M3.0** | Capability Registry (NEW — Best Approach) | Decides `FULLY_SUPPORTED / PROXY_LIMITED / UNAVAILABLE` per broker+tenant, gates M4 families | Add provider/feature, change Deriv limits |
| **M3.1** | Feature Engine & Profiles | Orchestrates + caches + picks params per asset class | Add asset class |
| **M3.2** | Per-Asset | 17 pure functions: RSI, EMA, ATR, etc. — each file parallelizable | Add indicator |
| **M3.3** | Cross-Asset | Correlation, cointegration — needs multiple symbols | Add pair logic |
| **M3.4** | Microstructure | Order book imbalance, VPIN — needs depth | Add book feature |
| **M3.5** | Portfolio Analytics (read-only) | Exposure, VaR, margin compute — reads `PortfolioState` | Add portfolio metric |
| **M3.6** | Macro & Event | Rates, event impact — daily | Add macro regime |

### 3. M3.0 — CAPABILITY REGISTRY — The Gatekeeper (BEST APPROACH — NOT a separate engine)

> **Why M3.0, not `backend/engines/data_capability/`?** Research proposes cross-cutting service called by Data/Analytics/Strategy. That couples `M2 → capability → M3 → capability` and needs DB/RPC. Our best approach keeps it **pure lookup inside M3**, per-tenant, no DB, `<1ms`.

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
**Connects:** `M3.1` checks `registry.is_available(feature)` before `get_features()`; `M4.1 lifecycle` on `registry.load()` disables families → `strategy_health=DISABLED` + UI explains why. **Per-tenant gate:** `WHERE broker_id=:bid` — Deriv user never sees book features.

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

### 4. M3.1 — FEATURE ENGINE & PROFILES — The Conductor

### `feature_engine.py`
**Does:** Given `symbol=EURUSD, timeframe=1m, user_id=42, broker=deriv`, it figures dependency graph `returns → momentum → RSI` and cache: `cache.get(user_42:deriv:EURUSD:1m:RSI_14) or compute`.
**Pseudocode:**
```python
async def get_features(symbol, timeframe, user_id, broker):  # async: Redis + Lake I/O, pure compute sync, cache per-tenant, well-commented WHY cache key includes user_id+broker
    key = f"{user_id}:{broker}:{symbol}:{timeframe}"
    if key in redis: return redis.get(key)  # <1ms
    candles = lake.get(symbol, broker, user_id, timeframe)  # from M2.8
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

### 5. M3.2 — PER-ASSET — The 17 Pure Functions (Embarrassingly Parallel)

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

### 6. M3.3 — CROSS-ASSET — Multi-Symbol

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

### 7. M3.4 — MICROSTRUCTURE — Needs Depth (Only Where `has_depth=True`)

### Files

#### `microstructure/order_book_imbalance.py`
**Does:** `(bid_qty - ask_qty)/(bid_qty+ask_qty)` → `0.7` = buy pressure; `-0.7` = sell pressure.
**Pseudocode:** `imb = (sum(bids.qty) - sum(asks.qty)) / (sum(bids.qty)+sum(asks.qty))`
**Connects:** `M4 microstructure/order_book_imbalance` → `imb>0.6 → long`.

#### `toxicity_vpin.py`, `market_impact.py`
**Does:** `VPIN` high → toxic flow (informed traders), `Kyle lambda` → price impact per lot.
**Connects:** `M5 liquidity_check` blocks if `vpin>0.8` (toxic), `M6 smart_router` uses `lambda` to pick venue.

---

### 8. M3.5 — PORTFOLIO ANALYTICS (Read-Only, Doesn’t Own Positions)

### Files

#### `portfolio/margin.py` (compute) vs `M5 margin_check` (decide)
**Does:** `M3` *computes* `margin_used = sum(position.qty * contract_size * price / leverage)`; `M5` *decides* `if available - required < threshold → Reject`. Compute vs decide split.
**Pseudocode (M3):** `margin_used = sum(p.qty * spec.contract_size * last_price / leverage for p in portfolio.positions)`
**Connects:** `M5 pre_trade/margin_check` calls `M3 portfolio/margin.py` (compute) then decides.

**Other files:** `exposure`, `concentration`, `var_calculator`, `scenario_risk` (what if EURUSD +2%?) — all read `PortfolioState` contract, not DB.

---

### 9. M3.6 — MACRO & EVENT — Daily

### Files

#### `macro/rates.py`, `event/event_registry.py`
**Does:** `rates` fetches FED 5.5%, `event_registry` knows `CPI 2026-09-21 14:30` is high impact.
**Pseudocode:** `event = registry.get("CPI", date); impact = classifier(event)  # high/medium/low`
**Connects:** `M4 systematic_macro` reads `macro_regime=risk_off → reduce crypto / commodity risk`.

---

### 10. How M3 Connects to Whole System

```
M2 MarketData → M3.0 Capability (per-tenant gate, <1ms) → M3.0 Capability (sync check) → M3.1 FeatureEngine (cache) → M3.2-3.6 pure compute (shared code) → AnalyticsResult(features, regime, capability_status, warning) → M4.1 gates families (DISABLED if UNAVAILABLE) + M4 generates → M5 Risk (reads vol/spread + capability)
```

**High-speed:** `M3.2` vectorized `numpy`, `M3.1` `Redis` cache per `user_id:broker:symbol:timeframe`, p95 <20ms.



---

#### Design Phases — Module-Following (Inline, from Master §37)

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

### 11. Open Questions for M3

- [ ] Phase 1 cut: 5 features (returns, MA, RSI, vol, spread) vs full 17 — lock 5 as v0.3?
- [ ] Should `portfolio/margin` compute stay in `M3` or move to `M5`? Keep compute vs decide split?
- [ ] `M3.2` 17 files parallel — assign 3 devs × 5 each?
---

## 9. Strategy + Signal — `backend/engines/alpha/` — The Alpha Factory — EXPANDED (M4 Alpha, v1.3 — BEST APPROACH M4.5 Trade Plan, 5 sub-modules + Institutional)

# M4 — ALPHA (Strategy + Signal Merged) — EXPANDED DISCUSSION
### The ONLY place alpha lives. Proposes intent, gates it, emits Validated Signal. Per-tenant intent, shared logic.

**Module Type:** Stateful — Strategy registry + Signal state_machine. Per-tenant signals, shared strategy code.
**Owns State:** `signal_store` (per user_id+broker_id) + `strategy_registry` (which families enabled)
**Input:** `AnalyticsResult` + `MarketData` (per-tenant)
**Output:** `TradePlan(entry/SL/TP/timing, trade_plan_id, per-tenant)` via `M4.5` → M5 (Validated Signal is internal `M4.4→M4.5` only)

> **Expanded for M4** — each file simple + pseudocode + connection. This patches into master §9-§10.

---

### 1. Purpose in One Sentence
> **M4 converts math (`RSI 72`) into intent (`EURUSD LONG strength 0.76, valid 60s`) and proves the intent is clean before risking money.** No sizing, no broker, no order — just *should we?* and *how strongly?*


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution is mandatory <500ms order→ack, <50ms tick→parquet, <20ms analytics, <10ms risk. No calculation error, file handling error, or abnormality is tolerated — every I/O is async `async def` + `await` + `try/except + retry with jitter + dead-letter`, every pure calc is sync `def` + frozen `BaseContract` + golden test, every state change is event-driven via `event_bus` not poll. Code must be well-commented WHY, not just WHAT.*

### 2. The 5 Sub-Modules — BEST APPROACH (M4.5 Trade Plan Inside)

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M4.1** | Strategy Registry | Base class, discovery, versioning, shadow_mode | Add new strategy family |
| **M4.2** | Strategy Families (11, Tier 1 first) | 11 families, each `class Strategy(BaseStrategy)` → `Signal` | Add/change a family |
| **M4.3** | Signal Validation | Checks tradable, dedup, freshness, latency | Change freshness window |
| **M4.4** | Signal Orchestration | Conflict, priority, provenance, state_machine | Change prioritization |
| **M4.5** | **Trade Plan (NEW — BEST APPROACH)** | `entry/stop/target/timing` → `TradePlan` contract, auditable SL/TP | Change entry/SL/TP model |

### 3. M4.1 — STRATEGY REGISTRY — The Framework

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

### 4. M4.2 — STRATEGY FAMILIES — The 11 Brains (Each Parallelizable)

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

### 5. M4.3 — SIGNAL VALIDATION — The Bouncer

### `signal/validator.py`
**Does:** Is `symbol` in `M2b instrument_master` and `active`? Is `qty_step` respected? If not, Drop + audit.
**Pseudocode:**
```python
def validate(signal: Signal):
    if not reference.exists(signal.symbol, signal.broker): return Drop("unknown_symbol")
    spec = reference.get_spec(signal.symbol, signal.broker)
    if signal.symbol in ["Gold"] and spec.has_depth==False and signal.needs_depth: Drop
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

### 6. M4.4 — SIGNAL ORCHESTRATION — The Prioritizer

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

### 7. M4.5 — TRADE PLAN — The How (BEST APPROACH — Inside Alpha, Not Separate Engine)

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

### 8. How M4 Connects to Whole System (Updated — TradePlan)

```python
# New law
M3 AnalyticsResult + M2 MarketData → M4.1 Registry → M4.2 Families → Signal → M4.3/4.4 → Validated Signal → M4.5 TradePlan → TradePlan → M5.3 Risk (checks TradePlan.valid_until, spread, RR, data_quality) → RiskDecision → M6
```

**High-speed:** `M4.5` `<0.5ms` pure, no I/O, per-tenant.

---

### 9. Strategy Families — Tier 1 Priority for Deriv (No Volume, Best Approach)

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

### 10. Open Questions for M4

- [ ] Phase 1 ship only `trend_following::ema_trend` as proof, or also `momentum`?
- [ ] Latency budgets: `100ms` micro vs `60s` macro — adjust?
- [ ] `M4.2` 11 families parallel — assign one dev per family?
- [ ] Shadow period `N` days before promotion — what `N`?
---


---

#### Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P22** | M4.1 Strategy Registry | `base_strategy, registry, shadow_mode` | `registry.load()` |
| **P23** | M4.2 Strategy Families (P1) | `trend_following::ema_trend` first family (Tier1) | `Signal LONG` |
| **P24** | M4.3 Signal Validation | `validator, deduplicator, freshness` | `Drop stale` |
| **P25** | M4.4 Signal Orchestration | `conflict_detector, queue_prioritizer` | `prioritized` |
| **P26** | **M4.5 Trade Plan (NEW)** | `trade_plan/engine, entry/stop/target/timing` → `TradePlan` | `RR>1 && valid_until` |

**After M3 (incl. M3.0):** Alpha starts after `M3` (M3.0 gates `UNAVAILABLE`). `P22` repeats 11× for each family (Tier1 first 8, then backlog). `P25` must be after `P24` (needs Validated Signal).

## 11. Portfolio & Risk — `backend/engines/portfolio_risk/` — State + Gatekeeper — EXPANDED (M5 Portfolio & Risk, v1.3 — BEST APPROACH validates TradePlan + 3 Layers + Institutional)

# M5 — PORTFOLIO & RISK (Atomic) — EXPANDED DISCUSSION
### Owns positions + RiskDecision. The gatekeeper — if M5 says no, M6 never sees an Order. Per-tenant account calc, shared engine.

**Module Type:** Stateful — STRONGEST independent Module. Owns ledger pre-condition, atomic transaction.
**Owns State:** `portfolio_state` + `positions` + `risk_limits` + `risk_state` — ONLY writer, per `user_id+broker_id`, no other Module writes.
**Input:** `TradePlan` (from M4.5, per-tenant) + `PortfolioState` (self, per-tenant) + `AnalyticsResult` (vol/spread per-tenant)
**Output:** `RiskDecision(approved, qty, rejected_reason, trade_plan_id)` → M6 (M6 builds Order from TradePlan)

> **Expanded for M5** — each file simple + pseudocode + connection. This patches into master §11-§12.

---

### 1. Purpose in One Sentence
> **M5 answers: “Is this TradePlan (entry/SL/TP/timing) safe for this tenant? How much?” — before any `Order` is built.** Same `M5` code serves `user 42@deriv` and `user 99@mt5`, but risk is per-tenant `user_id+broker_id`. **BEST APPROACH:** M5 now validates `TradePlan` (not raw Signal) — checks `valid_until, SL/TP existence, RR, data_quality, spread, margin`.


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution is mandatory <500ms order→ack, <50ms tick→parquet, <20ms analytics, <10ms risk. No calculation error, file handling error, or abnormality is tolerated — every I/O is async `async def` + `await` + `try/except + retry with jitter + dead-letter`, every pure calc is sync `def` + frozen `BaseContract` + golden test, every state change is event-driven via `event_bus` not poll. Code must be well-commented WHY, not just WHAT.*

### 2. The 5 Sub-Modules

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M5.1** | Portfolio State | Keeps `positions` (qty, avg entry) per tenant per broker | Change position logic |
| **M5.2** | Allocation | Target vs actual weights, capital per strategy | Change allocation |
| **M5.3** | Pre-Trade Risk | 10 checks in parallel — the gate | Change margin/exposure rule |
| **M5.4** | Sizing & Limits | `strength 0.7 → qty 0.4 lots` via `vol` | Change sizing |
| **M5.5** | Risk Ops | Post-trade, monitoring, kill_switch, asymmetric_lock | Change emergency |

**Atomic:** `M5.1 + M5.3 + M5.4` wrapped in one `UoW` per `TradePlan` → no partial `RiskDecision`. Best approach: TradePlan already validated for expiry/SL/TP before Risk.

---

### 3. M5.1 — PORTFOLIO STATE — The Ledger’s Pre-Condition

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

### 4. M5.2 — ALLOCATION — The Budget

### `allocation.py`, `capital_allocator.py`, `exposure_manager.py`
**Does:** `strategy_allocation: {trend:30%, mean_rev:20%}` + `capital_allocator: {deriv:60%, mt5:40%}` → `M5.3` knows `risk_budget` for sizing. `exposure_manager` tracks `gross exposure per symbol per tenant`.
**Pseudocode:**
```python
risk_budget = capital_allocator.get(user_id, broker) * strategy_allocation["trend"]  # e.g., $1000 * 30% = $300
# M5.4 will do qty = risk_budget / (ATR * contract_size)
```
**Connects:** `M5.4 sizing` reads this; `M10 API` `GET /portfolio` reads this for UI pie chart.

---

### 5. M5.3 — PRE-TRADE RISK — The Gate (10 Checks, Parallel)

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

### 6. M5.4 — SIZING & LIMITS — How Much?

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

### 7. M5.5 — RISK OPS — After & Emergency

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

### 8. How M5 Connects to Whole System

```
M4.5 TradePlan (per-tenant, with entry/SL/TP/timing) → M5.1 PortfolioState (per-tenant) + M5.2 Allocation → M5.4 Sizing (uses TradePlan.stop distance + M3 vol) → qty → M5.3 Pre-Trade (10 checks, validates TradePlan.valid_until/SL/TP/RR/quality/spread/margin) → RiskDecision(approved, qty, trade_plan_id) → M6 (builds Order from TradePlan)
                                                                                         ↓
                                                                                  M5.5 Risk Ops ← M7 asymmetric_lock event
```

**High-speed:** `M5.3` p95 <10ms (all in-mem, no I/O).



---

#### Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P27** | M5.1 Portfolio State | `portfolio_state` (holdings per tenant, tracks `last_trade_plan_id`) | `on_fill` per tenant |
| **P28** | M5.2 Allocation | `allocator` (target weights, risk_budget per tenant) | `risk_budget` |
| **P29** | M5.3 Risk Checks | `risk_manager` 10 checks (validates `TradePlan` — `valid_until, SL/TP, RR, quality, spread, margin`) | `test_risk_rejects_expired_trade_plan` |
| **P30** | M5.4 Sizing | `position_sizer` `TradePlan.stop_distance → qty` | `strength* risk_budget` |
| **P31** | M5.5 Kill Switch ‖ M5.6 Fees | `kill_switch, asymmetric_lock` ‖ `fee_model` | `kill_switch` audit |

**After M4 (M4.5 TradePlan):** Starts after `M4.5 TradePlan`. `M5.3` gates `TradePlan` (not Signal) — test `test_risk_rejects_expired_trade_plan`.

### 9. How Risk Uses 3 Layers (Simple — Same as Everywhere)

> **Plain English:** Risk has `Automatic` (from your balance/equity/leverage) + `Your Override` (where allowed, e.g. max exposure 7% vs default 10%) + `Reset to Default`. Platform hard limit (e.g. max leverage 50) can never be overridden.

Example: `Automatic 10% → You set 7% → Effective 7% → You press Reset → back to 10%`.

### 10. Open Questions for M5

- [ ] Sizing default: `vol_target` vs `risk_budget`?
- [ ] `asymmetric_lock` threshold: `%` divergence or margin level?
- [ ] Exposure limit per symbol: single or per-asset-class?
---

## 13. Execution — `backend/engines/execution/` — Validator, Router & Broker Talker — EXPANDED (M6 Execution, v1.3 BEST APPROACH TradePlan→Order + Institutional Async)

# M6 — EXECUTION (Order + Execution Merged) — EXPANDED DISCUSSION
### Builds broker-ready Order and talks to brokers. The ONLY module that talks to brokers for orders. Per-tenant orders, shared adapters.

**Module Type:** Stateful (order lifecycle) + I/O heavy. Per-tenant orders, shared broker adapters.
**Owns State:** `orders` + `order_state` (`pending→filled/unknown`) per `user_id+broker_id`, `child_orders`
**Input:** `RiskDecision(approved, qty, trade_plan_id)` + `TradePlan(entry/SL/TP/timing, per-tenant)` (from M5/M4.5)
**Output:** `ExecutionResult(fills, latency, slippage, trade_plan_id)` → M7 (links `TradePlan → Order → Fill`)

> **Expanded for M6** — each file simple + pseudocode + connection. This patches into master §13-§14.

---

### 1. Purpose in One Sentence
> **M6 turns `TradePlan(buy 0.4 EURUSD entry 1.085 SL 1.0835 TP 1.088 valid 60s)` + `RiskDecision(approved, qty)` into `Order(client_order_id=uuid5(trade_plan_id), qty 0.4, bracket SL/TP)` and gets it filled — retry-safe, idempotent, per-tenant.**


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution is mandatory <500ms order→ack, <50ms tick→parquet, <20ms analytics, <10ms risk. No calculation error, file handling error, or abnormality is tolerated — every I/O is async `async def` + `await` + `try/except + retry with jitter + dead-letter`, every pure calc is sync `def` + frozen `BaseContract` + golden test, every state change is event-driven via `event_bus` not poll. Code must be well-commented WHY, not just WHAT.*

### 2. The 5 Sub-Modules

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M6.1** | Order Core | Builder, validator, state_machine, idempotency | Change order lifecycle |
| **M6.2** | Routing | Pin or choose venue (`smart_router`) | Add routing rule |
| **M6.3** | Connectivity | WS/REST/FIX transports | Add FIX |
| **M6.4** | Broker Adapters | Deriv/MT5/Headway translators (per-broker parallel) | Add broker |
| **M6.5** | Execution Intelligence | Algos (market/limit/twap), costs, metrics | Add TWAP |

### 3. M6.1 — ORDER CORE — The Lifecycle Owner

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
**Does:** `validator` checks `qty % step ==0`, `qty >= min_qty` via `M2b`; `capability_check` checks `order_type in broker_capabilities[broker][symbol]` (e.g., `frxEURUSD` Deriv has no `stop`).
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

### 4. M6.2 — ROUTING — Where to Send?

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

### 5. M6.3 — CONNECTIVITY — The Transports (No Trading Logic)

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

### 6. M6.4 — BROKER ADAPTERS — The Thin Translators (Per-Broker Parallel, ISOLATED)

### `broker_adapters/deriv/{client,execution,models}`, `mt5/...`, `headway/...`
**Does:** Translates `Order(EURUSD, buy, 0.4, market)` → `Deriv native {"symbol":"frxXAUUSD","buy":0.4}` and `Deriv fill {"price":1.08,"qty":0.4}` → `Fill(price=1.08, qty=0.4)`. **No sizing, no risk.** One folder per broker, no cross imports.
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

### 7. M6.5 — EXECUTION INTELLIGENCE — How to Work the Order

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

### 8. How M6 Connects to Whole System

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

#### Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P32** | M6.1 Order Builder | `order_builder, state_machine, idempotency uuid5(trade_plan_id)` — builds `Order(entry, SL, TP)` from `TradePlan` | `Order` has `trade_plan_id` |
| **P33** | M6.2 Router | `router, smart_router` | `venue` chosen |
| **P34** | M6.3 Connectivity | `ws_client, rest_client, fix_client` per-tenant token | `reconnect` |
| **P35** | M6.4 Broker Adapters | `deriv_adapter` first, then `mt5, headway` — translates `TradePlan` SL/TP to native | `Deriv` fill |
| **P36** | M6.5 Algos | `twap, market_impact, slippage_model` | `TWAP` |

**After M5 (P31):** Execution starts after `M5` approves `TradePlan`. `M6.1` now builds bracket `Order` (`entry+SL+TP`) from `TradePlan` (not Signal). `M6.4` per-broker diff preserved.

### 9. Open Questions for M6

- [ ] Routing Phase 1: `pin` (`Signal.broker` pass-through) vs `smart_router` (spread-based)?
- [ ] Algos: `market+limit` only or also `twap` Day 1?
- [ ] FIX needed now or WS/REST sufficient?
---

## 15. Trust — `backend/engines/trust/` — Reconciliation + Ledger — EXPANDED (M7 Trust, v1.3 BEST APPROACH TradePlan lineage + Institutional)

# M7 — TRUST (Reconciliation + Accounting (Lives on TradePlan→Order→Fill lineage) Merged) — EXPANDED DISCUSSION
### Establishes broker truth and what it means financially. Per-tenant ledger, broker-diff truth.

**Module Type:** Stateful — truth boundary, owns append-only ledger per `user_id+broker_id`.
**Owns State:** `reconciliation_reports` + `ledger` (append-only per tenant per broker) + `pnl`/`fees`
**Input:** `ExecutionResult` (M6 claim per-tenant) + broker truth (external snapshots per tenant)
**Output:** `ReconciliationReport(status: matched/mismatched)` → `LedgerEntry`/`PnL` (per-tenant)

> **Expanded for M7** — each file simple + pseudocode + connection. This patches into master §15-§16.

---

### 1. Purpose in One Sentence
> **M7 proves `internal positions == broker positions` per tenant per broker. If not, lock. Then records money only after proof.**


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution <500ms, tick→parquet <50ms, analytics <20ms, risk <10ms. No calc/file/abnormality tolerated — async I/O + sync pure calc + event_bus + frozen contracts + golden tests + well-commented WHY.*

### 2. The 4 Sub-Modules

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M7.1** | Reconciliation — Brokers | Compare `account/order/position/fill` per broker per tenant | Add reconciler |
| **M7.2** | State Assurance | Diff, unknown handler, asymmetric_lock | Change lock policy |
| **M7.3** | Ledger | Append-only double-entry per tenant | Change ledger |
| **M7.4** | Financials | PnL (FIFO/LIFO), fees, valuation per tenant | Change PnL |

### 3. M7.1 — RECONCILIATION — BROKERS — The Comparers

### `reconciliation/broker/{account,order,position,balance,fill}_reconciler.py`
**Does:** `account_reconciler`: `internal cash 1000 vs broker cash 998 → diff -2`; `position_reconciler`: `internal EURUSD 0.7 vs broker 0.5 → mismatch 0.2`; each per `user_id+broker_id`.
**Pseudocode:**
```python
async def reconcile_positions(user_id, broker):
    internal = db.get_positions(user_id=user_id, broker=broker)  # WHERE user_id+broker_id
    external = broker_api.get_positions(token=vault.get(user_id, broker))  # per-tenant token
    diffs = comparator.compare(internal, external)  # [{symbol:EURUSD, internal:0.7, external:0.5, type:mismatch}]
    return ReconciliationReport(user_id=user_id, broker=broker, diffs=diffs, status="mismatched" if diffs else "matched")
```
**Connects:** `M6` ExecutionResult triggers immediate `reconcile_orders`, plus periodic `every 30s-5m` per broker per tenant via `M1 scheduling`.

---

### 4. M7.2 — STATE ASSURANCE — The Fixer & Locker

### `state/state_comparator.py`, `state_recovery.py`, `unknown_state_handler.py`
**Does:** `comparator` (sync pure, deterministic, well-commented WHY mismatch threshold) deep diffs; `recovery` can *authorized* correct internal *only after* audit + alert (never silent); `unknown_state_handler` handles `Order.state=unknown` → `query broker GET /order?client_order_id=abc` → 3 outcomes: `rejected (duplicate) → mark rejected`, `filled → recover fill → M5`, `404 → keep unknown, mark degraded`.
**Pseudocode:**
```python
async def handle_unknown(order: Order, user_id, broker):
    ext = broker.query(client_order_id=order.client_order_id, token=vault.get(user_id,broker))
    if ext.status=="rejected" and ext.reason=="duplicate": order.state="rejected"; audit.log("duplicate")
    elif ext.status=="filled": portfolio_risk.on_fill(ext.fill, user_id, broker); order.state="filled"
    elif ext is None: order.state="unknown"; mark_degraded(user_id,broker)
```

### `policies/asymmetric_lock.py`, `hedge_completion.py`
**Does:** `asymmetric_lock`: if `mismatch > threshold` or `unknown` for `EURUSD/deriv/user42`, emit event → `M5` blocks *new* `EURUSD/deriv` for `user42` but allows *exits* (close 0.7 → 0). `hedge_completion` ensures hedges fully executed.
**Pseudocode:**
```python
if report.status=="mismatched" and diff>threshold:
    event_bus.emit("asymmetric_lock", user_id=user_id, broker=broker, symbol="EURUSD", allow_exits=True, block_entries=True)
    # M5 listens and enforces
```
**Connects:** `M7.2 → M5.5` (lock), `M10` (UI shows `mismatched` badge).

---

### 5. M7.3 — LEDGER — The Append-Only Truth

### `accounting/ledger.py`, `cash.py`, `settlement.py`
**Does:** `ledger` is double-entry, **append-only, per-tenant per-broker**: `debit cash 1000, credit positions 0.7 EURUSD` on fill; correcting entry has `correction_of: previous_ledger_id` — never `UPDATE`. `cash` tracks `USD, EUR` balances per tenant per broker.
**Pseudocode:**
```python
async def append_fill(fill: Fill, order: Order, user_id, broker):
    entry = LedgerEntry(tenant=(user_id,broker), order_id=order.order_id, 
                        debit={"cash": -fill.price*fill.qty}, credit={"positions:EURUSD": fill.qty},
                        correction_of=None)
    db.insert(entry)  # WHERE user_id+broker_id, append-only
# Correction:
#   entry2 = LedgerEntry(correction_of=entry1.id, debit=..., credit=...)  # never update entry1
```
**Connects:** Triggered *only after* `M7.2` says `matched` or `unknown` resolved. `M5` never writes ledger.

---

### 6. M7.4 — FINANCIALS — The Money Meaning

### `accounting/pnl.py`, `fees.py`, `financing.py`, `valuation.py`, `currency_conversion.py`
**Does:** `pnl` `realized = (fill_price - avg_entry)*qty` via `FIFO` per `M2b` spec, `unrealized = (mark_price - avg_entry)*qty`; `fees` sums commission per broker per tenant; `financing` overnight swap; `valuation` mark-to-market; `currency_conversion` converts `EUR PnL → USD` via `M3` FX rate.
**Pseudocode:**
```python
async def pnl(position: Position, market_price):
    realized = sum((fill.price - position.avg_entry)*fill.qty for fill in fills)  # FIFO
    unrealized = (market_price - position.avg_entry) * position.qty
    return {"realized": realized, "unrealized": unrealized}
# Attribution via provenance:
#   pnl_by_strategy = group_by(ledger, key=lambda e: e.provenance.strategy_id)  # trend vs mean_rev
```
**Connects:** Reads `Ledger` per-tenant per-broker; `M10 API` `GET /accounting/pnl` returns this.

---

### 7. How M7 Connects to Whole System

```
M6 ExecutionResult(fills, per-tenant) → M7.1 Reconcilers (query broker per-tenant token) → M7.2 Comparator (diff) → if matched → M7.3 Ledger (append per-tenant) → M7.4 PnL (per-tenant, per-strategy)
                                                    ↓ mismatched/unknown
                                              M7.2 asymmetric_lock → M5 Risk (block new entries per-tenant, allow exits) + M10 UI + alert
```

**Build Independence:**
```bash
pytest modules/trust --internal=PortfolioState --external=BrokerSnapshot --mock-vault
# per-tenant: internal user 42/deriv vs external 42/deriv → expect matched; 42/deriv vs 99/deriv → different tenants, no leakage
```



---

#### Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P37** | M7.1 Reconcilers | `reconciler_deriv, reconciler_mt5` per broker — checks `Order(trade_plan_id)` vs broker | `reconcile` |
| **P38** | M7.2 Comparator | `comparator, unknown_state, asymmetric_lock` per `user_id+broker_id` | `mismatch` → `M5` lock |
| **P39** | M7.3 Ledger | `ledger` (async append-only, per-tenant WHERE user_id+broker_id, checksum, retry, well-commented WHY immutable) — stores `trade_plan_id, order_id, fill_id` | `SELECT WHERE user_id+broker_id` |
| **P40** | M7.4 PnL | `pnl_calculator` FIFO, fees per tenant — `TradePlan` trail | `PnL(trade_plan_id)` |

**After M6 (P36):** Trust starts after `M6`. `M7.3` links `TradePlan → Order → Fill` for audit — best approach lineage `Execution → TradePlan → Signal → Features → Raw`.

### 8. Open Questions for M7

- [ ] Ledger FIFO vs LIFO per asset class — per broker spec?
- [ ] Auto-correct threshold: ever auto-correct ≤1% drift, or always manual?
- [ ] Reconciliation interval: 30s Deriv, 5m MT5 — per broker per tenant?
---

## 17. Validation — `backend/engines/validation/` — Historical Truth → Paper → Canary — EXPANDED (M8 Validation, v1.3 BEST APPROACH replays TradePlan + Institutional)

# M8 — VALIDATION (Backtesting + Simulation + Paper + Canary — 4 Modes, 1 Team) — EXPANDED DISCUSSION
### Answers “Is it safe?” in four ways. All share Lake + fill models; none touches live brokers directly. Per-tenant validation.

**Module Type:** Stateful (runs/reports) — 4 modes, one team owns validation.
**Owns State:** `backtest_runs`, `simulation_reports`, `paper_state` (fake positions per tenant), `canary_state` per `user_id+broker_id`
**Input:** Historical `MarketData` (Lake per broker per tenant) OR live `MarketData` (paper, per-tenant) + `M3-M6` code as *libraries* (not running services)
**Output:** `ValidationReport(Sharpe, maxDD, leakage pass/fail)` → promotion `shadow→canary→prod` per tenant

> **Expanded for M8** — each file simple + pseudocode + connection. This patches into master §17-§20.

---

### 1. Purpose in One Sentence
> **M8 proves a strategy works *before* real money. 4 lenses: history (backtest), stress (simulation), live fake money (paper), live tiny money (canary) — all reuse real `M3-M6` code, only venue changes.**


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution <500ms, tick→parquet <50ms, analytics <20ms, risk <10ms. No calc/file/abnormality tolerated — async I/O + sync pure calc + event_bus + frozen contracts + golden tests + well-commented WHY.*

### 2. The 4 Sub-Modules

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M8.1** | Backtesting | Replay Lake ticks → `ValidationReport` | Change fill model |
| **M8.2** | Simulation | Shock spread/vol/broker failure | Add shock |
| **M8.3** | Paper Trading | Live ticks → paper broker (per-tenant) | Change matching |
| **M8.4** | Canary | 1% live per-tenant, rollback | Change threshold |

### 3. M8.1 — BACKTESTING — History, No Leakage

### `backtesting/data/{data_engine, tick_replay, order_book_replay, data_suitability}`
**Does:** `data_engine` loads `data/lake/brokers/deriv/user_id=42/date=.../EURUSD.parquet` *time-ordered*; `tick_replay` emits ticks with realistic `exchange_timestamp`; `data_suitability` blocks `gapped` unless overridden.
**Pseudocode:**
```python
def replay(broker, user_id, symbol, start, end):
    parquets = lake.list(broker=broker, user_id=user_id, symbol=symbol, start=start, end=end)  # per-tenant
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
Lake (per-tenant) → M8 tick_replay → M3 Analytics (real code) → M4 Alpha (real code) → M5 Risk (real code) → M6 Order (real code) → M8 virtual_broker (instead of real M6 broker_adapters) → M5 Portfolio (simulated) → M7 Ledger (simulated) → Report
```
**No M6 real broker.** If backtest and paper diverge → `fill_model` bug, not strategy.

---

### 4. M8.2 — SIMULATION — Stress (“What Breaks?”)

### `simulation/{monte_carlo, spread_shock, broker_failure, regime_variation, correlation_breakdown}`
**Does:** `spread_shock`: `spread 10× → ValidationReport`; `broker_failure`: broker offline mid-order → `unknown_state` handling; `regime_variation`: test trending strategy in `choppy` regime.
**Pseudocode:**
```python
def spread_shock(backtest_run, factor=10):
    for tick in backtest_run.ticks: tick.spread *= factor  # 1 pip → 10 pips
    return run_backtest(ticks_with_shock)  # Sharpe drops?
```

---

### 5. M8.3 — PAPER TRADING — Live Data, Fake Venue (Per-Tenant)

### `paper_trading/{paper_broker, matching_engine, account_state, execution_simulator}`
**Does:** Live `M2 ticks (per-tenant)` → same `M3-M6` real code → `paper_broker.matching_engine` (mid±spread, `latency_simulator 50ms`, `rejection_simulator` random) → `paper_state` (fake positions per `user_id+broker_id`). `reconciliation` for paper *should always match* (tests pipeline).
**Pseudocode:**
```python
# Same as live, only venue changes:
# live:   Signal → M5 Risk → M6 Order → M6 broker_adapters/deriv → Deriv
# paper:  Signal → M5 Risk → M6 Order → M8 paper_broker → paper fill
```

---

### 6. M8.4 — CANARY — 1% Live Per-Tenant, Rollback

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

### 7. Build Independence

```bash
pytest modules/validation --lake=parquet --mock-no-live-broker
# no live WS, no M2 live, no M6 real adapter — Lake replay only, per-tenant mocked
```



---

#### Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P41** | M8.1 Backtesting | `tick_replay, virtual_broker, leakage_detector` — replays `MarketData → M3.0/M4.5 TradePlan → M5 → M6` same path, no leakage | `no leakage` |
| **P42** | M8.2 Simulation | `spread_shock, gap_shock, latency_injector` | `shock` |
| **P43** | M8.3 Paper | `paper_broker` (async, simulates fill with spread/slippage, well-commented WHY same M4.5→M5 path), fake money) — simulates `TradePlan` entry/SL/TP | `paper` |
| **P44** | M8.4 Canary | `canary_router` 1% live — `TradePlan` promoted shadow→canary | `canary` |

**After M2+M3+M7 (incl. M3.0/M4.5):** Validation replays `TradePlan` path (not Signal-only) — ensures `M4.5` logic is backtested. All 4 modes share `Lake` + `virtual_broker`.

### 8. Open Questions for M8

- [ ] Backtest tick vs 1m candle replay — default tick?
- [ ] Simulation Day 1 shocks: `spread 10x`, `vol 2x`, `broker_failure` top 3?
- [ ] Canary `1%/3 symbols/2 weeks` — finalize per tenant?
---

## 21. Research — `backend/engines/research/` — No Contamination — EXPANDED (M9 Research, v1.3 BEST APPROACH TradePlan lineage + Institutional)

# M9 — RESEARCH (Isolated Lab) — EXPANDED DISCUSSION
### Experiment without polluting production. Tenant-scoped datasets, shared lab engine.

**Module Type:** Batch + stateful. Never imports Execution.
**Owns State:** `datasets` (versioned per broker per tenant), `feature_store`, `experiments`
**Input:** `Data Lake` (per broker per tenant) + `AnalyticsResult` (read) + `config` snapshots
**Output:** `ResearchReport` / `DatasetVersion`

> **Expanded for M9** — each file simple + pseudocode + connection. This patches into master §21.

---

### 1. Purpose in One Sentence
> **M9 is the lab where you try `rsi_period 14→12` on 2024-01→03 data without touching live `M4`.**


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution <500ms, tick→parquet <50ms, analytics <20ms, risk <10ms. No calc/file/abnormality tolerated — async I/O + sync pure calc + event_bus + frozen contracts + golden tests + well-commented WHY.*

### 2. The 3 Sub-Modules

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M9.1** | Datasets | Versioned `parquet` slices from Lake | Add dataset |
| **M9.2** | Features | Feature store (optional) | Add feature |
| **M9.3** | Experiments | Runs + registry + attribution | Run experiment |

### `research/datasets/{manager, versioning, validation}`
**Does:** `dataset_manager.create(broker=deriv, user_id=42, symbol=EURUSD, start=2024-01, end=2024-03) → dataset_id=hash(broker+user+symbol+date+filters)` + `versioning` stores `dataset_id → parquet list`.
**Pseudocode:** `dataset_id = sha256(f"{broker}:{user_id}:{symbol}:{start}:{end}")`

### `research/features/{registry, store, versioning}`
**Does:** `feature_store.get("rsi_14", dataset_id)` — cached `AnalyticsResult` features for `dataset_id`, so `M9.3` doesn’t recompute `RSI` 100 times.
**Connects:** Reads `M3` via `feature_engine` but *offline* (Lake, not live MarketData).

### `research/experiments/{runner, registry, parameter_search}` + `attribution`
**Does:** `runner` does `grid_search(rsi_period=[12,14,16])` → 3 backtests via `M8` virtual_broker → picks best. `registry` is MLflow-like `experiment_id → params + report`.
**Pseudocode:**
```python
for p in [12,14,16]:
    report = backtesting.run(dataset_id, strategy_params={"rsi_period":p})  # reuses M3-M6 code offline
    registry.log(experiment_id, params={"rsi_period":p}, metrics={"sharpe":report.sharpe})
```
**Connects:** Reads `M2` Lake, `M3` features, writes `ResearchReport` → `M4` may promote `rsi 12` to `config/strategies/momentum.yaml`, `M11` may summarize report.

**Never imports `M6 broker_adapters`** — lab never trades.



---

#### Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Module | Sub-Module | Gate |
|-------|--------|------------|------|
| **P45** | M9.1 | Datasets — `dataset_manager` (replays `MarketData → TradePlan` lineage) | `dataset` versioned |
| **P46** | M9.2 | Features — `feature_registry` (reads `M3.0` capability) | `feature` versioned |
| **P47** | M9.3 | Experiments — `experiment_runner` (never imports `M6` execution, uses `TradePlan`) | `experiment` |

**Parallel after M2+M3 (incl. M3.0):** Research reuses `TradePlan` path for `M8` replay — respects `M3.0` gates.

### 3. Open Questions for M9

- [ ] Feature store now (Feast) or experiment_runner first?
- [ ] Should `M9` be separate deployable or merged with `M8` lab?
---

## 22. Experience — `backend/engines/experience/` + `api` + `command` + `frontend` — EXPANDED (M10 Experience, v1.3 BEST APPROACH shows TradePlan + 3 Layers + Institutional)

# M10 — EXPERIENCE (Delivery — Synchronization + API + Command + Frontend) — EXPANDED DISCUSSION
### Publishes state, serves API, interprets commands, renders UI. Per-tenant projection, no trading logic.

**Module Type:** Delivery — owns the *view*, not the state. Per-tenant topics + per-tenant API auth.
**Owns State:** `subscription_state`, `sequence` per tenant per topic, `command_queue`
**Input:** Read projections: `PortfolioState`, `Order`, `ExecutionResult`, `Health` — via `query_models` + events, filtered by `user_id+broker_id`
**Output:** WS `Snapshot` + `Delta` (seq + Patch) + REST JSON + Command responses (per-tenant)

> **Expanded for M10** — each file simple + pseudocode + connection. This patches into master §22+§27+§28+§32.

---

### 1. Purpose in One Sentence
> **M10 shows `user 42/Deriv` *their* portfolio *only*, lets them type `/forex EURUSD` or `GET /portfolio`, and forwards `POST /trade` through `M5-M6` — never calculates RSI/margin itself.**


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution <500ms, tick→parquet <50ms, analytics <20ms, risk <10ms. No calc/file/abnormality tolerated — async I/O + sync pure calc + event_bus + frozen contracts + golden tests + well-commented WHY.*

### 2. The 4 Sub-Modules

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

### 4. How Settings Use 3 Layers (Simple)

> **Plain English:** Your dashboard settings (allowed assets, alerts, dashboard prefs) follow same `Automatic → Your Override → Reset`. Same pattern as Risk.

### 5. Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Module | Sub-Module | Gate |
|-------|--------|------------|------|
| **P48** | M10.1 | Synchronization — `event_publisher (async, per-tenant seq, retry, well-commented WHY seq), sequence_manager (sync pure)` (publishes `TradePlan` status `PENDING_RISK→APPROVED`) | `seq` per tenant |
| **P49** | M10.2 | API — 18 thin routers (async FastAPI, no business logic, well-commented WHY thin) (`GET /trade_plans/{id}`) | `thin router` |
| **P50** | M10.3 | Command — `parser, registry` + first 10 commands | `parse` |
| **P51** | M10.4 | Frontend — `features/*, realtime/*` (shows `entry/SL/TP` from `TradePlan`) | `realtime` |

**Parallel after M5-M7 (incl. TradePlan):** Experience shows `TradePlan` (not naked Signal) in UI — `entry/SL/TP` auditable per `user_id+broker_id`.

### 3. Open Questions for M10

- [ ] WS transport: Redis Pub/Sub now → Kafka later — abstract via `services/messaging`?
- [ ] Commands: Phase 3 first 10 (`marketday, forex, risk, trade+confirm`) → add others later?
- [ ] Frontend `types` generation via `openapi.json` — lock this flow?
---

## 23. Supporting Services — `backend/services/` — Important But Not Engines

Engines should not depend on random utilities. Services are thin, stateless helpers.

```text
backend/services/
├── reference_data/           # Section 7 — The Knowledge Base (detailed there)
├── authentication/           # JWT, API keys, broker token vault (encrypted at rest)
│   ├── auth_manager.py
│   ├── jwt.py
│   └── broker_vault.py       # Stores deriv/mt5 tokens in encrypted DB, never in logs
├── authorization/            # RBAC: permissions.py is source, this enforces
├── notifications/            # Email, Telegram, webhooks for alerts
├── scheduling/               # APScheduler for cron: reconciliation jobs, candle closes
├── caching/                  # Redis wrapper: get/set with namespace per engine
├── messaging/                # Event bus abstraction (Redis Pub/Sub → Kafka later)
├── serialization/            # JSON/parquet/Avro helpers, consistent decimal handling
└── audit/                    # Writes to database/events/trade_audit.py
```

**Rule:** Services never import engines. Engines may import services.

---

## 24. Database Architecture — Where Data Lives (Simple: 3 Boxes + Right Tool for Right Job)

> **Plain English:** We use **different boxes for different jobs**, but your code never needs to know which box. You just ask `get_candles(EURUSD, 1m)` and the system finds it whether it's in fast memory, fast disk, or cheap archive.

**The 3 Boxes (Logical Tiers, not 3 separate products you must manage Day 1):**

| Box | What it holds | Example Technology (can be swapped later) | How long it stays |
|-----|---------------|-------------------------------------------|-------------------|
| **HOT** — Right now | Prices you are trading on *this second* — latest quote, active candle, active TradePlan, open orders | **Redis** (super-fast memory) + **PostgreSQL + TimescaleDB** (durable table for candles/orders) — Redis is *never* the truth, Postgres is | Seconds to hours — then it moves |
| **WARM** — Recent, you ask often | Last 30-90 days of candles, signals, trade_plans, ledger — for backtesting, research, dashboards | **TimescaleDB** (fast time queries) + **Parquet files on object storage** (cheap scan) | 0-90 days (configurable) |
| **COLD** — Long memory | Raw ticks as Deriv sent them, old candles, old logs — rarely asked but must not be lost | **S3-compatible object storage** (like AWS S3 or MinIO) with Parquet/JSONL compressed | 90 days → 7 years (configurable, you set) |

**A nightly robot moves data automatically:** `HOT (Redis) → WARM (TimescaleDB/Parquet) → COLD (S3)` based on age/size/policy. You set `WARM = 90 days` in `config/storage.yaml`, robot does it. Researcher never needs to know if `2025-01-01` candle is in WARM or COLD — `MarketDataRepository.get(instrument="EURUSD", start="2025-01-01")` works either way.

**Who owns what — so no single database is forced to do everything (research §20):**

| Database | Owns (durable truth) | Example |
|----------|----------------------|---------|
| **PostgreSQL** | Users, tenants, accounts, credentials metadata (not the password itself, just where it is), strategies, signals, trade_plans, risk decisions, orders, executions, positions, ledger, performance summaries | `SELECT * FROM orders WHERE user_id=42 AND broker_id='deriv'` |
| **TimescaleDB (extension on Postgres)** | Time-series operational — *canonical candles, canonical quotes, operational measurements* — hypertable compressed, auto-retained 90d | `SELECT * FROM candles WHERE instrument='FX.EURUSD' AND interval='1m'` |
| **Redis** | Temporary current state only — latest quote, cache, locks. If Redis dies, Postgres rebuilds it. | `GET FX.EURUSD:1m:latest` → `{bid:1.085, ask:1.0851}` |
| **Object Storage + Parquet** | Large history — raw provider ticks (immutable), research datasets, archives | `market-data/provider=deriv/instrument=FX.EURUSD/date=2026-09-22/data_type=ticks/ticks.parquet` |
| **ClickHouse (optional later)** | Only if you query *billions* of ticks and TimescaleDB gets slow — `5 years EURUSD tick stats` — not needed Day 1 | Add later without rewriting strategies |
| **OpenSearch (optional)** | Logs, search `signal_id, trade_plan_id` | For debugging, not trading |

**Simple rule:** Analytics/Strategy never does `open parquet file`. It does `repository.get_candles(...)` — storage layer decides which box.

**Market data can be shared, your account data cannot (research #7 best approach):**
- **Shared (global) where identical:** `Deriv EURUSD 1m candles` — one copy for all users who subscribe to `EURUSD` on Deriv. Saves terabytes.
- **Private (per-tenant `user_id+broker_id`):** `subscriptions, signals, trade_plans, orders, positions, ledger, credentials` — isolated `WHERE user_id+broker_id`. User 42@deriv cannot see user 99@headway.

**Example partition on disk (easy to understand):**
```text
market-data/
  provider=deriv/
    instrument=FX.EURUSD/
      date=2026-09-22/
        data_type=candles/
          interval=1m/
            candles.parquet
```
For private data:
```text
ledger/
  broker=deriv/
    user_id=42/
      date=2026-09-22/
        ledger.parquet
```




**19 Systems → 12 Modules — Quick Reference (so you don't hunt):**
See §3 table above + §3b. Every research system has a home — no duplicate Risk system, no extra microservice Day 1.

**Configuration Management (logical, §2b):**
*Stores `config/*.yaml` versioned in Postgres `config_versions`, precedence `Platform Safety > Automatic Default > User Override > Effective`, history `who changed what, old vs new, when, why`, rollback.*

**Notification & Alert (logical, inside M12):**
*`M12.1` owns `alert rules, severity, routing, channels (email/push), delivery status, retry, history` — user-configurable alerts + system/risk/broker alerts.*

**User & Admin (logical):**
*`M1 + M10` owns `User Management` (identity, roles, activation), `M12 + §2.2` owns `System Administration` (feature flags, supported assets, emergency controls).*

## 25. Intelligence / AI Layer — `backend/intelligence/` — The Narrator, Not The Trader — EXPANDED (M11 Intelligence, v1.3 BEST APPROACH narrates TradePlan + Institutional)

# M11 — INTELLIGENCE (AI Narrator — Read-Only) — EXPANDED DISCUSSION
### Explains, summarizes, retrieves. Never trades. Per-tenant context, guarded in code.

**Module Type:** Stateless per-request — read-only narrator, per-tenant.
**Owns State:** `llm_audit` (per-tenant logs) — no business state
**Input:** User prompt + read context **only for `user_id+broker_id` tenant** (from `M3` `AnalyticsResult`, `M5` `PortfolioState` per-tenant, `M9` `ResearchReport`)
**Output:** `Answer` (citations, provenance) — never `Order`

> **Expanded for M11** — each file simple + pseudocode + connection. This patches into master §25.

---

### 1. Purpose in One Sentence
> **M11 answers “Why did we short EURUSD?” by reading `M4` `explainability` for *that* tenant’s `Signal`, not by trading.**


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution <500ms, tick→parquet <50ms, analytics <20ms, risk <10ms. No calc/file/abnormality tolerated — async I/O + sync pure calc + event_bus + frozen contracts + golden tests + well-commented WHY.*

### 2. The 3 Sub-Modules

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M11.1** | Context & Prompts | Gather per-tenant context, versioned prompts | Change prompt |
| **M11.2** | Validation | Cross-check vs DB/API before answer | Change validator |
| **M11.3** | Guard & Audit | Block hallucinated trades, log | Change allowlist |

### `intelligence/context_builder.py`, `prompt_manager.py`, `source_registry.py`
**Does:** `context_builder` does `tenant_context.set(user_id, broker) → get PortfolioState(user_id,broker) + recent Signals(user_id,broker) + AnalyticsResult → build prompt`; `prompt_manager` holds versioned `prompts/summarize_portfolio_v2.txt` (no hard-coded strings); `source_registry` allowlists `internal DB read models, Lake, config` — not live broker.
**Pseudocode:**
```python
def build_context(prompt, user_id, broker):
    tenant_context.set(user_id, broker)  # per-tenant!
    portfolio = portfolio_risk.get_state(user_id=user_id, broker=broker)  # only his
    signals = alpha.get_signals(user_id=user_id, broker=broker, limit=5)
    return prompt_manager.render("explain_signal", {portfolio, signals, prompt})
```

### `factual_validator.py`, `calculation_validator.py`, `provenance.py`
**Does:** `factual_validator` checks `LLM says "RSI 70" → actually `AnalyticsResult.features["rsi_14"]==68? If mismatch → block or mark "unverified"`; `calculation_validator` recomputes `PnL = (mark - avg)*qty` via `M3/M7` instead of trusting LLM math; `provenance` attaches `source: AnalyticsResult id 123, timestamp`.
**Pseudocode:**
```python
def validate(answer, user_id, broker):
    claimed_rsi = extract(answer, "RSI")
    actual = analytics.get_features(symbol="EURUSD", user_id=user_id, broker=broker).features["rsi_14"]
    if abs(claimed_rsi - actual) > 1: answer = guard.block("hallucinated RSI")
    return answer
```

### `response_builder.py`, `response_guard.py`, `tool_policy.py`, `audit.py`
**Does:** `tool_policy` **hard-coded** `ALLOWED=["analytics.query","portfolio.query"]`, `BLOCKED=["order.*","execution.*"]` — cannot be enabled via config, requires code + bible change. `response_guard` blocks disallowed content (e.g., hallucinated trade). `audit` logs every `prompt + tool call + answer` per-tenant.
**Pseudocode:**
```python
ALLOWED = ["analytics.query","portfolio.query","market_data.query"]
BLOCKED = ["order.*","execution.*","risk.override"]
def call_tool(name, user_id, broker):
    if name in BLOCKED: audit.log("blocked", name, user_id, broker); raise GuardError
    if name not in ALLOWED: raise GuardError
    return tool_fn(tenant_context.set(user_id, broker))
```
**Connects:** `M10 Experience` chat panel → `M11` → reads `M3/M5/M9` per-tenant, never `M6`.

**Build Independence:**
```bash
pytest modules/intelligence --mock=PortfolioState --expect=Answer
# tool_policy test: attempt "order.create" → guard rejects + audit, per-tenant mocked
```



---

#### Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Module | Sub-Module | Gate |
|-------|--------|------------|------|
| **P52** | M11.1 | Context & Prompts — `context_builder` (includes `TradePlan` entry/SL/TP + `M3.0` status) | `context` |
| **P53** | M11.2 | Validation — `factual_validator` (checks `TradePlan` RR/validity) | `factual` |
| **P54** | M11.3 | Guard & Audit — `tool_policy` (BLOCKS `order.*` — AI narrates `TradePlan`, never executes) | `BLOCKED` |

**Parallel after M3+M5 (incl. TradePlan):** `M11` narrates `TradePlan` (`why entry 1.085 SL 1.0835`) but cannot trade — `test_ai_cannot_trade` checks `TradePlan` not `Order`.

### 3. Open Questions for M11

- [ ] Should `M11` ever *suggest* params (`rsi 12→14`) as read-only suggestion (human approves), or strictly explain?
- [ ] LLM provider: OpenAI vs local — where key stored?
---

## 26. Ops — `backend/monitoring/` + `testing_suite` + `deployment` — EXPANDED (M12 Ops, v1.3 BEST APPROACH 19→12 + Notification/Admin + Institutional)

# M12 — OPS (Monitoring + Testing + Deployment — Cross-Cutting) — EXPANDED DISCUSSION
### Observes everything, owns nothing in trading path. Per-tenant observability, read health from all.

**Module Type:** Platform — cross-cutting, read-only (except `kill_switch` via `M5`). Per-tenant metrics.
**Owns State:** `metrics` (Prometheus per tenant), `alerts`, `incidents`, `deployment_state`
**Input:** `HealthStatus` per `user_id+broker_id` per Module + `MarketData` quality + `ExecutionResult` latency
**Output:** `Alert` (per-tenant), `Runbook` execution

> **Expanded for M12** — each file simple + pseudocode + connection. This patches into master §26+§30+§40.

---

### 1. Purpose in One Sentence
> **M12 watches every Module per tenant (`user 42/Deriv` latency, `user 99/MT5` gap) and owns the tests that prove the bible is enforced.**


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution <500ms, tick→parquet <50ms, analytics <20ms, risk <10ms. No calc/file/abnormality tolerated — async I/O + sync pure calc + event_bus + frozen contracts + golden tests + well-commented WHY.*

### 2. The 3 Sub-Modules

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M12.1** | Monitoring | Metrics, alerts, health, runbooks (per-tenant) | Add alert |
| **M12.2** | Testing Suites | Mirrors prod, per-tenant isolation tests | Add test |
| **M12.3** | Deployment & Data Ops | Docker, envs, Lake retention (per-tenant) | Change deploy |

### M12.1 — Monitoring — The Watcher (Per-Tenant)

#### `monitoring/metrics.py`, `alerts.py`, `health.py`, `data_monitor.py`, `latency_monitor.py`, `runbooks/*`
**Does:** `metrics` Prometheus counters `ticks_ingested{tenant="42:deriv"}`, `health` aggregates `M1-M11 health` per tenant, `data_monitor` tracks `lag per tenant per broker`, `latency_monitor` per hop `ingest→analytics→risk` p95 per tenant, `alerts` → `notifications` (Telegram/PagerDuty) per tenant, `runbooks/{data_outage.md, unknown_state.md, kill_switch.md}` step-by-step.
**Pseudocode:**
```python
def on_health(health: HealthStatus, user_id, broker):
    metrics.gauge(f"health{{tenant={user_id}:{broker},module={health.engine}}}", 1 if health.status=="healthy" else 0)
    if health.status=="unhealthy": alerts.send(f"{health.engine} unhealthy for {user_id}:{broker}", tenant=(user_id,broker))
```
**Connects:** Reads `M1-M11 health` per tenant, `M10` shows `M12.1` badges.

---

### M12.2 — Testing Suites — The Proof (Mirrors Prod, Per-Tenant)

#### `testing_suite/{unit_tests, integration_tests, failure_tests, security_tests, performance_tests, contract_tests}`
**Does:** `unit_tests/data/test_tick_normalizer` per broker per tenant; `integration_tests/end_to_end` `tick (user 42/Deriv) → ledger (42/Deriv)` — full chain per tenant; `failure_tests/data_failure` `gap 1001 missing for user 42/Deriv → recovery`; `security_tests/test_cross_tenant_isolation` (must pass); `performance_tests` `k6 10k ticks/s per tenant`; `contract_tests` snapshot per contract per tenant.
**Pseudocode (isolation test):**
```python
def test_cross_tenant_isolation():
    tenant_context.set(user_id=42, broker="deriv")
    db.insert(Position(symbol="EURUSD", qty=1.0, user_id=42, broker="deriv"))
    tenant_context.set(user_id=99, broker="deriv")
    assert db.get_positions(user_id=99, broker="deriv") == []  # cannot see 42's
    tenant_context.set(user_id=42, broker="mt5")
    assert db.get_positions(user_id=42, broker="mt5") == []  # cannot see deriv's
```
**Connects:** Every Module’s `Definition of Done` requires its tests here.

---

### M12.3 — Deployment & Data Ops — The Ship & Retain

#### `deployment/{Dockerfile, docker-compose.yml, k8s/}, data/lake/{retention}, scripts/{migrate, seed}`
**Does:** `Dockerfile` `python:3.11` → `uvicorn app.main`; `compose` `FastAPI + Postgres + Redis` per env; `k8s` later; `retention` `raw_ticks 30d hot, 1y cold, audit 7y` per tenant; `migrate` `alembic upgrade` per tenant schema.
**Pseudocode:**
```python
# deployment is multi-tenant single binary — tenant isolation at runtime via M1.4, not per-tenant deploy
# retention.py
def retain(date, user_id, broker):
    if date < now - 30d: move_parquet(f"brokers/{broker}/user_id={user_id}/date={date}/", cold_storage)
```
**Connects:** `M2 Lake` + `M1 config` env overlays.



---

### 4. What M12 Also Covers Logically (No New Code, Just Headers)

> **Plain English:** `M12` is where `Operations` live. Research lists 4 systems inside it:
> - **Monitoring & Observability** — health, metrics, logs, traces (`M12.1`)
> - **Notification & Alert** — alert rules, severity, email/push channels, retry, history (`M12.1` + `M10` UI)
> - **Audit & Compliance** — who changed what, old→new, when, why — append-only `M7.3 Ledger + M11 audit` (`M12` just ensures it)
> - **System Administration** — feature flags, supported brokers/assets, emergency `kill_switch` — `M12 + §2.2 Platform Runtime`

All 3 config layers use: `Automatic Default → Your Override → Reset to Default`. Hard safety boundaries can never be overridden.

### 5. Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Module | Sub-Module | Gate |
|-------|--------|------------|------|
| **P55** | M12.1 | Monitoring — `metrics (async collect, well-commented WHY), runbooks (well-commented WHY each step)` (`TradePlan` latency `M4.5→M5 <10ms`, `M3.0` gates) | `alert` |
| **P56** | M12.2 | Testing Suites — `unit/integration/failure/contract` (`test_trade_plan_rejects_expired`, `test_capability_unavailable`) | `coverage` |
| **P57** | M12.3 | Deployment — `Docker, k8s` — Modular Monolith Day 1 → extract via Redis/Kafka later | `deploy` |

**Continuous:** Ops now tracks `TradePlan` lineage `Execution←TradePlan←Signal` per tenant + `M3.0` capability metrics.

### 3. Open Questions for M12

- [ ] Metrics: Prometheus now vs Datadog later?
- [ ] Runbooks: live in `M12` or top-level `runbooks/`?
- [ ] Deployment: Modular Monolith (1 proc, in-proc queues) Day 1 → extract via Redis/Kafka later — confirm?
---

## 27. API Layer — `backend/app/api/v1/` — Thin Interface — STUB — MERGED INTO M10 Experience §22 (M10.2)

This section is now a **stub**. The full `API` specification lives in **§22 M10 Experience → M10.2 API**. See `modules/10_M10_Experience.md` for per-file `APIRouter`, per-tenant isolation `WHERE user_id+broker_id`, `schemas` validation, and `synchronization` integration.

> **Do not implement from this stub — implement from §22.**

---

### Per-File (see §22 M10.2)
| File | Purpose |
|------|---------|
| `api/v1/*.py` (18 routers) | Thin routers, no business logic, delegate to `M5-M7` services + `M10.1 synchronization` |
| `api/schemas/*.py` | `pydantic` request/response validation |
| `api/deps.py` | `Depends(get_current_user)` → `tenant_context` injection |

## 28. Command System — `backend/app/command/` — Natural Language Interface — STUB — MERGED INTO M10 Experience §22 (M10.3)

This section is now a **stub**. The full `Command` specification lives in **§22 M10 Experience → M10.3 Command**. See `modules/10_M10_Experience.md` for `parser`, `registry`, per-tenant `tool_policy` guard, and `HELP.md`-driven disclosure.

> **Do not implement from this stub — implement from §22.**

---

### Per-File (see §22 M10.3)
| File | Purpose |
|------|---------|
| `command/parser.py` | `parse('analyse frxXAUUSD')` → `{intent, entities}` |
| `command/registry.py` | Maps intent → `M3/M5/M7` read model call |
| `command/tools/*.py` (30 files) | Each tool = one read-only operation, audited per tenant |

## 29. Configuration — `backend/config/` — Single Source of Truth, No Hardcoding

```text
backend/config/
├── assets/
│   ├── asset_classes.yaml    # e.g., forex: {pip_decimals: 4, vol_window: 20}
│   ├── forex.yaml            # Symbols, trading hours
│   ├── crypto.yaml
│   ├── commodities.yaml
│
├── brokers/
│   ├── deriv.yaml            # {api_url, capabilities: [ticks, candles], rate_limit: 100/s}
│   ├── mt5.yaml
│   └── headway.yaml
│
├── strategies/
│   ├── momentum.yaml         # params: {lookback: 20, threshold: 0.02}
│   ├── trend_following.yaml
│   ├── cross_sectional_momentum.yaml
│   ├── mean_reversion.yaml
│   ├── mean_reversion_grid.yaml
│   ├── statistical_arbitrage.yaml
│   ├── cross_asset_relative_value.yaml
│   ├── microstructure.yaml
│   ├── liquidity_event.yaml
│   ├── systematic_macro.yaml
│   └── event_driven.yaml
│
├── execution/
│   ├── algorithms.yaml       # {default: market, twap: {slice_interval: "1m"}}
│   └── routing.yaml          # {EURUSD: {primary: mt5, fallback: deriv}}
│
├── risk/
│   ├── limits.yaml           # {max_exposure_per_symbol: 0.1, max_leverage: 10, min_margin: 0.5}
│   ├── sizing.yaml           # {method: vol_target, vol_target: 0.02}
│   ├── kill_switches.yaml
│   └── stress.yaml
│
├── system/
│   ├── system.yaml           # {base_currency: USD, clock: utc}
│   ├── logging.yaml
│   └── feature_flags.yaml    # {enable_stat_arb: false}
│
└── environments/             # Overlay per env (deep-merged)
    ├── development.yaml
    ├── testing.yaml
    ├── paper.yaml
    ├── canary.yaml
    └── production.yaml
```

**Rules:**
- No `risk.yaml` at root AND `risk/` folder. Structure above is canonical — no duplication.
- Loaded via `core/config.py` using `pydantic-settings` with validation. Invalid YAML → startup fails loudly.
- Secrets (API keys) in `.env` + `.env.example`, never in YAML. `.env` not in git.

---

## 30. Testing Architecture — `backend/testing_suite/` — Mirrors Production — STUB — MERGED INTO M12 Ops §26 (M12.2)

This section is now a **stub**. The full `Testing` specification lives in **§26 M12 Ops → M12.2 Testing Suites**. See `modules/12_M12_Ops.md` for `unit/integration/failure/contract` tiers, `isolation_guard`, `speed_budget`, and `fake_broker`.

> **Do not implement from this stub — implement from §26.**

---

### Per-File (see §26 M12.2)
| File | Purpose |
|------|---------|
| `testing_suite/unit/` | Pure functions: `M3.2` indicators, `M5.3` checks |
| `testing_suite/integration/` | `M2 → M3 → M4 → M5 → M6` per-tenant chain, `fake_broker` |
| `testing_suite/failure/` | `kill_switch`, `unknown_state`, `late_ticks` |
| `testing_suite/contract/` | Enforces `M→M` contracts: `Signal`, `Order`, `RiskDecision` |

## 31. Data Lake — The Big Historical Library (Partitioned, Replayable, Cheap)

> **Plain English:** Lake is your **WARM + COLD** library. Every tick and candle ever received, plus a catalog that knows where each file is, so you can replay `2026-01-01` exactly as it happened for backtesting.

**How Lake is organized — folders you can picture:**

```text
data/lake/
├── raw/                # exactly as Deriv sent it, immutable
│   └── provider=deriv/instrument=FX.EURUSD/date=2026-09-22/ticks.parquet
├── canonical/          # normalized MarketData (frxEURUSD → EURUSD, 3 timestamps)
│   └── provider=deriv/instrument=FX.EURUSD/date=2026-09-22/canonical.parquet
├── candles/            # aggregated 1m,5m,1h,1d (built by M2.4)
│   └── provider=deriv/instrument=FX.EURUSD/interval=1m/date=2026-09-22/candles.parquet
└── catalog/            # small table: what files exist, their quality (COMPLETE/GAP_AFFECTED)
```

**For private account data (separate, not shared):**
```text
data/lake/private/
  broker=deriv/user_id=42/date=2026-09-22/
    signals.parquet, trade_plans.parquet, orders.parquet, ledger.parquet
```

**Lifecycle robot (same as §24):**
`HOT Redis (seconds) → WARM TimescaleDB+Parquet (0-90 days, fast) → COLD S3 (90d-7y, cheap)` automatically. You query `get_candles(EURUSD, "2025-01-01", "2025-01-31")` and Lake + TimescaleDB collaborate — you don't care which box.

**Idempotency:** Same Deriv tick `provider=deriv, provider_event_id=tick-id-123, epoch=1234567890` processed twice → Lake writes once (checked by `provider+id+epoch`).

**Why Lake matters for you (non-technical):** Want to know why a trade lost on `2026-09-20`? Replay that day's raw ticks through same `TradePlan` logic — no guessing. That's `M8.1 tick_replay` reading Lake.



## 32. Frontend — `frontend/` — Mirrors Backend Boundaries — STUB — MERGED INTO M10 Experience §22 (M10.4)

This section is now a **stub**. The full `Frontend` specification lives in **§22 M10 Experience → M10.4 Frontend**. See `modules/10_M10_Experience.md` for `features/*`, `realtime/*`, per-tenant WS topics, `replay_sync`, and `never copies logic` rule.

> **Do not implement from this stub — implement from §22.**

---

### Per-File (see §22 M10.4)
| File | Purpose |
|------|---------|
| `frontend/features/portfolio/` | Reads `M5` read model via `api` + `sync` |
| `frontend/realtime/useMarketStream.ts` | Subscribes `sync:42:deriv:frxXAUUSD:ticks` per tenant |
| `frontend/components/Chart.tsx` | Candles from `M2 Lake` via `api` |
| `frontend/lib/syncClient.ts` | `seq` + `replay` replay on reconnect |

## 33. Dependency Rules — The Enforcement Layer

**If this section is violated, modularity dies.**

```
                         CORE  (no deps)
                          ↑
                          │ (everyone may import core)
                       SERVICES  (may import core + contracts)
                          ↑
                          │ (engines may import services + contracts)
DATA ───────────────→ ANALYTICS ─→ STRATEGY ─→ SIGNAL ─→ PORTFOLIO ─→ RISK ─→ ORDER ─→ EXECUTION ─→ RECONCILIATION ─→ ACCOUNTING
  │                       ↑           ↑          ↑          ↑           ↑        ↑           ↑              ↑               ↑
  │                       └───────────┴──────────┴──────────┴───────────┴────────┴───────────┴──────────────┴───────────────┘
  │                                       Only via contracts, never via engine import
  │
  └→ STORAGE                SIDE ENGINES: BACKTESTING, SIMULATION, PAPER_TRADING, CANARY, RESEARCH, SYNCHRONIZATION
         (may read canonical lake)        (may import contracts + services, but side engines NEVER import main-path engines directly)

INTELLIGENCE  (may import contracts + services + query_models, NEVER engines/order/execution)
FRONTEND      (may call API + WS, NEVER DB or engines)
API           (may call engines via contracts, never implement logic)
COMMAND       (may call services + engine read methods via contracts)
```

**Forbidden Imports (CI will fail via `import-linter`):**
```
❌ execution  -> strategy, analytics, signal (execution never looks back)
❌ risk       -> strategy implementation (risk sees Signal contract, not strategy code)
❌ analytics  -> broker_adapters
❌ strategy   -> broker APIs (deriv client, mt5 client)
❌ intelligence -> order, execution (AI trading)
❌ frontend   -> database, engines
❌ order      -> execution/broker_adapters
❌ any engine -> intelligence
```

**Allowed:**
```
✅ strategy   -> contracts/signal, contracts/analytics, contracts/market_data, services/reference_data
✅ risk       -> contracts/signal, contracts/portfolio, services/reference_data
✅ execution  -> contracts/order, services/reference_data, core/retry_policy
✅ backtesting -> contracts/*, data/lake (not live engines)
```

**Enforcement:**
- `import-linter` config in `backend/.importlinter` — runs in CI.
- `backend/docs/architecture/dependency_rules.md` has the allow-list table.
- PRs that add `from engines.` in another engine require bible amendment.

---

## 34. Data Flow & Sequence Diagrams

### Flow 1: Live Tick → Fill (Happy Path)

```
Deriv WS --tick--> Data.ingestion -> normalization -> validation -> quality -> storage (parquet + catalog)
                         ↓ MarketData(quality=pristine)
               Analytics.feature_engine (computes RSI, vol, etc.) -> AnalyticsResult
                         ↓
               Strategy.momentum (checks rsi>70, vol filter) -> Signal(strength=0.75, valid_until=+60s)
                         ↓
               Signal Engine (validate, dedup, freshness) -> Validated Signal
                         ↓
               Trade Plan Engine M4.5 (entry/SL/TP/timing from Validated Signal + atr + M2b spec + M3.0 capability) -> TradePlan(entry=1.085 SL=1.0835 TP=1.088 valid_until=+60s, trade_plan_id)
                         ↓
               Portfolio (checks current exposure per-tenant)
                         ↓
               Risk.pre_trade (validates TradePlan: margin, exposure, spread, data_quality, RR, valid_until) + sizing (TradePlan.stop_distance → qty) -> RiskDecision(approved=True, qty=0.5, trade_plan_id)
                         ↓
               Order Builder (idempotency + capability) -> Order(state=pending, client_order_id=uuid5)
                         ↓
               Execution.routing (smart_router? deriv vs mt5) -> broker_adapters/deriv -> Broker (REST/WS)
                         ↓ ExecutionResult(fill)
               ExecutionState tracker
                         ↓
               Reconciliation (periodic or on fill) -> confirms broker fill = internal fill
                         ↓
               Portfolio position_manager updates Position
                         ↓
               Accounting ledger appends, pnl updates
                         ↓
               Synchronization publishes: portfolio.update, order.update, execution.update -> Frontend via WS
                         ↓
               Intelligence may summarize: "EURUSD momentum long filled 0.5 lots via Deriv"
```

### Flow 2: Gap Detected

```
Data.ingestion sees watermark 1001 after 0999 (gap) -> quality_engine marks gapped
 -> recovery_engine.gap_analyzer -> recovery_source_selector (REST candles) -> chunk_orchestrator fetches gap
 -> recovery_validator -> storage_writer backfills parquet -> catalog updated -> degradation_policy lifts degraded
Analytics.data_quality_inputs blocks signals on gapped interval unless strategy explicitly allows degraded
```

### Flow 3: Risk Rejection

```
TradePlan -> Risk.margin_check sees insufficient margin (or expired valid_until, missing SL/TP, RR<1) -> RiskDecision(approved=False, reason="insufficient_margin" | "trade_plan_expired")
 -> No Order created -> Synchronization publishes signal.rejected -> Frontend shows "Rejected: margin" -> Audit logged
 -> No downstream engines invoked.
```

### Flow 4: Unknown State Recovery

```
Execution sends Order, broker timeout (no ack) -> Order.state=unknown -> triggers Reconciliation.unknown_state_handler
 -> Queries broker -> finds broker rejected (duplicate client_order_id) -> Order marked rejected + audit -> Risk not charged
 OR -> finds broker filled -> Reconciliation recovers fill -> Portfolio + Accounting updated -> alert emitted
```

---

## 35. Failure Modes & Recovery — Every Engine Must Handle These

| Category | Failure | Detection | Engine Responsible | Policy |
|---|---|---|---|---|
| **Data** | WS disconnect | heartbeat | data/connection_manager | reconnect with jitter, mark degraded |
| | Gap | watermark skip | data/recovery_engine | auto-backfill, degrade downstream if unrecoverable |
| | Duplicate | hash+watermark | data/data_quality | dedup, never propagate |
| | Spike | sigma check | data/anomaly_detector | flag suspect, analytics may filter |
| **Analytics** | Stale data | freshness check | analytics/data_quality_inputs | skip feature calc, emit no AnalyticsResult |
| | Compute error | exception | analytics/engine | emit AnalyticsResult with error field, strategy skips |
| **Signal** | Duplicate signal | dedup | signal/deduplicator | drop, audit |
| | Stale signal | valid_until | signal/freshness + risk/data_quality_check | reject at Risk |
| **Risk** | Margin breach | margin_monitor (poll) | risk/emergency | asymmetric_lock + emergency_exit if threshold |
| | Drawdown breach | drawdown_monitor | risk/emergency | kill_switch strategy |
| **Order** | Idempotency clash | client_order_id exists | order/idempotency | return existing Order, don't resubmit |
| | Unknown state | timeout | order/state_machine + reconciliation | mark unknown, trigger reconciliation |
| **Execution** | Rejection | broker response | execution/broker_adapters | map to error_code, audit, no retry if permanent (insufficient margin) |
| | Partial fill | broker fill < qty | execution/child_order_manager + order/partial_fill_manager | track, may leave open or cancel remainder per policy |
| | Broker timeout | no response | execution/retry_policy | retry with same client_order_id (idempotent) up to N times |
| **Reconciliation** | Mismatch | comparator diff | reconciliation/state_comparator | asymmetric_lock + alert, manual recovery via state_recovery |
| | Broker offline | connection error | reconciliation/policy | pause reconciliation, mark degraded |

**Golden Rule:** No failure is silent. Every failure emits: `metrics increment + log with correlation_id + audit event + (if critical) alert`.


---
# Architecture Bible — Part 4: Security, Deployment, Roadmap & Definition of Done

---

## 36. Security & Broker Isolation

**Isolation is not optional — one broker's data must never leak to another's context.**

- **Broker Context:** `database/broker_isolation/broker_context.py` uses `ContextVar`. Every incoming API request and every engine call sets `broker_context.set(broker_id)` via middleware/decorator. All repository queries automatically inject `WHERE broker_id = :current`.
- **Vault:** Broker API keys/tokens stored in `services/authentication/broker_vault.py` — encrypted at rest (Fernet/AES), never logged, injected via `core/config.py` from `.env` or secret manager (AWS SM / GCP SM in prod).
- **Permissions:** `core/permissions.py` RBAC: `viewer` may read `portfolio/*`, `trader` may call `order/preview`, `admin` may trigger `kill_switch`. `app/command/command_permissions.py` enforces same for commands.
- **Audit:** Every state-changing operation (Signal → Order → Fill → Reconciliation correction) writes to `database/events/trade_audit.py` with `actor`, `correlation_id`, `before/after`, `reason`.
- **AI Guard:** `intelligence/tool_policy.py` is the security boundary for AI. Blocked tools cannot be enabled via config — requires code change + bible amendment.

**Tests:**
- `testing_suite/security_tests/test_broker_isolation.py` attempts cross-broker reads and expects failure.
- `testing_suite/security_tests/test_ai_cannot_trade.py` attempts to call blocked tool and expects guard rejection.

---

## 37. Implementation Roadmap — Module-Following (57 Sub-Module Phases) — v1.3 BEST APPROACH (M3.0 + M4.5 added)
> **Phases = Sub-Modules, delivered Module-by-Module, not time-based big-bang.** Each phase = one Sub-Module DONE. `55 → 57` after best-approach `M3.0 Capability` + `M4.5 Trade Plan`. Phases in same Module that don’t share state can run **in parallel** (`‖`).

### P01-P04 FOUNDATION — M1 (Days 1-10) — MUST be first, everyone depends on it

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P01** | M1.1 Contracts | `shared/contracts/*.py` 8 frozen schemas + migrations + snapshot tests | `pytest contract_tests` pass |
| **P02** | M1.2 Core Runtime | `BaseEngine`, `lifecycle`, `clock`, `health`, `logging` | `GET /health` HEALTHY with mocked Module |
| **P03** | M1.3 Config & Env | `config/**/*.yaml` + `feature_flags` | Startup fails loud with missing YAML key |
| **P04** | M1.4 Security & Isolation | `broker_context`, `permissions`, `vault`, `retry_policy` | `test_cross_broker_query_blocked` pass |

**Milestone:** `v0.1-foundation`

### P05-P14 DATA — M2 + M2b (Days 11-28) — Parallel after M1

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P05** | M2b.1 Instrument & Symbol | `instrument_master`, `symbol_mapping` | `to_canonical("frxEURUSD")==EURUSD` |
| **P06** | M2b.2 Specs & Capabilities | `contract_specs`, `broker_capabilities` | `get_spec(EURUSD,deriv)` |
| **P07** | M2b.3 Calendar | `trading_calendar`, `corporate_actions` | `is_open(...)` |
| **P08** | M2.1 Ingestion | `connection_manager`, `stream_manager`, `broker_feeds/deriv` (first broker) | WS heartbeat + reconnect |
| **P09** | M2.2 Normalization | `*_normalizer → MarketData (canonical)` | No `frxEURUSD` leaks |
| **P10** | M2.3 Validation | `*_validator` | Drops future tick |
| **P11** | M2.4 Transformation ‖ M2.5 OrderBook | `candle_builder` ‖ `book_manager` | `Tick→Candle` golden ‖ `Delta→Snapshot` seq |
| **P12** | M2.6 Data Quality | `duplicate_detector (exactly-once), quality_classifier` | No duplicate watermark |
| **P13** | M2.7 Recovery | `watermark_registry, gap_analyzer, chunk_orchestrator` | Gap backfill test |
| **P14** | M2.8 Storage & Lake | `data_writer, partitioner, catalog + data/lake/**` | `catalog` matches parquet |

### P15-P21 ANALYTICS — M3 (Days 29-43) — 7 sub-modules (BEST APPROACH: M3.0 added)

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P15** | **M3.0 Capability (NEW)** | `capability_registry, provider_capabilities` — `FULLY_SUPPORTED/PROXY/UNAVAILABLE` per broker | `test_order_book_unavailable_on_deriv` |
| **P16** | M3.1 Feature Engine & Profiles | `feature_engine, profiles` cache per tenant | `cache_hit <1ms` |
| **P17** | M3.2 Per-Asset P1 | First 5 indicators (momentum/trend) — parallel | `rsi` golden |
| **P18** | M3.2 Per-Asset P2 | Remaining 12 indicators — parallel | `hurst` golden |
| **P19** | M3.3 Cross-Asset | `correlation, cointegration, hedge_ratio` | `corr 0.85` |
| **P20** | M3.4 Microstructure | `order_book_imbalance` (capability-gated) | `UNAVAILABLE` on Deriv → skip |
| **P21** | M3.5 Portfolio (ro) ‖ M3.6 Macro | `portfolio/margin` compute ‖ `rates/event_registry` | compute vs decide split |

### P22-P26 ALPHA — M4 (Days 44-60) — 5 sub-modules (BEST APPROACH: M4.5 added)

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P22** | M4.1 Strategy Registry | `base_strategy, registry, shadow_mode` | `registry.load()` |
| **P23** | M4.2 Strategy Families (P1) | `trend_following::ema_trend` first family (Tier1) | `Signal LONG` |
| **P24** | M4.3 Signal Validation | `validator, deduplicator, freshness` | `Drop stale` |
| **P25** | M4.4 Signal Orchestration | `conflict_detector, queue_prioritizer` | `prioritized` |
| **P26** | **M4.5 Trade Plan (NEW)** | `trade_plan/engine, entry/stop/target/timing` → `TradePlan` | `RR>1 && valid_until` |

**Tier 1 priority for Deriv (no volume):** `trend_following, momentum, mean_reversion, volatility_breakout, pullback, cross_sectional_momentum, statistical_arbitrage/spread, regime_based` — all `FULLY_SUPPORTED`. Tier 2 `tick_activity` = `PROXY_LIMITED ⚠️`. Tier 3 `order_book_imbalance/vpin/queue` = `UNAVAILABLE → DISABLED` via `M3.0`.

### P27-P31 PORTFOLIO & RISK — M5 (Days 61-77) — validates TradePlan

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P27** | M5.1 Portfolio State | `portfolio_state` (holdings per tenant) | `on_fill` per `user_id+broker_id` |
| **P28** | M5.2 Allocation | `allocator` (target weights) | `risk_budget` |
| **P29** | M5.3 Risk Checks | `risk_manager` 10 checks (validates `TradePlan` — `valid_until, SL/TP, RR, quality, spread, margin`) | `test_risk_rejects_expired_trade_plan` |
| **P30** | M5.4 Sizing | `position_sizer` `TradePlan.stop_distance → qty` | `strength* risk_budget` |
| **P31** | M5.5 Kill Switch ‖ M5.6 Fees | `kill_switch, asymmetric_lock` ‖ `fee_model` | `kill_switch` audit |

### P32-P36 EXECUTION — M6 (Days 78-93)

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P32** | M6.1 Order Builder | `order_builder, state_machine, idempotency uuid5` | `Order` from `TradePlan` |
| **P33** | M6.2 Router | `router, smart_router` | `venue` |
| **P34** | M6.3 Connectivity | `ws_client, rest_client, fix_client` | `reconnect` |
| **P35** | M6.4 Broker Adapters | `deriv_adapter` first, then `mt5, headway` (per-tenant token) | `Deriv` fill |
| **P36** | M6.5 Algos | `twap, market_impact, slippage_model` | `TWAP` |

### P37-P40 TRUST — M7 (Days 94-105)

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P37** | M7.1 Reconcilers | `reconciler_deriv, reconciler_mt5` per broker | `reconcile` |
| **P38** | M7.2 Comparator | `comparator, unknown_state, asymmetric_lock` | `mismatch` |
| **P39** | M7.3 Ledger | `ledger` append-only per tenant | `SELECT WHERE user_id+broker_id` |
| **P40** | M7.4 PnL | `pnl_calculator` FIFO, fees per tenant | `PnL` |

### P41-P44 VALIDATION — M8 (Days 106-125)

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P41** | M8.1 Backtesting | `tick_replay, virtual_broker, leakage_detector` | `no leakage` |
| **P42** | M8.2 Simulation | `spread_shock, gap_shock, latency_injector` | `shock` |
| **P43** | M8.3 Paper | `paper_broker` (real flow, fake money) | `paper` |
| **P44** | M8.4 Canary | `canary_router` 1% live | `canary` |

### P45-P57 PARALLEL LANES — After M7 stable

| Phase | Module | Sub-Module |
|-------|--------|------------|
| **P45** | M9.1 | Datasets — `dataset_manager` |
| **P46** | M9.2 | Features — `feature_registry` |
| **P47** | M9.3 | Experiments — `experiment_runner` |
| **P48** | M10.1 | Synchronization — `event_publisher, sequence_manager` |
| **P49** | M10.2 | API — 18 thin routers |
| **P50** | M10.3 | Command — `parser, registry` + first 10 commands |
| **P51** | M10.4 | Frontend — `features/*, realtime/*` |
| **P52** | M11.1 | Context & Prompts — `context_builder` |
| **P53** | M11.2 | Validation — `factual_validator` |
| **P54** | M11.3 | Guard & Audit — `tool_policy` |
| **P55** | M12.1 | Monitoring — `metrics, runbooks` |
| **P56** | M12.2 | Testing Suites — `unit/integration/failure/contract` |
| **P57** | M12.3 | Deployment — `Docker, k8s` — Modular Monolith Day 1 → extract via Redis/Kafka later |

**Milestone:** `v1.0` = `M1-M12` all `Airplane Test` pass, `57` phases `DONE`, `TradePlan` lineage `Execution ← TradePlan ← Signal ← Features ← Raw`.

## 38. Definition of Done (DoD) — For EVERY Engine

A PR claiming "Engine X done" is REVIEWED AGAINST THIS CHECKLIST. All boxes must be ticked.

For `engines/<engine>/`:

- [ ] **Interface:** Implements `BaseEngine` (`start`, `stop`, `health`)
- [ ] **Contracts:** Consumes/produces ONLY `backend/contracts/*` (verified by `import-linter`)
- [ ] **Config:** No magic numbers; all params from `backend/config/*.yaml` validated in `parameter_validator.py` or equivalent
- [ ] **Reference Data:** No hard-coded symbols/capabilities; uses `services/reference_data`
- [ ] **Clock:** Uses `core/clock.now()`, never `datetime.now()`/`time.time()` directly
- [ ] **Logging:** Structured logs with `engine=<name> correlation_id=<id>` via `core/logging.py`
- [ ] **Health:** `health()` reports healthy/degraded/unhealthy correctly
- [ ] **Failure Handling:** Handles every failure mode from Section 35 relevant to it, with metrics+alert+audit
- [ ] **Idempotency/State:** Where applicable (Order, Execution, Reconciliation) — retry-safe
- [ ] **Tests:**
  - [ ] `testing_suite/unit_tests/<engine>/` unit tests, >85% for logic-heavy
  - [ ] `testing_suite/contract_tests/engine_contracts/` contract test if it owns a contract
  - [ ] `testing_suite/failure_tests/` scenario for its critical failure
  - [ ] `testing_suite/integration_tests/` if it spans engines
- [ ] **Docs:** `backend/docs/engines/<engine>/README.md` with Purpose/Inputs/Outputs/Dependencies/Contracts/Config/Failure Modes/Tests/Extension Points
- [ ] **Audit:** Every state change writes to `trade_audit` with `correlation_id`
- [ ] **No Leaks:** No broker-specific code outside `broker_feeds/` or `broker_adapters/` — proven by grep + `import-linter`

---

## 39. Configuration & Secrets Management

- **YAML is king:** Required fields are annotated with `pydantic.BaseModel`. Optional fields have defaults.
- **Environment overlay:** `config/environments/{paper,canary,production}.yaml` deep-merges on top of base. Production enables stricter limits.
- **Feature Flags:** `config/system/feature_flags.yaml` → `core/feature_flags.py`. Flags checked in `strategy_registry`, `execution/routing_policy`, `risk/limits`. Changing a flag does not require redeploy (reload via `scheduling` or SIGHUP).
- **Secrets:** In `backend/.env` (dev) and Secret Manager (prod). Accessed via `core/config.py` → `Settings`. Never committed. `.env.example` lists all required keys without values.
- **Validation:** On startup, `core/config.py` validates ALL YAMLs. Fail fast with clear error: `"strategies/momentum.yaml missing threshold"`.

---

## 40. Deployment Model — Modular Monolith First

**Start as ONE deployable:** `backend/app/main.py` runs all engines in one process, each as an async worker/task. Communicate via in-process queues + `services/messaging` (Redis Pub/Sub abstracted).

**Scale independently later** by extracting an engine to its own service **without changing its contract**:
```
Before: Data Engine (in-process) --MarketData--> Analytics (in-process)
After:  Data Service (separate container) --MarketData via Redis/Kafka--> Analytics Service
```
Because contracts are versioned and transport-agnostic, extraction is a deployment change, not a rewrite.

**Environments:**
- `development` — local Docker Compose: FastAPI + Postgres + Redis
- `testing` — CI: Postgres (temp) + mocks for broker WS
- `paper` — full pipeline but `execution` routed to `paper_broker`
- `canary` — 1% prod traffic, real brokers, `canary/exposure_limits`
- `production` — full real brokers

Each has `config/environments/*.yaml` + isolated DB/Redis namespace.

**Docker:**
```
backend/Dockerfile       # Python 3.11+, installs requirements, runs alembic migrate, starts uvicorn
frontend/Dockerfile      # Node 18+, next build, next start
deployment/docker-compose.yml
deployment/k8s/*.yaml    # Later
```

---

## 41. Operational Runbooks — Summary

Detailed runbooks live in `backend/monitoring/runbooks/*.md`. Summary behavior:

- **Data Outage:** Data engine marks broker `degraded`, Analytics skips intervals, Signal stops firing for affected symbols, alert via `alerts.py` → `notifications` (Telegram). No orders for affected symbols (Risk `data_quality_check` blocks).
- **Broker Outage:** Execution marks broker `offline`, smart_router routes to fallback if policy allows, else Risk rejects with `broker_unavailable`. Open orders moved to `unknown` → Reconciliation retries.
- **Unknown State:** Order stays `unknown` until `unknown_state_handler` resolves. `asymmetric_lock` blocks new entries for symbol+broker, allows exits. Human intervenes via `reconciliation/state_recovery.py` audit tool if needed.
- **Kill Switch:** `POST /api/v1/risk/kill_switch {scope: "global"|"strategy:trend"|"broker:deriv"}` — idempotent, audited, propagates to Risk → Order (cancels pending) → Execution (cancels broker orders) → Portfolio (flatten if configured).
- **Rollback:** `monitoring/rollback.py` + `deployment_state.py` — one command to redeploy previous image + DB migration down if needed. Canary `rollback.py` additionally disables canary strategy.

---

## 42. Extension Points — Where to Add New Things (Without Violating Bible)

| Want to add... | Where | Contract |
|---|---|---|
| New broker (e.g., cTrader) | `engines/data/broker_feeds/ctrader/{stream,client,models}` + `engines/execution/broker_adapters/ctrader` + `config/brokers/ctrader.yaml` + `services/reference_data/symbol_mapping` | MarketData, Order, ExecutionResult (existing) |
| New strategy | `engines/strategy/<new_family>/strategy.py` + `@register_strategy` + `config/strategies/<new>.yaml` | Consumes AnalyticsResult, produces Signal |
| New feature (e.g., Hurst) | `engines/analytics/per_asset/hurst_exponent.py` + profile param in `config/assets/*.yaml` | Produces field in AnalyticsResult.features |
| New execution algo (e.g., TWAP) | `engines/execution/algorithms/twap.py` extends `BaseAlgo` + `config/execution/algorithms.yaml` | Consumes Order, produces child Orders + ExecutionResult |
| New asset class (e.g., FX options) | `config/assets/<new>.yaml` + `services/reference_data/instrument_master` + Analytics profiles | Uses existing MarketData with new `kind` if needed |
| New command | `backend/app/command/commands/<new>.py` + `@register_command` | Calls services/engines via read APIs |
| New AI tool | `intelligence/tool_policy.py` allowlist + `context_builder.py` | Must be read-only; if write needed, requires bible amendment |

**Prohibited Extensions (Require Bible Amendment + Review):**
- New top-level `engines/new_engine/` — only if it has a true lifecycle (per Section 2). If it's a registry/validator, it belongs in `services/`.
- Circular import between engines — never.
- AI writing orders — never.

---

## 43. Top 10 Pitfalls to Avoid (Learned from Amateur Review)

1. **Don’t put margin calc in Order.** It’s in `risk/pre_trade/margin_check.py`. Order just builds.
2. **Don’t hard-code `frxEURUSD` or `frxXAUUSD`.** Use `symbol_mapping` → canonical.
3. **Don’t compute analytics in Strategy.** Strategy calls `AnalyticsResult` only.
4. **Don’t size in Strategy.** `strength` → Risk.sizing does qty.
5. **Don’t talk to broker outside Execution.** Even “just to check price”.
6. **Don’t retry Order without idempotency_key.** Duplicate orders are a real-money bug.
7. **Don’t ignore `unknown` state.** Every unknown triggers reconciliation.
8. **Don’t let AI call execution.** Guard is in code, not just docs.
9. **Don’t skip `data_quality` filtering.** Gapped data → wrong RSI → wrong Signal → bad Order.
10. **Don’t add engine-to-engine imports.** Import contracts, not engines.

---

## 44. Appendices

### A. Versioning
This bible is **1.0**. Changes follow semver:
- **Patch** (1.0.1): Clarify wording, no structure change
- **Minor** (1.1.0): Add sub-module within existing engine (e.g., new analytics feature), backward compatible
- **Major** (2.0.0): New engine, contract break, dependency direction change — requires migration plan + all tests updated

### B. Glossary
- **Canonical Symbol:** Internal unified symbol (EURUSD). Broker-native is `frxEURUSD` (MT5).
- **Watermark:** Monotonic seq per symbol+broker, used for gap detection.
- **Shadow Mode:** Strategy runs but Signals are not dispatched.
- **Asymmetric Lock:** Allow exits, block entries for affected scope.
- **Provenance:** Chain of custody: MarketData watermark → AnalyticsResult IDs → Signal → RiskDecision → Order → ExecutionResult → ReconciliationReport.
- **Lake:** `backend/data/lake` parquets — replayable source for backtesting.

### C. Quick Reference: Where Does Logic Live?
```
"How volatile is EURUSD right now?"          -> analytics/per_asset/volatility.py
"Should we buy EURUSD now?"                  -> strategy/* -> Signal
"Is buying allowed / how much?"              -> risk/pre_trade + sizing
"Build the order object"                     -> order/order_builder.py
"Send it to Deriv/MT5"                       -> execution/broker_adapters/*
"Did broker really fill us?"                 -> reconciliation/broker/*
"What is my PnL now?"                        -> accounting/pnl.py
"What does the user see live?"              -> synchronization/event_publisher.py + frontend/realtime
"What does AI say about why we bought?"     -> intelligence/context_builder + factual_validator
```

### D. The One-Page Mental Model (Print This)
```
                    ┌─────────────────────┐
                    │     CORE SYSTEM     │  config, clock, lifecycle, health, logging
                    └──────────┬──────────┘
                               │
                    ┌──────────▼──────────┐
                    │      DATA ENGINE    │  ingestion→normalize→validate→quality→recover→store
                    └──────────┬──────────┘
                               ↓  MarketData
                    ┌─────────────────────┐
                    │   ANALYTICS ENGINE  │  per_asset / cross_asset / microstructure / portfolio / macro / event
                    └──────────┬──────────┘
                               ↓  AnalyticsResult
                    ┌─────────────────────┐
                    │  STRATEGY ENGINE    │  11 families, emit Signal(strength)
                    └──────────┬──────────┘
                               ↓  Signal
                    ┌─────────────────────┐
                    │    SIGNAL ENGINE    │  validate→dedup→freshness→conflict→queue
                    └──────────┬──────────┘
                               ↓  Validated Signal
                    ┌─────────────────────┐
                    │  PORTFOLIO ENGINE   │  positions, allocations, exposure state
                    └──────────┬──────────┘
                               ↓  PortfolioState
                    ┌─────────────────────┐
                    │     RISK ENGINE     │  pre_trade + sizing + limits + emergency
                    └──────────┬──────────┘
                               ↓  RiskDecision (approved? + qty)
                    ┌─────────────────────┐
                    │     ORDER ENGINE    │  build+validate+idempotency+state_machine
                    └──────────┬──────────┘
                               ↓  Order
                    ┌─────────────────────┐
                    │   EXECUTION ENGINE  │  routing→connectivity→broker_adapters→algorithms
                    └──────────┬──────────┘
                               ↓  ExecutionResult (fill)
                    ┌─────────────────────┐
                    │ RECONCILIATION ENG. │  broker vs internal truth, unknown_state_handler
                    └──────────┬──────────┘
                               ↓  ReconciliationReport
                    ┌─────────────────────┐
                    │  ACCOUNTING ENGINE  │  ledger + pnl + fees + financing
                    └─────────────────────┘

  Side:  BACKTESTING / SIMULATION / PAPER_TRADING / CANARY / RESEARCH / SYNCHRONIZATION
  Guard: INTELLIGENCE (read-only)  FRONTEND (via API+WS)  MONITORING  COMMAND
```

---

## 45. The Final Rule — The Bible Is Enforceable

This document is not aspirational. It is **CI-enforceable**:

1. **Structure:** `scripts/check_structure.py` verifies folder tree matches Section 3.
2. **Imports:** `import-linter` verifies Section 33 dependency rules.
3. **Contracts:** `contract_tests` verify Section 5 immutability.
4. **Coverage:** `pytest --cov` verifies Section 30 thresholds.
5. **Docs:** Every `engines/<engine>/README.md` must exist and have 9 required headings.

If any of the 5 fails, merge is blocked.

---


---

## Changelog — v1.0 → v1.1 (2026-09-21) — LOCKED 10-MODULE

- **Decision:** User directive `lock 10-module` — flatten 16 engines → 10 Isolatable Modules. Engine ≠ Module. Isolation proven via Shared Kernel + Airplane Test.
- **Section 3:** Rewritten canonical tree from `backend/engines/ + services/` flat list to `shared/ + modules/{data,analytics,alpha,portfolio_risk,execution,trust,validation,research,experience,intelligence,ops}`.
- **New doc:** `docs/architecture/isolated_modules.md` (229 lines) — Module Catalog (M1-M11), Ownership table, No Shared Mutable State, Airplane Test, Failure Isolation, Dependency Matrix, Physical Layout.
- **Merged atomic boundaries:** `Strategy+Signal → Alpha (M4)`, `Portfolio+Risk → M5`, `Order+Execution → M6`, `Reconciliation+Accounting → M7`, `Backtesting+Simulation+Paper+Canary → Validation (M8)`, `Synchronization+API+Command+Frontend → Experience (M10)`.
- **Enforcement:** `import-linter` now layers on `modules.*` (future tree), not `engines.*` flat. Dependency matrix in isolated_modules.md §5 is the new allow-list.
- **Tree:** Previous `financial_analyst_app/` scaffold deleted. Will be regenerated *after* v1.1 lock as `shared/ + modules/` tree (not `backend/engines/` flat).
- **Status:** All other sections (6-45) remain valid as *internal engine* specs — they are now subsections of their parent Module (e.g., Section 9 Strategy + Section 10 Signal are both inside M4 Alpha).

Each `modules/<name>/` has its own: `README.md`, `contracts` (re-exports), `src/`, `tests/`, `health`, `version`.

---

## 3b. Sub-Modules — True Isolation Inside Each Module (v1.3 — BEST APPROACH 57)

**Why:** A Module can be built in isolation (10 teams). But can 3 devs work on *Data* without blocking? Only if *Data*’s **sub-modules** are also isolatable.

**57 Sub-Modules across 19 Logical Systems → 12 Modules (v1.3: +M3.0 Capability +M4.5 Trade Plan + 3 Config Layers):**
*19 systems are logical grouping — 12 Modules are isolatable deploy units. See §3 and §24 tables.*

- **M1 Foundation (4):** M1.1 Contracts, M1.2 Core Runtime, M1.3 Config & Env, M1.4 Security & Isolation
- **M2 Data (8):** M2.1 Ingestion, M2.2 Normalization, M2.3 Validation, M2.4 Transformation, M2.5 OrderBook, M2.6 Data Quality, M2.7 Recovery, M2.8 Storage & Lake
- **M2b Reference (3):** M2b.1 Instrument & Symbol, M2b.2 Specs & Capabilities, M2b.3 Calendar & Actions
- **M3 Analytics (7):** M3.0 Capability Registry (NEW), M3.1 Feature Engine & Profiles, M3.2 Per-Asset (17 pure files, each file ‖), M3.3 Cross-Asset, M3.4 Microstructure, M3.5 Portfolio Analytics (read-only), M3.6 Macro & Event
- **M4 Alpha (5):** M4.1 Strategy Registry, M4.2 Strategy Families (11 families ‖ — Tier1 first 8), M4.3 Signal Validation, M4.4 Signal Orchestration, **M4.5 Trade Plan (NEW — entry/SL/TP/timing → TradePlan)**
- **M5 Portfolio & Risk (5):** M5.1 Portfolio State, M5.2 Allocation, M5.3 Pre-Trade Risk (margin_check ← THE FIX), M5.4 Sizing & Limits, M5.5 Risk Ops (post_trade + emergency)
- **M6 Execution (5):** M6.1 Order Core, M6.2 Routing, M6.3 Connectivity, M6.4 Broker Adapters (deriv ‖ mt5 ‖ headway), M6.5 Execution Intelligence (algos + costs)
- **M7 Trust (4):** M7.1 Reconciliation Brokers, M7.2 State Assurance (unknown_state_handler), M7.3 Ledger, M7.4 Financials (PnL/fees)
- **M8 Validation (4):** M8.1 Backtesting, M8.2 Simulation, M8.3 Paper Trading, M8.4 Canary
- **M9 Research (3):** M9.1 Datasets, M9.2 Features, M9.3 Experiments
- **M10 Experience (4):** M10.1 Synchronization, M10.2 API, M10.3 Command, M10.4 Frontend
- **M11 Intelligence (3):** M11.1 Context & Prompts, M11.2 Validation, M11.3 Guard & Audit
- **M12 Ops (3):** M12.1 Monitoring, M12.2 Testing Suites, M12.3 Deployment

**Blueprint now inline:** Every Module chapter (Sections 4-26) and every `modules/0X_M*.md` file now contains its Sub-Modules table **inline** — no separate `SUB_MODULES_BLUEPRINT.md` to cross-reference. Each Sub-Module owns its files, internal contract (frozen dataclass, not DB), and `pytest` with mocks — e.g., `M3.2 per_asset` 17 files are *embarrassingly parallel*: 3 devs each own 5 indicators.

**Phases rewritten to follow Modules and also inline per Module:** The old “Phase 0,1,2,3” (time-based, 3 phases) is now **57 Module-Following Phases** (`P01 M1.1 Contracts → P57 M12.3 Deployment` — v1.3 +M3.0 +M4.5). See `modules/PHASES.md` (master) AND each `modules/0X_M*.md` § Design Phases (inline per Module) — each phase = one Sub-Module DONE. Milestones: `v0.1-foundation → v0.2-data → v0.3-analytics → v0.4-alpha → v0.5-risk → v0.6-execution → v0.7-trust → v0.8-validation → v1.0`.

**Discussion:** Each `modules/0X_M*.md` now has a **§9 Sub-Modules** table from the blueprint. To refine, pick a Sub-Module (e.g., `M6.4 Broker Adapters — Deriv`) and propose `Change / Why / Impact / Internal contract? / External contract bump?`

## Changelog — v1.1 → v1.2 (2026-09-21) — 55 SUB-MODULES

- **Decision:** User directive `2nd can each module be divided into true sub modules that can be worked on in isolation` — now **57 Sub-Modules** across 13 groups (M1-M12) after v1.3 best-approach (+M3.0 +M4.5). Each Sub-Module is a true isolation unit (owns files + internal contract + tests + health propagation).
- **New files:** `modules/SUB_MODULES_BLUEPRINT.md` (55-row catalog, internal contracts, parallel lanes) + `modules/PHASES.md` (50+ phases rewritten to follow Modules, `P01 M1.1 → P55 M12.3`, with dependencies and tagging `v0.1-foundation → v1.0`).
- **Section 3b:** Added Sub-Modules overview (M1.1-M12.3 list) — this section is now the single source for “which sub-module lives where.”
- **Modules/ files:** Each `modules/0X_M*.md` now has §9 Sub-Modules table linking to blueprint — refine by Sub-Module without touching sibling.
- **Phases:** Old Phase 0-3 (3 phases) replaced by Module-following 55 phases. Build order follows isolation: `M1 (4) → M2/M2b (11) → M3 (6) → M4 (4) → M5 (5) → M6 (5) → M7 (4) → M8 (4) → parallel M9/M10/M11/M12`.

## Changelog — v1.2 → v1.3 (2026-09-21) — INSTITUTIONAL (99.8% automated, multi-user, broker-diff)

- **Multi-User Platform:** Added `tenant_context (user_id + broker_id)` to `M1.4 Security`. All `repositories` inject `WHERE user_id=:uid AND broker_id=:bid`; Lake partitioned `brokers/{deriv,mt5}/user_id=...`; `test_cross_tenant_query_blocked` gate. Deriv user sees ONLY Deriv streams/stored/processed + Deriv account management (`M5 PortfolioState` per tenant). MT5/headway similarly isolated. Same `Shared Kernel` + `M3-M6` serve all tenants.
- **High-Speed 99.8% Algo:** P95 budgets made explicit in Executive Summary (§1) and §4 Core Rules: Ingestion→Storage <50ms, Analytics <20ms, Risk <10ms, Order→Ack <500ms. Achieved via `M1.2` (`Redis` + `feature_engine` cache) + `M2` async + `M3` vectorized `numpy/pandas` + `M5` in-mem limits. 99.8% automated = only `emergency/kill_switch` + `unknown_state` require manual.
- **Multi-Broker, DBs Diff, Calc Shared:** Raw/normalized Lake remains **diff per broker per user**, but `M2.2 Normalization → canonical MarketData` + `M2b Reference` mapping (`frxEURUSD`→`EURUSD`) makes `M3 Analytics`, `M4 Alpha`, `M5 Risk`, `M6 Execution Intelligence` **write once, run for every broker** — no per-broker fork. `M7 Trust` per-tenant ledger remains diff.

## Changelog — v1.3 → v1.3 BEST APPROACH (2026-09-22) — TradePlan + Capability + Tier1 (no errors)

- **Best Approach per research (corrected):** Research treated as *useful info, not law*. Adopted `M3.0 Capability Registry` (pure lookup inside `M3`, not separate `data_capability` engine) with `FULLY_SUPPORTED / DERIVED / PROXY_LIMITED / UNAVAILABLE / REQUIRES_EXTERNAL` per `broker+tenant`; `M4.5 Trade Plan` (5 files `entry/stop/target/timing` → `TradePlan` contract inside `M4` atomic, not new Module); kept `M2.6 Data Quality` early (not `analytics/`) and `M5 Portfolio+Risk atomic` (no race) as locked.
- **M3:** `6 → 7` sub-modules (`M3.0` added). `M4:` `4 → 5` (`M4.5` added). `55 → 57` phases: `P15 M3.0`, `P16-P21` rest of M3, `P22-P26` M4 (P26 TradePlan), `P27-P31` M5 now validates `TradePlan` (checks `valid_until, SL/TP, RR, quality, spread, margin`), `P32-P57` shifted.
- **Strategy Families Tier 1:** Prioritized 8 `FULLY_SUPPORTED` price-only families for Deriv (no volume): `trend_following, momentum, mean_reversion, volatility_breakout, pullback, cross_sectional_momentum, statistical_arbitrage/spread, regime_based`. Tier 2 `tick_activity` = `PROXY_LIMITED ⚠️`, Tier 3 `order_book/vpin/queue` = `UNAVAILABLE → DISABLED` via `M3.0`. No guarantee of 99.8% win-rate — target `expectancy>0 after fees, Sharpe>Y, automation 99.8% uptime` (research §33-35 honoured).
- **Contracts:** New frozen `TradePlan v1.0` (`trade_plan_id, signal_id, entry, stop_loss, take_profit, timing, calculation_meta`) + `M3.0` capability types. `M5` now `RiskDecision(trade_plan_id)`.

- **Multi-User Platform:** Added `tenant_context (user_id + broker_id)` to `M1.4 Security`. All `repositories` inject `WHERE user_id=:uid AND broker_id=:bid`; Lake partitioned `brokers/{deriv,mt5}/user_id=...`; `test_cross_tenant_query_blocked` gate. Deriv user sees ONLY Deriv streams/stored/processed + Deriv account management (`M5 PortfolioState` per tenant). MT5/headway similarly isolated. Same `Shared Kernel` + `M3-M6` serve all tenants.
- **High-Speed 99.8% Algo:** P95 budgets made explicit in Executive Summary (§1) and §4 Core Rules: Ingestion→Storage <50ms, Analytics <20ms, Risk <10ms, Order→Ack <500ms. Achieved via `M1.2` (`Redis` + `feature_engine` cache) + `M2` async + `M3` vectorized `numpy/pandas` + `M5` in-mem limits. 99.8% automated = only `emergency/kill_switch` + `unknown_state` require manual.
- **Multi-Broker, DBs Diff, Calc Shared:** Raw/normalized Lake remains **diff per broker per user**, but `M2.2 Normalization → canonical MarketData` + `M2b Reference` mapping (`frxEURUSD`→`EURUSD`) makes `M3 Analytics`, `M4 Alpha`, `M5 Risk`, `M6 Execution Intelligence` **write once, run for every broker** — no per-broker fork. `M7 Trust` per-tenant ledger remains diff.

## Changelog — v1.3 → v1.4 (2026-09-22) — M2/M2b 18-POINT AUDIT (tighten before freeze)

- **Source:** User audit (18 points) on M2 DATA + M2b REFERENCE — design-level issues caught before implementation. Applied as **v1.4 CORRECTED [18-point audit]** to `modules/02_M2_Data.md`, `modules/02b_M2b_Reference.md`, `shared/contracts/market_data.py`, and master §§6-7.
- **1) Core relationship clarified:** `M2b = knows what market data means (identity/specs/calendar/capabilities)`, `M2 = obtains/processes/validates/recovers/stores observations` — documented in §2 and diagram `M2b → M2 → M3/M5/M8`.
- **2) M2b type fixed:** `Stateless registry/cache` → `Read-only reference-data service with versioned state and cache` — state = immutable snapshots (`REF-YYYY-MM-DD-NNN`), lookups are stateless from caller's perspective but module owns versioned state. Explains why historical backtest needs `effective_at`.
- **3) Canonical identity formalized:** `canonical_instrument_id = FX.EURUSD | COMMODITY.XAUUSD | COMMODITY.XAGUSD | BTCUSD` is primary key; `canonical_symbol` (`EURUSD`) and `display_name` (`Gold`) derived; `broker_native_symbol` (`frxEURUSD`, `frxXAUUSD`) never leaks. Mapping table added; storage partition is `instrument=FX.EURUSD`.
- **4) Ownership boundary formalized:** Table `M2b owns identity/symbol/specs/calendar/capabilities/corporate actions` vs `M2 owns tick/quote/trade/book/timestamp/source_sequence/watermark/quality/recovery/storage` — added as §2 in both modules.
- **5) Watermark terminology fixed:** `source_sequence` (broker-provided, may be None) vs `source_event_id` (tick-id-123) vs `watermark/processing_sequence` (M2's own monotonic per instrument+broker, M2.7's gap key) — M2b knows nothing about any. Updated M2.1 → M2.7 pseudocode.
- **6-7) Effective dating added:** `get_spec(instrument, broker, effective_at)` + `get_current_spec` convenience, `is_open(instrument, timestamp, broker)` evaluates calendar effective at timestamp, specs & calendars are versioned `WHERE effective_from <= :ts ORDER BY effective_from DESC LIMIT 1`. Historical changes (e.g., `min_qty 0.001 → 0.002` on 2026-06-01) no longer corrupt backtests.
- **8) Temporal Reference Events classified:** `corporate_actions` and `contract_rollover` are `Temporal Reference Events` (event + relationship) distinct from static/versioned specs — cleaner M2b internal model.
- **9) execution_profiles boundary:** M2b provides **reference facts only** (`commission`, `financing`, `slippage_params`); **M6 decides routing** (`grep M2b -r "route"` must be 0).
- **10) Capabilities confirmed:** `has_depth`, `order_types` remain M2b facts, used by both M2 (expect depth?) and M6 (validate order).
- **11) Cache wording fixed:** `per-tenant shared cache keyed by (symbol,broker)` → `Shared cache keyed by (instrument_id, broker_id) with no user duplication` — 1000 users share one EURUSD entry. Tenant auth is separate.
- **12) ReferenceService facade added:** Single stable `reference.*` entrypoint (`exists/to_canonical/to_native/get_spec/get_calendar/is_open/get_capabilities/get_execution_profile/get_corporate_actions`) delegating to 8 internal modules — stable boundary, mockable.
- **13) Sync vs async clarified:** Runtime lookups are **synchronous deterministic read-only** (`<1µs`, no await) — hot path `M2.2 per-tick`, `M5 per-risk <10ms`; only `load_snapshot/refresh` are `async I/O`.
- **14) Validation gate added:** `reference_validator` fails startup on `duplicate canonical`, `unknown instrument`, `missing tick_size`, `invalid session`, `duplicate effective_from` — with field name.
- **15) Invariants formalized:** `M2b-I01` Canonical Identity → `I08` Deterministic Lookup (table in §2, enforceable).
- **16) Dependency clarified:** `M2 ─depends on→ M2b` (M2 imports ReferenceService), `M2b never depends on M2` (no circular) — enforced by `import-linter`.
- **17) Lineage added:** `MarketData.reference_snapshot_id = REF-2026-09-22-014` stamped by M2.2, stored in parquet + catalog — enables `Why did frxXAUUSD normalize to COMMODITY.XAUUSD on 2026-03-15?` reproducibility for audit/replay/research/compliance. Propagated to `TradePlan` later.
- **18) Lake contradiction resolved:** Market-data Lake is **global shared** `market-data/provider=deriv/instrument=FX.EURUSD/...` (no user_id, saves terabytes); private per-tenant remains `private/broker=deriv/user_id=42/{signals,trade_plans,orders,ledger}` (`WHERE user_id+broker_id`). Early draft `brokers/deriv/user_id=42/symbol=EURUSD` for market-data removed.
- **Contracts updated:** `MarketData` now `instrument_id + reference_snapshot_id + source_sequence + source_event_id` (all optional with defaults for backward compat), `watermark` re-documented as processing seq. Future `AnalyticsResult`/`TradePlan` will propagate `reference_snapshot_id`.
- **Master patched:** `§6` and `§7` inner bodies repacked from `modules/02*.md v1.4`, master length 3590 → 3959 lines, verified markers `v1.4 CORRECTED`, `reference_snapshot_id`, `canonical_instrument_id`.

**End of Bible — v1.4 CORRECTED — 2026-09-22 — LOCKED (12-MODULE incl. M2b + 57 SUB-MODULES + 18-point M2/M2b audit + reference_snapshot_id)**

**End of Bible — v1.3 BEST APPROACH — 2026-09-22 — LOCKED (12-MODULE incl. M2b + 57 SUB-MODULES incl. M3.0/M4.5 + INSTITUTIONAL + TradePlan)**
**End of Bible — v1.0 — 2026-09-21**

> Build calmly, in order, with contracts. The system works when boundaries are respected.
> Start with Phase 0 → Phase 1 pipeline order. Do not skip.
