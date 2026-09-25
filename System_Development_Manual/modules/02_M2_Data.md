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
        symbol=instrument_id.split(".")[-1],  # display
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
| `watermark / processing_sequence` | **M2.2 owns & generates — M2.7 observes only [FROZEN v1.5 — BUILD_STATUS Discrepancy]** | `1001, 1002...` per `instrument_id+broker` | **Gap detection & recovery** (M2.7's key, disjoint runs only — never invents seqs), not M2b |
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

## 8. M2.7 — RECOVERY — Watermark is processing seq (M2's) — observes only [FROZEN v1.5]

**Allocator: M2.2 `WatermarkRegistry.next()` sole; M2.7 observes gaps + backfills only.**

**Pseudocode (clarified):**

```python
# M2.2 WatermarkRegistry — sole allocator — global per instrument+broker
def next_watermark(instrument_id: str, broker: str) -> int:  # sync — M2.2 only
    key = (instrument_id, broker)
    last = db.get(f"watermark:{key}") or 0  # global
    db.set(f"watermark:{key}", last+1)
    return last+1

# M2.7 observes only — never invents seqs
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

> **Synced to `System_Development_Manual v1.5` (2026-09-24):** `§6a` Temporal Resolver + `PIT Data Service as_of T` + `Dependency Graph` gate `M2` → `M3` → `M4`. `M2.7` frozen as disjoint observer only — `WatermarkRegistry.next()` sole allocator (`BUILD_STATUS` Discrepancy). Market-data global vs private lineage clarified — `M2.8` path has no `user_id`. Retention minimalism: keep `RAW+candle+Decision lineage`, recompute not per-tick cold.
