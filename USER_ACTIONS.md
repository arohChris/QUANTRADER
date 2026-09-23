# USER_ACTIONS — WHAT YOU (HUMAN) MUST DO
# ALL CAPS. UPDATED CONSTANTLY. READ THIS FIRST.

## CURRENT STATUS (UPDATED 2026-09-23)
# GATE: G0 ENTRY
# M1 COMPLETE | M2b.1 COMPLETE | M2.1 Ingestion/Normal/Valid/Trans COMPLETE
# M2.1 Connection (P08) COMPLETE @ 367a85f + 759b0c5 — LGTM + HUMAN TEST OK 2026-09-23
#   You: "tested and all parts are working" → real-human verify recorded in BUILD_STATUS.
# M1.3 ACCOUNT MODE (demo/live) ADDED @ 13969ec — suite 256/256.
# NEXT ACTION: none blocking. Next = AI opens NEXT sub-module SPEC only when you
#   say which slice (no parallel slices). Reply with preferred next or "next".

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
