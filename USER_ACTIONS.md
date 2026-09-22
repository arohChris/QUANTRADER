# USER_ACTIONS — WHAT YOU (HUMAN) MUST DO
# ALL CAPS. UPDATED CONSTANTLY. READ THIS FIRST.

## CURRENT STATUS (UPDATED 2026-09-22)
# GATE: G0 ENTRY
# M1 COMPLETE: 42/42 TESTS GREEN
# NEXT: P05 M2b.1 SPEC + TESTS + COMMODITY + AssetClass (AI WRITES, YOU LGTM BEFORE IMPLEMENTATION)

================================================================
ACTION 1 — PENDING: LGTM ON P05 TESTS (BLOCKING)
================================================================
# WHEN YOU SEE THIS MARKED [READY FOR REVIEW]:
#   1. READ docs/specs/m2b.instrument_symbol.md
#   2. READ financial_analyst_app/tests/m2b/test_instrument_symbol_spec.py
#   3. REPLY EXACTLY: LGTM on tests for M2b.1 Instrument & Symbol
#   4. ONLY THEN MAY AI WRITE IMPLEMENTATION (modules/reference/)
# STATUS: [READY FOR REVIEW] 2026-09-22 (spec review corrections applied same day)
#   Spec: docs/specs/m2b.instrument_symbol.md (commodity + AssetClass; I02 scoped to mapped pairs; passthrough = unresolved native symbol, M2.2 quarantines)
#   Tests: financial_analyst_app/tests/m2b/test_instrument_symbol_spec.py (29 tests)
#   Contract tests: 18/18 green (AssetClass 1.0 frozen, package 1.1.0)
#   Fixtures: config/assets/commodities.yaml (XAUUSD, XAGUSD); forex/crypto broker_native fixed
#   Tests written BEFORE any implementation (AGENTS.md Step 3).
#   Implementation does NOT exist yet — do not write it until LGTM.
# REPLY WHEN READY: LGTM on tests for M2b.1 Instrument & Symbol

================================================================
ACTION 2 — PENDING: GIT IDENTITY + FIRST COMMIT (BLOCKING FOR HISTORY)
================================================================
# GIT IS NOW ON PATH (2.55.0). REPO HAS .git BUT ZERO COMMITS.
# ALL FILES UNTRACKED. YOU MUST:
#   1. SET IDENTITY (once):
#        git config --global user.name "Your Name"
#        git config --global user.email "you@example.com"
#   2. REPLY "COMMIT" AND I WILL STAGE + MAKE THE FIRST COMMIT
#      (message: v0.1-foundation: M1 complete + P05 M2b.1 spec/tests + commodity AssetClass)
# STATUS: [BLOCKING] — NO HISTORY UNTIL IDENTITY + COMMIT APPROVED
# NOTE: I will NOT commit without your explicit "COMMIT" reply (AGENTS.md).

================================================================
ACTION 3 — WHEN READY: HAND-CALC / REAL-DATA VERIFY
# =================================================================
# AFTER IMPLEMENTATION PASSES 100% TESTS, YOU MUST:
#   1. OPEN docs/hand/<sub>_<date>.md — CHECK THE 2-3 HAND CALCS
#   2. RUN scripts/verify_<sub>.py OR OPEN THE OUTPUT CSV
#   3. EYEBALL FIRST 5 VALUES YOURSELF
#   4. REPLY WITH YOUR VERDICT (OK or FAIL + WHAT IS WRONG)
# STATUS: [NOT YET] — NO IMPLEMENTATION YET

================================================================
ACTION 4 — PERIODIC: KEEP THIS FILE FRESH
================================================================
# AI UPDATES THIS FILE WHENEVER:
#   - A SPEC/TEST PAIR IS READY FOR YOUR LGTM
#   - GATE CHANGES (G0->G1 ETC)
#   - BUILD_STATUS.md KNOWN BUGS APPEAR
#   - A NEW BLOCKING HUMAN STEP IS ADDED
# IF AN ACTION ABOVE SAYS [DONE], AI HAS ALREADY CLEARED IT.

================================================================
DO NOT
================================================================
# DO NOT TELL AI TO "SKIP GUARDRAILS" OR "MOVE FAST" ON
# RISK / EXECUTION / LEDGER — AI MUST REFUSE PER AGENTS.MD §10
# DO NOT MARK BUILD_STATUS "complete" WITHOUT LOG + HAND-CALC + LGTM
