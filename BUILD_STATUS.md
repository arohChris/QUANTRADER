# BUILD_STATUS — QUANTRADER2 — financial_analyst_app

> **SINGLE SOURCE OF TRUTH — read before any work. Where this file and the manual disagree, this file wins.**
> Guardrails: `AGENTS.md` §6. Nothing is `complete` unless this file says so.

## Current Gate: G0 — Edge Validation (entry 2026-09-22)

## Last Updated: 2026-09-22  commit pending (repo initialized, 0 commits, user.name/email unset)  — M2b.1 + M2.1 human-review corrections applied to specs; M2b.1 still awaiting LGTM

## Sub-Modules

| Module | Sub | Spec | Contract | Tests (count) | Tests Pass | Hand-Calc | Real-Data Verify | Human Review | Status | Commit |
|--------|-----|------|----------|---------------|------------|-----------|------------------|--------------|--------|--------|
| M1 | M1.1 Contracts | ✓ `shared/contracts/*.py` 8 schemas | ✓ 1.0 frozen + snapshot | 42 | 42/42 | n/a | n/a | ✓ @you | complete | v0.1-foundation |
| M1 | M1.2 Core Runtime | ✓ `BaseEngine, Clock, Lifecycle` | n/a | 42 | 42/42 | n/a | n/a | ✓ @you | complete | v0.1-foundation |
| M1 | M1.3 Config & Env | ✓ `Settings, feature_flags SIGHUP` | n/a | 42 | 42/42 | n/a | n/a | ✓ @you | complete | v0.1-foundation |
| M1 | M1.4 Security & Isolation | ✓ `tenant_context, vault, permissions` | n/a | 42 | 42/42 | n/a | n/a | ✓ @you | complete | v0.1-foundation |
| M2 | M2.1 Ingestion | ✓ `docs/specs/m2.tick_ingest.md` (review corrections applied 2026-09-22: duplicate_tick naming, source_event_id dedup, event_id identity, M2.1→M2.2 boundary, output = internal tick/event \| Drop) | pending (M2.1-internal output type not yet frozen; Drop module missing) | 0 | — | — | — | — | spec | — |
| M2 | M2.2 Normalization | spec in `02_M2_Data.md` §4 | — | 0 | — | — | — | — | not started | — |
| M2b | M2b.1 Instrument & Symbol | ✓ `docs/specs/m2b.instrument_symbol.md` | ✓ `AssetClass` 1.0 frozen (additive); ReferenceService facade pending impl | 29 | 0/29 (no impl — red until LGTM+code) | n/a (no money) | — | ✗ awaiting LGTM | tests | — |
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

- **blocking:** no git commits yet — repo has `.git` but zero commits; `git config user.name` / `user.email` appear unset; all files untracked. Need human to set identity + approve first commit (see USER_ACTIONS.md Action 2).
- **non-blocking:** import-linter config needed `include_external_packages=True` (fixed 2026-09-22); `check_structure.py` cp1252 Unicode crash fixed with ASCII output (fixed 2026-09-22).
- **non-blocking:** `tests/m2_market_data/test_tick_ingest_spec.py` + `modules/m2_market_data/ingest/tick_ingest.py` encode pre-correction M2.1 contract (old imports `ErrorCode` from `base`, missing `shared.contracts.drop`, (symbol,ts,seq) dedup, emits `MarketData`) — intentional red until M2.1's own spec LGTM + rework to match corrected spec.
- M1 `42/42` green. New bugs go here as `blocking` vs `non-blocking` with issue link.

## Open Questions

- **M2b.1:** ~~Unknown `native` symbol passthrough vs strict raise~~ — **RESOLVED in review 2026-09-22:** passthrough retained but reworded as "unresolved native symbol" (not a resolved canonical ID); M2.2 detects/rejects/quarantines unresolved results; I02 scoped to explicitly mapped pairs only. Spec updated.
- **M2b.1 commodity:** Deriv/MT5 native symbols (`frxXAUUSD`, `XAUUSD`, `frxXAGUSD`) are fixtures pending broker-doc confirm at P06; `tick_size`/`contract_size` in commodities.yaml are identity fixtures only — authoritative specs are P06 `contract_specs`.
- **M3 profile loader:** manual `get_profile(asset_class)` loads `config/assets/{asset_class}.yaml` but commodity entries use `asset_class: commodity` while file is `commodities.yaml` — M3 loader needs a one-line map `commodity → commodities.yaml` (or rename file). Deferred to M3.1; not blocking M2b.1.
- M2.8 Lake global vs private resolved v1.4 (global market-data, private per-tenant) — locked.
- M1 5 open questions resolved v1.4 (MarketData.user_id, clock.sleep, feature_flags SIGHUP, composite tenant, vault Secret Manager) — locked.

## Discrepancies vs Manual

- Manual canonical list shows `FX. | SYNTHETIC. | INDEX. | EQUITY.` only — this spec adds `CRYPTO.` and `COMMODITY.` namespaces (crypto already existed in config; commodity added 2026-09-22 per user request). Flagged, not silently resolved: manual §canonical identity may need a v1.5 note.
- Manual folder tree lists `assets/{forex,crypto,commodities,indices,synthetic}.yaml` — project also has no `indices.yaml` yet (out of scope for M2b.1 defaults).
- None else — AGENTS.md v2.0 aligns with manual v1.4. Where they diverge, this file wins per AGENTS.md §6.
