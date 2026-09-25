# USER_ACTIONS — WHAT YOU (HUMAN) MUST DO
# ALL CAPS. UPDATED CONSTANTLY. READ THIS FIRST.

## CURRENT STATUS (UPDATED 2026-09-24)
# GATE: G0 ENTRY
# M1 COMPLETE | M2b.1 COMPLETE | M2.1 COMPLETE | M2.6/2.7/2.8 COMPLETE
# M1.3 2-GATE COMPLETE | Frontend design FROZEN A16F
# ACTION 27 DONE — LGTM on M3.0 Capability spec (conditional: AGENTS.md critical)
# ACTION 28 DONE — LGTM on CI guard scripts
# ACTION 29 OPEN — LGTM on M3.0 TESTS (Step 4) — builder STOPPED here
# ACTION 24 (M10.1) still PARKED — backend first per you.

==================================================
ACTION 29 — LGTM ON M3.0 TESTS (Step 4) — OPEN
==================================================
# STATUS: [OPEN] 2026-09-24 — Step 3 tests written, NOT approved yet.
#   FILE: financial_analyst_app/tests/m3_analytics/test_m3.0_spec.py
#   24 tests = 20 spec §Test plan names exact + 4 edge extras (empty_requires,
#   override_requires_no_status, missing_version_or_features, malformed_yaml)
#   — AGENTS.md §2 Step 3: one test per stated edge in §Edges.
#   Written against docs/specs/m3.0_capability.spec.md only.
#   Collection RED until Step 5 (no modules/m3_analytics yet) — honest, expected.
#   Gate test name frozen: test_order_book_unavailable_on_deriv
#   Reply: "LGTM on tests for M3.0" (or request changes)
#   After LGTM: Builder may write implementation (§12 Critical Thinking Gate first).

==================================================
ACTION 28 — LGTM ON CI GUARD SCRIPTS + tests/ci — DONE
==================================================
# STATUS: [DONE] 2026-09-24 — human "all lgtm if agents.md is followed critically".
#   Scripts (financial_analyst_app/scripts/):
#     check_pit_guard.py   — no alpha lake bypass (exit 0 pre-P22)
#     check_retention.py   — no AnalyticsResult cold parquet
#     check_build_order.py — G0 before M6; M3.0 before M4.1
#     check_naming.py      — mX.Y specs / test_mX.Y_spec / verify_mX.Y
#   Tests: financial_analyst_app/tests/ci/ (31 passed)
#   Live: all four [OK] on current tree

==================================================
ACTION 27 — LGTM ON M3.0 CAPABILITY SPEC (P15) — DONE
==================================================
# STATUS: [DONE] 2026-09-24 — human "all lgtm if agents.md is followed critically"
#   → conditional LGTM on spec. Condition: AGENTS.md §0–§13 followed critically
#   (serial steps, hard stop at ACTION 29 for tests, §12 before impl).
#   SPEC: docs/specs/m3.0_capability.spec.md (DRAFT → LGTM'd)
#   Gate test name frozen: test_order_book_unavailable_on_deriv
#   Blocks: M3.1 Feature Engine, M3.2 RSI, M4.1 (build order P15 first)
#   Day-1: broker lookup only; user_id accepted but ignored; no DB/I/O in get()

==================================================
ACTION 26 — IMPL LGTM ON M2.8 STORAGE — DONE
==================================================
# STATUS: [DONE] 2026-09-23 — human LGTM with 3 fixes → all applied + suite green:
#   1) watermark rejects bool (True/False not int>=1)
#   2) catalog.jsonl rewrite atomic (tmp → os.replace) — still no parquet+catalog 2PC
#   3) all partitions validated (_entry_from_rows) before first filesystem write
#   + @needs_parquet on test_user_id_not_in_created_path (pyarrow path).
#   pytest 25/25 · full suite 386/386 · structure OK · verify EXIT 0.
#   BUILD_STATUS M2.8 → complete.

==================================================
ACTION 25 — LGTM ON M2.8 TESTS — DONE
==================================================
# STATUS: [DONE] 2026-09-23 — human LGTM + 2 improvements applied:
#   1) explicit rows_in_write_order assert in checksum test
#   2) multi-row same-partition P14 test (3 rows → 1 file)
#   partition_key freeze already exact in REV1.1 spec.
#   user_id tree walk fixed to relative paths (test name false positive).

==================================================
ACTION 24 — LGTM ON M10.1 SYNCHRONIZATION SPEC (PARKED)
==================================================
# STATUS: [OPEN / PARKED] — human “2.8 first”. SPEC: docs/specs/m10.1_synchronization.spec.md

==================================================
ACTION 23 — LGTM ON M2.8 STORAGE SPEC — DONE
==================================================
# STATUS: [DONE] 2026-09-23 — human LGTM + 5 freezes + 2 nits → applied REV1.1.
#   Partition identity, no parquet+catalog 2PC, rows_in_write_order, order_book
#   ValueError, JSONL one entry/key, read_partition signature, path segment rules.

## DEMO vs LIVE — HOW TO SWITCH (human OR auto-bot) — UPDATED 2026-09-23
# FILE: financial_analyst_app/config/accounts.yaml
#   mode: demo | live   under each broker (default_mode: demo while G0/G1)
#   live.enabled: false → must set true to ARM real-money (fail loud otherwise)
# ENV override (no file edit):  ACCOUNT_MODE=demo  or  ACCOUNT_MODE=live
# PLACEHOLDERS for future insert (human or bot) — NAMES only, never secret values:
#   .env (copy .env.example → .env, never commit .env):
#     DERIV_DEMO_ACCOUNT_ID / DERIV_DEMO_API_TOKEN
#     DERIV_LIVE_ACCOUNT_ID / DERIV_LIVE_API_TOKEN   ← fill only when going live
#     MT5_* / HEADWAY_* same pattern
#   OR vault (M6 private): vault.store(user_id, broker, token) — api_token_vault_key
# Market-data (M2.1) needs NO account token — only platform app_id in brokers/deriv.yaml.
#
# FRONTEND (M10.4) — PARKED at PROJECT ROOT, built later per you 2026-09-23 ("backend first"):
#   QUANTRADER2/frontend/                          — own folder at root, M10.4 layout, zero backend code
#   financial_analyst_app/tests/m10_experience/     — frontend-only tests
#   NOT inside financial_analyst_app. Backend never serves HTML.
# API JSON Day-1: GET/POST http://127.0.0.1:8000/api/v1/account/mode (will gain auto field + /auto-trade after impl)

## WHERE CONFIG FILES LIVE
# Root: financial_analyst_app/config/
#   accounts.yaml               — DEMO vs LIVE mode + env/vault placeholder NAMES
#   brokers/deriv.yaml          — Deriv ws_url + platform app_id (M2.1 connection)
#   brokers/mt5.yaml, headway.yaml
#   environments/paper.yaml     — DEMO / fake-money overlay (use this for demo)
#   environments/{development,testing,canary,production}.yaml
#   assets/{forex,crypto,commodities}.yaml — instrument master (M2b.1)
#   risk/limits.yaml, system/system.yaml, system/feature_flags.yaml
#   strategies/trend_following.yaml, execution/routing.yaml
# Repo root: .env.example — copy to .env and fill placeholders (never commit .env)
# Secrets: never in YAML — env (.env) or vault DB (private tokens).

==================================================
ACTION 13 — DONE: LGTM ON M1.3 2-GATE SPEC (MODE + AUTO)
==================================================
# STATUS: [DONE] 2026-09-23 — "lgtm" → Step 1 closed.
#   Spec frozen: docs/specs/m1.3_account_mode_two_gate.spec.md
#   Builder proceeds to Step 3 tests only (no impl until ACTION 14).

==================================================
ACTION 14 — DONE: LGTM ON M1.3 2-GATE TESTS (Step 4)
==================================================
# STATUS: [DONE] 2026-09-23 — "lgtm" → Step 4 closed → builder Step 5 impl.
#   tests/test_account_mode_two_gate.py (30) frozen as review baseline.

==================================================
ACTION 15 — DONE: SMOKE + IMPL LGTM (M1.3 2-GATE)
==================================================
# STATUS: [DONE] 2026-09-23 — "lgtm" → M1.3 2-gate marked complete in BUILD_STATUS.
#   Spec A13 + tests A14 + impl A15 all closed.

==================================================
ACTION 16 — DONE: LGTM ON M2.6 DATA QUALITY SPEC
==================================================
# STATUS: [DONE] 2026-09-23 — "LGTM on spec for M2.6" + 4 minors applied:
#   (1) store unified (per-instance seen+prev_map; optional dedup_store inject)
#   (2) paths locked financial_analyst_app/modules|tests/m2_market_data/...
#   (3) provider = md.provider or md.broker in fingerprint key
#   (4) prev_map broker = md.broker or md.provider fallback
#   Spec: docs/specs/m2.6_data_quality.spec.md — ready for tests.

==================================================
ACTION 17 — DONE: LGTM ON M2.6 TESTS (19)
==================================================
# STATUS: [DONE] 2026-09-23 — "lgtm" → Step 5 quality.py written.

==================================================
ACTION 18 — DONE: LGTM ON M2.6 IMPL (quality.py)
==================================================
# STATUS: [DONE] 2026-09-23 — "lgtm" → suite + verify green → BUILD_STATUS complete.
#   Module: financial_analyst_app/modules/m2_market_data/quality.py
#   Tests: 19/19 | verify: scripts/verify_m2.6_quality.py

==================================================
ACTION 19 — DONE: LGTM ON M2.7 RECOVERY SPEC
==================================================
# STATUS: [DONE] 2026-09-23 — "lgtm" → tests written; recovery.py NOT yet.

==================================================
ACTION 20 — DONE: LGTM ON M2.7 TESTS (19)
==================================================
# STATUS: [DONE] 2026-09-23 — "LGTM on tests" → Step 5 recovery.py written.

==================================================
ACTION 21 — DONE then SUPERSEDED: initial impl LGTM then REVOKED
==================================================
# STATUS: [SUPERSEDED] 2026-09-23 — human review: architecture OK, correctness
#   blocked on to_interval collapsing disjoint gaps. REV2 applied.

==================================================
ACTION 22 — DONE: LGTM ON M2.7 REV2.1 (disjoint-gap fix)
==================================================
# STATUS: [DONE] 2026-09-23 — human "lgtm" → BUILD_STATUS M2.7 complete.
#   REV2.1 hardening: §9 three-run + run-boundary, §7 multi-interval (28 tests).
#   Suite 353/353; verify disjoint + three-run OK; structure OK.
#   Ownership frozen: M2.2 sole allocator; M2.7 observe-only.

==================================================
ACTION 16F — LGTM ON FRONTEND DESIGN SPEC (non-blocking design lock)
==================================================
# STATUS: [OPEN] 2026-09-23 — design bible only; does NOT start M10.4 build.
#   SPEC: docs/frontend_spec.md
#   Captures: cockpit shell, Cmd+K center + right audit, chart Lake proof,
#   TradePlan rec cards, terminal AI/news, MODE+AUTO breaker panel (maps A13
#   two-gate), GitOps config, 5 crisp rules, API touchpoints for account/status.
#   Reply "lgtm frontend spec" → freeze file. M10.4 build still deferred
#   until human opens it after current backend slice (ACTION 20+) clears.

==================================================
ACTION 12 — DONE: HUMAN TEST M2.1 CONNECTION (P08)
==================================================
# STATUS: [DONE] 2026-09-23 — "tested and all parts are working" → BUILD_STATUS real-human
#   verify updated; M2.1 Connection stays complete. No further config action for demo.

==================================================
ACTION 11 — DONE: LGTM ON M2.2 NORMALIZE TESTS (Step 4 gate)
==================================================
# STATUS: [DONE] 2026-09-22 — superseded path; M2.2 complete @ e75adac (ACTION history).

==================================================
ACTION 10 — DONE: LGTM ON M2.1 TICK-INGEST REV1 + Drop 1.4
==================================================
# STATUS: [DONE] 2026-09-22 — "lgtm" → Drop 1.4 frozen, M2.1 REV1 signed, suite 139/139.

==================================================
ACTION 9 — DONE: LGTM ON M2.2 SPEC + DROP 1.4 CONTRACT
==================================================
# STATUS: [DONE] 2026-09-22 — "lgtm" → Step 1+2 closed → Step 3 tests written.

==================================================
ACTION 8 — DONE: EYEBALL M2.1 VERIFY (3 real Deriv assets)
==================================================
# STATUS: [DONE] 2026-09-22 — you replied "ok" → BUILD_STATUS M2.1 = complete.
# Output confirmed: accepts=4 drops=6; frxEURUSD/cryBTCUSD/frxXAUUSD bids/asks/quotes/pips correct.

==================================================
ACTION 7 — DONE: LGTM ON M2.1 TESTS (Deriv WS format)
==================================================
# STATUS: [DONE] 2026-09-22 — "lgtm" → Step 4 closed → TickIngestor rewritten for Deriv frames.

==================================================
ACTION 6 — DONE: EYEBALL M2.1 (superseded — wrong sample format)
==================================================
# STATUS: [DONE/CANCELLED] 2026-09-22 — sample was incorrect; replaced by ACTION 7.

==================================================
ACTION 5 — DONE: LGTM ON M2.1 TICK INGEST TESTS
==================================================
# STATUS: [DONE] 2026-09-22 — "lgtm" → Step 4 closed → Step 5 impl written.

==================================================
ACTION 1 — DONE: LGTM ON P05 TESTS
==================================================
# STATUS: [DONE] 2026-09-22 — "LGTM" → Step 4 closed → Step 5 impl written.

==================================================
ACTION 3 — DONE: EYEBALL REAL-DATA VERIFY
==================================================
# STATUS: [DONE] 2026-09-22 — you replied "ok" → M2b.1 marked complete in BUILD_STATUS.

==================================================
ACTION 2 — DONE: GIT IDENTITY + COMMITS
==================================================
# STATUS: [DONE] 2026-09-22 — identity Christian; efb59ff (first) + a6908c2 (M2b.1 complete).

==================================================
ACTION 4 — PERIODIC: KEEP THIS FILE FRESH
==================================================
# AI UPDATES THIS FILE WHENEVER:
#   - A SPEC/TEST PAIR IS READY FOR YOUR LGTM
#   - GATE CHANGES (G0->G1 ETC)
#   - BUILD_STATUS.md KNOWN BUGS APPEAR
#   - A NEW BLOCKING HUMAN STEP IS ADDED
# IF AN ACTION ABOVE SAYS [DONE], AI HAS ALREADY CLEARED IT.

==================================================
DO NOT
==================================================
# DO NOT TELL AI TO "SKIP GUARDRAILS" OR "MOVE FAST" ON
# RISK / EXECUTION / LEDGER — AI MUST REFUSE PER AGENTS.MD §10
# DO NOT MARK BUILD_STATUS "complete" WITHOUT LOG + HAND-CALC + LGTM
