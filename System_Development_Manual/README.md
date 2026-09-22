# System Development Manual — Modular Bible

**Master:** `System_Development_Manual.md` v1.3 (2,455 lines, 140 KB, LOCKED — 10-MODULE + 55 SUB-MODULES + INSTITUTIONAL) — **THE LAW**
**Working copies:** `modules/` — discuss / improve / add / remove **by Module *and* Sub-Module** in isolation, then patch back to master.

> **v1.3 — Institutional (99.8% automated, multi-user, broker-diff):** Multi-user tenant isolation (`user_id+broker_id`), high-speed budgets (<50ms ingest, <10ms risk), broker DBs remain diff per user per broker but `M3-M6` calc shared (no per-broker rewrite) — see `System_Development_Manual.md` Executive Summary + §4 + §36 + §M2/M5 details.
> **v1.2 — 55 Sub-Modules inline:** Each Module is split into 3-8 Sub-Modules **inside its own file** (no separate blueprint). Each Sub-Module can be worked on by a different person — `M3.2 Per-Asset` 17 indicator files are parallel, `M6.4 Broker Adapters` per-broker is parallel.
> **Phases rewritten inline:** 55 Module-following phases (`P01 M1.1 Contracts → P55 M12.3 Deployment`) — now **Section 37 in the master** AND **inside each `modules/0X_M*.md`** (§ Design Phases), not a separate `PHASES.md`.

> You asked: “structure based on our bible so we can discuss, improve add remove refine **by modules**” — this is it.

## How We Discuss — By Sub-Module (v1.2)

1. **Open `modules/00_README.md`** — Module map + workflow (47 lines).
2. Pick ONE Module you want to refine — e.g., `04_M4_Alpha.md` to add a strategy.
3. Propose changes **inside its §9 Open Questions** as: `Change / Why / Impact / Contract bump?`
4. When approved, I patch `System_Development_Manual.md` + bump version.

Each module file is **self-contained** — you can change `M5 Portfolio & Risk` without reading `M6 Execution`.

## Module Files (12, all from Bible Sections 4-45)

| # | File | Module | Lines | Source Sections |
|---|------|--------|-------|-----------------|
| 0 | `00_README.md` | Index + workflow | 47 | — |
| 1 | `01_M1_Foundation.md` | Foundation (4 sub: Contracts, Core, Config, Security) | 138 | §4, §5, §29, §39 | **M1.1-M1.4** |
| 2 | `02_M2_Data.md` | Data Platform | 95 | §6, §31, §35 |
| 2b| `02b_M2b_Reference.md` | Reference KB | 50 | §7 |
| 3 | `03_M3_Analytics.md` | Analytics | 77 | §8 |
| 4 | `04_M4_Alpha.md` | Alpha (Strategy+Signal) | 83 | §9+§10 |
| 5 | `05_M5_Portfolio_Risk.md` | Portfolio & Risk (atomic) | 85 | §11+§12 |
| 6 | `06_M6_Execution.md` | Execution (Order+Execution) | 79 | §13+§14 |
| 7 | `07_M7_Trust.md` | Trust (Reconciliation+Accounting) | 71 | §15+§16 |
| 8 | `08_M8_Validation.md` | Validation (Backtest+Sim+Paper+Canary) | 54 | §17-§20 |
| 9 | `09_M9_Research.md` | Research | 37 | §21 |
| 10| `10_M10_Experience.md` | Experience (Sync+API+Command+Frontend) | 60 | §22+§27+§28+§32 |
| 11| `11_M11_Intelligence.md` | Intelligence | 50 | §25 |
| 12| `12_M12_Ops.md` | Ops (Monitoring+Testing+Deploy) | 46 | §26+§30+§36+§37 |

Total working-copy size: ~76 KB — each Module <100 lines of *decision* (not prose) — built for discussion.

## Tree Now

```
System_Development_Manual/
├── README.md                      ← you are here
├── System_Development_Manual.md   ← master (LOCKED)
└── modules/
    ├── 00_README.md
    ├── 01_M1_Foundation.md
    ├── 02_M2_Data.md
    ├── 02b_M2b_Reference.md
    ├── 03_M3_Analytics.md
    ├── 04_M4_Alpha.md
    ├── 05_M5_Portfolio_Risk.md
    ├── 06_M6_Execution.md
    ├── 07_M7_Trust.md
    ├── 08_M8_Validation.md
    ├── 09_M9_Research.md
    ├── 10_M10_Experience.md
    ├── 11_M11_Intelligence.md
    └── 12_M12_Ops.md
```

No code. No scaffolded tree. Just the bible, split so we can refine **by Module → Sub-Module (inline)** — exactly as you wanted.

**v1.2 Patch (your request):**
- **Breaking within each Module, not separate:** `SUB_MODULES_BLUEPRINT.md` content is now **embedded inline** inside each `modules/0X_M*.md` (§ Sub-Modules) AND inside `System_Development_Manual.md` Sections 4-26 — no separate file to jump to.
- **Main manual writings now by Module with Sub-Modules:** Open master Section 9 (Strategy) and you see `M4.1-M4.4` right there; open `modules/04_M4_Alpha.md` and you see same — self-contained.

**v1.2 Inline (per your request):**
- **No separate `SUB_MODULES_BLUEPRINT.md` / `PHASES.md` to jump to** — every `modules/0X_M*.md` now has **§ Sub-Modules (inline table) + § Design Phases (inline Pxx slice)** from those blueprints.
- **Master `System_Development_Manual.md` also inline:** Sections 4-26 each embed its Sub-Modules (13 injections) + **Section 37 is now 55 Module-following phases** (`P01 M1.1 → P55 M12.3`, `v0.1-foundation`→`v1.0`).
- Open `04_M4_Alpha.md` and you see `M4.1-M4.4` *and* `P21-P24` right there — self-contained, no back-and-forth.
