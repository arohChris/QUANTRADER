# BUILD_STATUS — QUANTRADER2 — financial_analyst_app

> **SINGLE SOURCE OF TRUTH — read before any work. Where this file and the manual disagree, this file wins.**
> Guardrails: `AGENTS.md` §6. Nothing is `complete` unless this file says so.

## Current Gate: G0 — Edge Validation (entry 2026-09-22)

## Last Updated: 2026-09-22 — M2.3: spec LGTM+4 minors → Drop 1.5.0 applied → Step 3 tests written (`test_validate_spec.py` 20 cases). Suite excluding new file **179/179**; new file **collection-RED** (expected, no `validate.py` yet). **Awaiting human tests LGTM (Step 4)** before Step 5 `modules/m2_market_data/validate.py`.

## Sub-Modules

| Module | Sub | Spec | Contract | Tests (count) | Tests Pass | Hand-Calc | Real-Data Verify | Human Review | Status | Commit |
|--------|-----|------|----------|---------------|------------|-----------|------------------|--------------|--------|--------|
| M1 | M1.1 Contracts | ✓ `shared/contracts/*.py` 8 schemas | ✓ 1.0 frozen + snapshot | 42 | 42/42 | n/a | n/a | ✓ @you | complete | v0.1-foundation |
| M1 | M1.2 Core Runtime | ✓ `BaseEngine, Clock, Lifecycle` | n/a | 42 | 42/42 | n/a | n/a | ✓ @you | complete | v0.1-foundation |
| M1 | M1.3 Config & Env | ✓ `Settings, feature_flags SIGHUP` | n/a | 42 | 42/42 | n/a | n/a | ✓ @you | complete | v0.1-foundation |
| M1 | M1.4 Security & Isolation | ✓ `tenant_context, vault, permissions` | n/a | 42 | 42/42 | n/a | n/a | ✓ @you | complete | v0.1-foundation |
| M2 | M2.1 Ingestion | ✓ `docs/specs/m2.tick_ingest.md` **REV1** signed @you 2026-09-22 | ✓ `InternalTick` 1.1 + pkg 2.1.0 + `Drop` **1.4 frozen** (LGTM ACTION 10) | 50+contract | full suite **139/139** pre-M2.2-tests, 0 skipped | n/a (no money) | ✓ verify accepts=4 drops=6; human OK | ✓ LGTM @you (ACTION 10) | complete | afd90f9 |
| M2 | M2.2 Normalization | ✓ `docs/specs/m2.normalize.md` REV1 **LGTM @you (ACTION 9)** | Drop **1.4 frozen**; MarketData 1.4 unchanged | 40 (`test_normalize_spec.py`) | full suite **179/179**, 0 skipped | ✓ hand-calcs in test comments (HALF_UP/tick/identity) | ✓ `scripts/verify_normalize.py` on `data/sample_ticks_realistic.jsonl` — accepts=4 drops=0, identity_ok FX.EURUSD — human OK 2026-09-22 | ✓ tests LGTM ACTION 11 + impl LGTM @you Step 7/8 | complete | e75adac |
| M2 | M2.3 Validation | ✓ `docs/specs/m2.3_validation.spec.md` **LGTM +4 minors 2026-09-22** | ✓ Drop **1.5.0** frozen (human LGTM this review); MarketData 1.4 unchanged; contract lock test updated to 11 members | 20 (`test_validate_spec.py`) — written before code | collection-RED until Step 5 (expected); suite excluding new file **179/179** | n/a (comparisons only) | — | ✗ **awaiting tests LGTM (Step 4)** | tests | (pending commit) |
| M2b | M2b.1 Instrument & Symbol | ✓ `docs/specs/m2b.instrument_symbol.md` | ✓ `AssetClass` **2.0.0** (major scope cut) + `ReferenceError` hierarchy 1.2.0 — pkg **2.1.0** | 36 (post-review F2/F4/F5/F7) | m2b file 36/36; full suite **139/139**, 0 skipped | n/a (no money) | ✓ fixtures only (`config/assets/*.yaml` + human OK 2026-09-22) — **no broker-tick replay log** (F9: downgraded vs M2.1 bar) | ✓ tests LGTM + verify OK @you; review F1–F11 applied | complete (review minors applied 2026-09-22; F6 deferred P06) | a6908c2 |
| M3 | M3.1 RSI | — | — | 0 | — | — | — | — | not started | — |
| M4 | M4.1 <strategy> | — | — | 0 | — | — | — | — | not started | — |
| M5 | M5.1 Risk | — | — | 0 | — | — | — | — | not started | — |
| M6 | M6.1 Execution | — | — | 0 | — | — | — | — | not started | — |
| M7 | M7 Ledger | — | — | 0 | — | — | — | — | not started | — |
| M8 | M8 Backtest | — | — | 0 | — | — | — | — | not started | — |

*Status values only:* `not started` | `spec` | `contract` | `tests` | `tests approved` | `implemented` | `implemented, not verified` | `complete`

## Gates

- [ ] **G0 Edge Validation:** `scripts/gate0_walkforward.py` + expectancy-after-fees doc on out-of-sample data — NOT STARTED (needs M2b+M2+M3+M4+M8 minimal slice)
- [ ] **G1 Minimal Paper Loop:** 5+ trades signal→risk→fill→ledger PnL == hand calc — NOT STARTED
- [ ] **G2 Second Strategy + Full Ledger:** 2 strategies concurrent, no risk corruption — NOT STARTED
- [ ] **G3+ Everything else:** one slice at a time — NOT STARTED

## Known Bugs

- ~~**blocking:** no git commits yet~~ — **RESOLVED 2026-09-22:** identity set (Christian); commits `efb59ff` + `a6908c2` (M2b.1 complete).
- **non-blocking:** import-linter config needed `include_external_packages=True` (fixed 2026-09-22); `check_structure.py` cp1252 Unicode crash fixed with ASCII output (fixed 2026-09-22).
- ~~**non-blocking:** `tests/m2_market_data/test_tick_ingest_spec.py` disabled (pre-correction contract)~~ — **RESOLVED 2026-09-22:** rewritten against corrected `docs/specs/m2.tick_ingest.md` (InternalTick+Drop contracts, fingerprint dedup, no MarketData emit). Suite is intentionally collection-RED until Step 5 `TickIngestor` impl (only after human LGTM on tests).
- ~~**non-blocking:** M2.1 REV1 (Deriv optional id/bid/ask etc.) applied to spec + Drop 1.4 code 2026-09-22 — existing M2.1 tests/impl may need a small sync pass once ACTION 10 LGTM lands; re-run full suite then. Drop 1.4 enum change needs human LGTM per AGENTS §5 before treated as frozen.~~ — **SYNCED 2026-09-22:** `tick_ingest.py` + tests rewritten to REV1; suite **133/133 green, 0 skipped**; verify re-run OK. Still awaiting human LGTM to freeze Drop 1.4 + sign ACTION 10 (AGENTS §5).
- ~~**expected (not a bug):** full suite is RED while M2b.1 Step 5 impl is unwritten~~ — **RESOLVED 2026-09-22:** importorskip removed, impl written; suite was **139/139** then **179/179 after M2.2** (do not quote historical 76/76 as current — F10).
- ~~**expected (not a bug):** suite collection-RED until M2.2 Step 5 `normalize.py`~~ — **RESOLVED 2026-09-22:** impl + watermark written; full suite **179/179 green, 0 skipped**; verify `identity_ok=True`.
- **F6 deferred → P06 entry criteria (non-blocking for M2b.1):** `reference.py` silently continues on missing YAML / malformed `broker_native` / duplicate native last-wins / no `AssetClass` validation at load. Spec defers to P06 `reference_validator` (`FAIL STARTUP`); **P06 must land the load-time validator before `tick_size` becomes authoritative.**
- **F9 verify honesty:** M2b.1 Real-Data cell is **fixtures only** — no `scripts/verify_*` broker-tick replay. Fine for pure mapping (no money); do not treat as M2.1-class evidence.
- M1 `42/42` green. New bugs go here as `blocking` vs `non-blocking` with issue link.

## Open Questions

- **M2b.1:** ~~Unknown `native` symbol passthrough vs strict raise~~ — **RESOLVED in review 2026-09-22:** passthrough retained but reworded as "unresolved native symbol" (not a resolved canonical ID); M2.2 detects/rejects/quarantines unresolved results; I02 scoped to explicitly mapped pairs only. Spec updated.
- **M2b.1 commodity:** Deriv/MT5 native symbols (`frxXAUUSD`, `XAUUSD`, `frxXAGUSD`) are fixtures pending broker-doc confirm at P06; `tick_size`/`contract_size` in commodities.yaml are identity fixtures only — authoritative specs are P06 `contract_specs`.
- **M3 profile loader:** manual `get_profile(asset_class)` loads `config/assets/{asset_class}.yaml` but commodity entries use `asset_class: commodity` while file is `commodities.yaml` — M3 loader needs a one-line map `commodity → commodities.yaml` (or rename file). Deferred to M3.1; not blocking M2b.1.
- M2.8 Lake global vs private resolved v1.4 (global market-data, private per-tenant) — locked.
- M1 5 open questions resolved v1.4 (MarketData.user_id, clock.sleep, feature_flags SIGHUP, composite tenant, vault Secret Manager) — locked.

## Discrepancies vs Manual

- ~~Manual still lists `SYNTHETIC./INDEX./EQUITY.` namespaces and `indices/synthetic.yaml` tree~~ — **RESOLVED 2026-09-22:** full manual cleanup applied (`System_Development_Manual.md` + modules/01,02,02b,03,04,06); canonical list + folder tree now **forex | crypto | commodity only**.
- ~~Bible 02b examples used bare `BTCUSD` as canonical id / `tick_size=0.001` for XAUUSD~~ — **RESOLVED 2026-09-22 (F11):** examples updated to `CRYPTO.BTCUSD` + `tick_size=0.01` fixture note (code already followed spec; bible was stale).
- None else — AGENTS.md v2.0 aligns with manual after scope cleanup. Where they diverge, this file wins per AGENTS.md §6.
