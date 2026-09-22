# USER_ACTIONS — WHAT YOU (HUMAN) MUST DO
# ALL CAPS. UPDATED CONSTANTLY. READ THIS FIRST.

## CURRENT STATUS (UPDATED 2026-09-22)
# GATE: G0 ENTRY
# M1 COMPLETE: 42/42 TESTS GREEN
# M2b.1 COMPLETE: 76/76 FULL SUITE GREEN (0 SKIP) — LGTM + verify OK + first commit efb59ff
# NEXT: (A) COMMIT the M2b.1 impl delta  (B) AI starts M2.1 tests rewrite

==================================================
ACTION 1 — DONE: LGTM ON P05 TESTS
==================================================
# STATUS: [DONE] 2026-09-22 — "LGTM" → Step 4 closed → Step 5 impl written.

==================================================
ACTION 3 — DONE: EYEBALL REAL-DATA VERIFY
==================================================
# STATUS: [DONE] 2026-09-22 — you replied "ok" → M2b.1 marked complete in BUILD_STATUS.

==================================================
ACTION 2 — PARTIAL: GIT (identity + first commit done; impl delta uncommitted)
==================================================
# DONE: identity Christian <onyebuchiogadinma@gmail.com>; first commit efb59ff.
# STILL UNCOMMITTED:
#   financial_analyst_app/modules/reference/reference.py   (M2b.1 impl — NEW)
#   financial_analyst_app/tests/m2b/test_instrument_symbol_spec.py
#   docs/specs/m2b.instrument_symbol.md
#   BUILD_STATUS.md, USER_ACTIONS.md
# REPLY "COMMIT" and I will stage + commit
#   (message: M2b.1 Instrument & Symbol complete — impl + status 76/76)
# NOTE: I will NOT commit without your explicit "COMMIT" reply (AGENTS.md).

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
