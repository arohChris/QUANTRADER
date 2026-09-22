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

M2b prevents `if symbol=="frxXAUUSD"` scattered in code. One place knows `frxXAUUSD → COMMODITY.XAUUSD` (`display: Gold`), `tick_size=0.01` (identity fixture — authoritative value in P06 `contract_specs`), `deriv: market/limit supported, depth unavailable`, `trading hours 00:00-23:59`.

### Canonical Identity — The Most Important Correction

**Formal definitions (single source, no ambiguity):**

| Term | Example | Used by | Meaning |
|------|---------|---------|---------|
| `canonical_instrument_id` | `FX.EURUSD`, `COMMODITY.XAUUSD`, `COMMODITY.XAGUSD`, `CRYPTO.BTCUSD` | **All modules — primary key** | Unique, namespaced, immutable id. `FX.` = forex, `CRYPTO.` = crypto, `COMMODITY.` = commodity (bare `BTCUSD` is display/`canonical_symbol` only — REV1) |
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
reference.get_corporate_actions("CRYPTO.BTCUSD", effective_at=ts) -> [Action(split 4:1 @ 2024-08-31)]
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
- **Corporate action** → event (`AAPL split 4:1 @ 2024-08-31`)
- **Rollover** → event + relationship (`DE40 expiry 2026-09-20 → next contract`)
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
