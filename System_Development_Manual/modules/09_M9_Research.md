# M9 — RESEARCH (Isolated Lab) — EXPANDED DISCUSSION
### Experiment without polluting production. Tenant-scoped datasets, shared lab engine.

**Module Type:** Batch + stateful. Never imports Execution.
**Owns State:** `datasets` (versioned per broker per tenant), `feature_store`, `experiments`
**Input:** `Data Lake` (per broker per tenant) + `AnalyticsResult` (read) + `config` snapshots
**Output:** `ResearchReport` / `DatasetVersion`

> **Expanded for M9** — each file simple + pseudocode + connection. This patches into master §21.

---

## 1. Purpose in One Sentence
> **M9 is the lab where you try `rsi_period 14→12` on 2024-01→03 data without touching live `M4`.**


> **INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented**
> *Execution <500ms, tick→parquet <50ms, analytics <20ms, risk <10ms. No calc/file/abnormality tolerated — async I/O + sync pure calc + event_bus + frozen contracts + golden tests + well-commented WHY.*

## 2. The 3 Sub-Modules

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

## Design Phases — Module-Following (Inline, from Master §37)

> **Phases for this Module only** — each phase = one Sub-Module DONE. Inline so you never open a separate PHASES.md.

| Phase | Module | Sub-Module | Gate |
|-------|--------|------------|------|
| **P45** | M9.1 | Datasets — `dataset_manager` (replays `MarketData → TradePlan` lineage) | `dataset` versioned |
| **P46** | M9.2 | Features — `feature_registry` (reads `M3.0` capability) | `feature` versioned |
| **P47** | M9.3 | Experiments — `experiment_runner` (never imports `M6` execution, uses `TradePlan`) | `experiment` |

**Parallel after M2+M3 (incl. M3.0):** Research reuses `TradePlan` path for `M8` replay — respects `M3.0` gates.

## 3. Open Questions for M9

- [ ] Feature store now (Feast) or experiment_runner first?
- [ ] Should `M9` be separate deployable or merged with `M8` lab?
---
