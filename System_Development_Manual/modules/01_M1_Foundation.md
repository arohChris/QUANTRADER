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
No hard-coded `if symbol=="R_75"` or `rsi_period=14` or `max_exposure=0.1` in code. All numbers live in `config/*.yaml`, loaded once, validated at startup. Change YAML, not code.

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
