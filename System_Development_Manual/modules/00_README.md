# System Development Manual — Modular Bible — How to Discuss & Refine by Module

**Master:** `System_Development_Manual.md` (v1.1, 2,067 lines — THE LAW)
**This folder:** `modules/` — The *discussable* split. Each file = one isolatable Module. Edit ONE file to improve ONE Module without touching the rest.

> **Workflow:** Propose change in `modules/0X_<module>.md` → review diff → if approved, patch the change back into `System_Development_Manual.md` + bump version. Module files are the *working copies*; the master is the *locked source*.

## Storage — 3 Boxes (Simple)

`HOT` Redis+Timescale (now) → `WARM` Timescale+Parquet (0-90d) → `COLD` S3 (90d-7y) — robot moves by age. Market data global shared, your account data per-tenant `user_id+broker_id`.

## Module Map — 12 Isolatable Units (Each With Inline Sub-Modules — No Separate Blueprint)

| File | Module | What It Owns | Discuss If You Want To... |
|------|--------|--------------|---------------------------|
| `01_M1_Foundation.md` | **M1 FOUNDATION** | `shared/core` + `shared/contracts` (Shared Kernel) + `config` | Change clocks, health, broker isolation, contract versioning |
| `02_M2_Data.md` | **M2 DATA** | Market Data pipeline (ingestion → storage) | Add broker feed, change candle building, gap recovery, lake partitioning |
| `02b_M2b_Reference.md` | **M2b REFERENCE** | Instrument master, symbol mapping, contract specs | Add instrument, change lot sizes, trading calendar |
| `03_M3_Analytics.md` | **M3 ANALYTICS** | Feature computation (per_asset / cross_asset / microstructure / portfolio / macro / event) | Add indicator, change profile params, add regime classifier |
| `04_M4_Alpha.md` | **M4 ALPHA** | Strategy (11 families) + Signal (validator/dedup/freshness) | Add strategy, change signal freshness, shadow mode |
| `05_M5_Portfolio_Risk.md` | **M5 PORTFOLIO & RISK** | Portfolio state + Risk (pre_trade, sizing, limits, emergency) | Change margin calc, sizing, kill-switch, exposure limits |
| `06_M6_Execution.md` | **M6 EXECUTION** | Order (builder/state_machine/idempotency) + Execution (routing/connect/adapters/algos) | Add order type, routing rule, execution algo (TWAP) |
| `07_M7_Trust.md` | **M7 TRUST** | Reconciliation + Accounting (ledger/pnl) | Change reconciliation policy, ledger, PnL attribution |
| `08_M8_Validation.md` | **M8 VALIDATION** | Backtesting + Simulation + Paper + Canary | Change fill model, stress shock, canary threshold |
| `09_M9_Research.md` | **M9 RESEARCH** | Datasets + Features + Experiments | Add dataset, feature store |
| `10_M10_Experience.md` | **M10 EXPERIENCE** | Synchronization + API + Command + Frontend | Add endpoint, WS topic, command, UI feature |
| `11_M11_Intelligence.md` | **M11 INTELLIGENCE** | AI narrator (context_builder, tool_policy) | Change prompts, allowlist, factual validator |
| `12_M12_Ops.md` | **M12 OPS** | Monitoring + Testing Suites + Deployment | Add alert, runbook, test harness |

## How to Discuss — Template per Module File

Each `0X_*.md` has the same 9 headings so any Module can be refined in isolation:

1. Purpose & Ownership
2. Inputs → Outputs (Contracts)
3. Internal Engines / Components (not exposed)
4. Dependencies (only via contracts)
5. Configuration (YAML keys)
6. Failure Modes (from Section 35)
7. Build Independence (Airplane Test)
8. Tests & Definition of Done
9. Open Questions / Proposed Changes  ← EDIT HERE to propose refine/add/remove

## Status

- **v1.1 LOCKED (10-module)** — Module boundaries in `System_Development_Manual.md` Section 3 + `docs/architecture/isolated_modules.md` (archived inside bible).
- All `modules/0X_*.md` in this folder are **editable working copies** derived from bible Sections 4-35. Editing them does NOT auto-update the master — patch back after review.

Start with the Module you care about most. Open its file. Propose changes in §9.

---

---

## v1.2 Change — Inline Sub-Modules (per your request)

**Before v1.2:** `SUB_MODULES_BLUEPRINT.md` was a *separate* 55-row blueprint + `PHASES.md` was separate. You had to go back-and-forth.

**Now v1.2:** Every `01_M1_*.md` … `12_M12_*.md` **contains its Sub-Modules table AND its Design Phases inline** (§ Sub-Modules + § Design Phases). The master `System_Development_Manual.md` also embeds each Module’s Sub-Modules right inside Sections 4-26.

> **Result:** Open `04_M4_Alpha.md` and you see `M4.1 Strategy Registry`, `M4.2 11 Families`, `M4.3 Validation`, `M4.4 Orchestration` *right there*, plus `P21-P24` phases — no cross-reference. Same in the master bible.



> **v1.3 Institutional:** `user_id+broker_id` tenant isolation + high-speed budgets + broker-diff lake but shared `M3-M6` calc — see master Executive Summary. No per-broker rewrite.
