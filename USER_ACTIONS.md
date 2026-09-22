# USER_ACTIONS — WHAT YOU (HUMAN) MUST DO
# ALL CAPS. UPDATED CONSTANTLY. READ THIS FIRST.

## CURRENT STATUS (UPDATED 2026-09-22)
# GATE: G0 ENTRY
# M1 COMPLETE: 42/42 TESTS GREEN
# M2b.1 COMPLETE: 36/36 + full suite 139/139 (post F-review) — commits efb59ff + a6908c2
# M2.1 REV1 + M2.2 SPEC + Drop 1.4: LGTM received 2026-09-22 ("lgtm") → Steps 1–2 closed
# NEXT: Review M2.2 normalize TESTS (Step 3 written) → reply "LGTM on M2.2 tests" → AI writes impl (Step 5).

==================================================
ACTION 11 — LGTM ON M2.2 NORMALIZE TESTS (Step 4 gate)
==================================================
# STATUS: [WAITING] 2026-09-22 — tests file ready for your review
# FILE: financial_analyst_app/tests/m2_market_data/test_normalize_spec.py
# SPEC: docs/specs/m2.normalize.md (REV1 — you already LGTM'd via ACTION 9)
# NOTE: Suite is intentionally collection-RED until Step 5 impl (import of
#   modules.m2_market_data.normalize missing) — same pattern as M2.1 Step 3–4.
# REPLY: "LGTM on M2.2 tests" → Step 4 closed → AI writes Normalizer impl → full green.

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
