# M7 — TRUST (Reconciliation + Accounting (Lives on TradePlan→Order→Fill lineage) Merged) — EXPANDED DISCUSSION
### Establishes broker truth and what it means financially. Per-tenant ledger, broker-diff truth.

**Module Type:** Stateful — truth boundary, owns append-only ledger per `user_id+broker_id`.
**Owns State:** `reconciliation_reports` + `ledger` (append-only per tenant per broker) + `pnl`/`fees`
**Input:** `ExecutionResult` (M6 claim per-tenant) + broker truth (external snapshots per tenant)
**Output:** `ReconciliationReport(status: matched/mismatched)` → `LedgerEntry`/`PnL` (per-tenant)

> **Expanded for M7** — each file simple + pseudocode + connection. This patches into master §15-§16.

---

## 1. Purpose in One Sentence
> **M7 proves `internal positions == broker positions` per tenant per broker. If not, lock. Then records money only after proof.**


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution <500ms, tick→parquet <50ms, analytics <20ms, risk <10ms. No calc/file/abnormality tolerated — async I/O + sync pure calc + event_bus + frozen contracts + golden tests + well-commented WHY.*

## 2. The 4 Sub-Modules

| Sub | Name | Job | Touch When |
|-----|------|-----|------------|
| **M7.1** | Reconciliation — Brokers | Compare `account/order/position/fill` per broker per tenant | Add reconciler |
| **M7.2** | State Assurance | Diff, unknown handler, asymmetric_lock | Change lock policy |
| **M7.3** | Ledger | Append-only double-entry per tenant | Change ledger |
| **M7.4** | Financials | PnL (FIFO/LIFO), fees, valuation per tenant | Change PnL |

## 3. M7.1 — RECONCILIATION — BROKERS — The Comparers

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

## 4. M7.2 — STATE ASSURANCE — The Fixer & Locker

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

## 5. M7.3 — LEDGER — The Append-Only Truth

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

## 6. M7.4 — FINANCIALS — The Money Meaning

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

## 7. How M7 Connects to Whole System

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

## Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Sub-Module | Deliverable | Gate |
|-------|------------|-------------|------|
| **P37** | M7.1 Reconcilers | `reconciler_deriv, reconciler_mt5` per broker — checks `Order(trade_plan_id)` vs broker | `reconcile` |
| **P38** | M7.2 Comparator | `comparator, unknown_state, asymmetric_lock` per `user_id+broker_id` | `mismatch` → `M5` lock |
| **P39** | M7.3 Ledger | `ledger` (async append-only, per-tenant WHERE user_id+broker_id, checksum, retry, well-commented WHY immutable) — stores `trade_plan_id, order_id, fill_id` | `SELECT WHERE user_id+broker_id` |
| **P40** | M7.4 PnL | `pnl_calculator` FIFO, fees per tenant — `TradePlan` trail | `PnL(trade_plan_id)` |

**After M6 (P36):** Trust starts after `M6`. `M7.3` links `TradePlan → Order → Fill` for audit — best approach lineage `Execution → TradePlan → Signal → Features → Raw`.

## 8. Open Questions for M7

- [ ] Ledger FIFO vs LIFO per asset class — per broker spec?
- [ ] Auto-correct threshold: ever auto-correct ≤1% drift, or always manual?
- [ ] Reconciliation interval: 30s Deriv, 5m MT5 — per broker per tenant?
---
