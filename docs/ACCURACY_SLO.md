# Data Integrity & Accuracy SLO

> **Human directive 2026-09-27:** *"be very very professional — 99.999 accuracy is the
> minimum."*
>
> **This document exists because that number is meaningless until it has a denominator.**
> "99.999% accurate" is 1 error in 100,000 — accurate at *what*? Per tick ingested? Per order
> filled? Per ledger posting? Per PnL number shown? Each has a different verification burden
> and a different blast radius. Until each is defined and measured, the figure is decoration.
>
> **Current claim: NONE.** Per `AGENTS.md` §3.14, no accuracy or "institutional-grade" claim
> may be made without a re-runnable gate artifact. That artifact does not exist yet, and this
> file records exactly what is required to produce it.

---

## 1. The denominators

Accuracy is per-operation, not global. Each row is a separate SLO with its own evidence.

| # | Operation | Denominator | Target | Money at risk | Verified today |
|---|---|---|---|---|---|
| S1 | Candle OHLC from ticks | candles built | **99.999%** | wrong indicator input | partially — ordering fixed, 40 tests |
| S2 | Session/closure decision | gap classifications | **99.999%** | false gaps block trading; false closures hide loss | 5/5 broker agreement at one instant |
| S3 | Order fill price | fills | **99.999%** | direct capital loss | **NOT VERIFIED — M6 unbuilt** |
| S4 | Ledger posting | postings | **100%** (exact) | reconciliation breaks | **NOT VERIFIED — M7 unbuilt** |
| S5 | PnL | trades | **100%** (exact, Decimal) | wrong tax/withdrawal sizing | **NOT VERIFIED** |
| S6 | Risk decision | decisions | **100%** fail-closed | unbounded loss | **NOT VERIFIED — M5 unbuilt** |
| S7 | Instrument identity | symbol resolutions | **100%** | wrong asset traded | 67 tests, incl. phantom-key fix |
| S8 | Config validation | config loads | **100%** fail-closed | wrong session/tick/money | 20 failure-mode tests |

**Why S1–S2 are 99.999% and S4–S6 are 100%:** a data gap is recoverable and visible; a wrong
ledger posting or a risk decision that let an order through is not. Recovery is a backfill; a
mis-posted balance is a lie that survives until someone reconciles it.

## 2. What is actually measured today

| Metric | Value | How measured |
|---|---|---|
| Test pass | **1353 / 1353** | `pytest -q` — plain, **no exclusion and no `--ignore`** since D13 was fixed |
| Excluded by a registered blocker | **0** | D13 cleared; register empty; guard still fails on new breakage |
| Skips / xfail | **0 / 0** | §2 Step 6 requires zero |
| Flakiness | **0** — 5/5 identical runs, plus order-reversed | §3.5 forbids flaky tests |
| Suite collectability | **GUARDED** | `scripts/check_collection.py` — 4 failure modes, all tested |
| Line coverage | **89%** overall; **95%** on the new reference/gap modules | `pytest --cov` |
| Failure-mode coverage | 20 dedicated tests on uncovered branches | `test_m2b.5_failure_modes_spec.py` |
| Ruff (touched files) | clean | — |
| import-linter | 5 contracts kept, 0 broken | root `setup.cfg` |
| Hand-calc (money) | RSI `70.4641` exact; `Decimal("0.1")+Decimal("0.2")==Decimal("0.3")` exact | §3.1 |

**Coverage is not accuracy** (§3.5). 89% coverage says nothing about whether a fill is priced
correctly. It is listed because it is a hygiene floor, not evidence of correctness.

## 3. Defect ledger — every known defect, with severity

Severity: **S1** = money loss / silent wrong number · **S2** = blocks trading or data integrity
· **S3** = correctness risk, recoverable · **S4** = hygiene.

| # | Defect | Sev | Status |
|---|---|---|---|
| D1 | Metals appeared to trade all Saturday (`% 1440` double-count) — would backfill forever against a closure | **S1** | **FIXED** — 119h/wk, 30-combo brute-force cross-check, 0 mismatches |
| D2 | Reference master turned the `analytics:` config block into a phantom instrument | **S2** | **FIXED** — `asset_class` required, 3 RED-first tests |
| D3 | Sunday 21:00–24:00 forex reported shut (single midday probe) — hid 3h of real trading | **S1** | **FIXED** — two weekly anchors |
| D4 | `window()` session-length double-count (root of D1) | **S1** | **FIXED** — signed offset |
| D5 | Money fields were YAML floats on the money path (§3.1 violation) | **S1** | **FIXED** — quoted strings → `Decimal`; unquoted now rejected |
| D6 | `calendar.py` resolved class via module global — new assets invisible | **S2** | **FIXED** — injectable resolver |
| D7 | `coverage.py` had the same coupling (missed in D6's fix) | **S2** | **FIXED** — found by the 25-asset test |
| D8 | Gap classification was O(minutes) — 10-year window hung | **S2** | **FIXED** — O(days) |
| D9 | A broker error could be read as "closed" | **S1** | **FIXED** — `UNKNOWN` never `CLOSED`, tested |
| D10 | `to_canonical` returned `str \| None` (type defect, benign at runtime) | **S4** | **FIXED** |
| D11 | 2 vacuous tests that could never fail | **S3** | **FIXED** — replaced with round-trip + drift checks |
| D12 | 5 `raises(Exception)` sites passing on accidental `AttributeError` | **S3** | **FIXED** — all tightened to real types |
| **D13** | **Suite could not be COLLECTED — parallel writer's `tests/m2b/test_m2b.3_spec.py` imported missing `modules.reference.execution_profiles`** | **S2** | **FIXED 2026-09-27.** Module implemented; **43/43** M2b.3 tests pass; full suite **1353** with no exclusion, no `--ignore`, and no registered blockers. Guard + register remain in place and still FAIL on new breakage |
| **D14** | **M5/M6/M7 unbuilt: no fill pricing, no ledger, no risk decision, no PnL.** S3–S6 cannot be measured at all | **S1** | **OPEN — G0 not exited** |
| **D15** | **45 pre-existing ruff errors in files outside this slice** | **S4** | **OPEN — deliberately untouched** (not my slice; silent reformatting causes conflicts) |
| **D16** | **M2b.4 / M2b.5 / M2b.6 are at Steps 1–6: no human sign-off, no Step 7 real-data artifact** | **S2** | **OPEN — awaiting human** |
| **D17** | **`price_dp` in the standard is consumed by nothing** | **S4** | **OPEN — documented; deliberately no second rounding path yet** |
| **D18** | **Perf budgets in §4.1 never measured in prod-like conditions**; `session_defaults()` is resolved per `rule_for` call | **S3** | **OPEN — needs a benchmark** |

**Zero open S1 defects in code that exists. Every open S1 is missing code, not a bug in
existing code** — D14 is the whole problem, and it is a sequencing fact, not a quality one.

## 4. What must be true before any accuracy claim

1. ~~**D13 closed**~~ — **DONE 2026-09-27.** `modules/reference/execution_profiles.py` exists,
   43/43 of its tests run, the register entry is deleted, and the suite is whole: **1353,
   nothing excluded.**
2. **D14 closed** — M5 risk, M6 execution, M7 ledger built, with S3–S6 measured. Money cannot
   be accurate if the money path does not exist.
3. **Every SLO has a re-runnable artifact** — a script a third party runs, per §1 gate rule 3.
   "It worked on my machine" is not evidence.
4. **D16 closed** — human review of the three open slices.
5. **Adversarial + hand-calc on the money path** — §3.1 requires 2–3 explicit `Decimal`
   hand-calcs with a second independent implementation for high-risk formulas. Currently
   absent for fills, ledger and PnL because they are unbuilt.
6. **Kill -9 / restart recovery proven** — §3.9 requires state recovery exactly, tested.
7. **G0 exited with documented expectancy after fees on out-of-sample data.**

## 5. Non-negotiables that follow from the target

- **Unknown is never "fine".** `UNKNOWN → REJECT` on the money path; `UNKNOWN → candidate gap`
  on data. Never a silent default in either direction.
- **Exact types, never convenient ones.** `Decimal` from `str` in money; `Fraction` where
  rationals are exact. A `float` in the money path is a defect, not a style choice — and the
  linter that suggests otherwise is overridden with a written reason.
- **Every config value bounded at boot.** Absurd values stop the process; they do not "work".
- **Coverage is never proof.** Hand-calcs and adversarial tests are the proof.
- **A number without a denominator is not an SLO.** Every claim names its unit.

## 6. How this file stays honest

- A new defect is appended to §3 the day it is found, not when it is fixed.
- A defect is only marked FIXED with the test that fails without the fix.
- If a figure in §2 cannot be reproduced with the stated command, it is wrong and gets
  corrected — not quietly reworded.
