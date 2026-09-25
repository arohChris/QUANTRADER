# M10 EXPERIENCE — module README

> Bible: `System_Development_Manual/modules/10_M10_Experience.md` · Canonical tree §3 `modules/experience/`
> **Status: partially deferred** — M10.2 extracted here 2026-09-25; M10.1 parked; M10.3+ not started.

## 1. Purpose
Delivery ring module: HTTP/WebSocket surfaces + command layer that the frontend consumes. **No trading logic** — validate → service → JSON; business decisions stay in M3–M7.

## 2. Ownership / State
| Sub | Home | Status |
|---|---|---|
| M10.2 account-gate API | `api/v1/router.py` (extracted from `app/main.py` 2026-09-25) | implemented, spec LGTM'd (m1.3 two-gate) |
| M10.1 synchronization | planned `synchronization/` per parked spec `frontend/specs/m10.1_synchronization.spec.md` | `spec` (parked, A24 open) |
| M10.3 commands | planned `command/` | not started |
| M10.4 frontend | repo-root `frontend/` (bible §3 allows top-level) | shell implemented, hand-off ready |

## 3. Contracts consumed/produced
Consumes frozen `shared/core` (AccountGateService, permissions). Produces REST `/api/v1/account/*` (JSON only). Future: envelope + session (see `frontend/FRONTEND_BACKEND_INTEGRATION_PROPOSAL.md` §7 — proposal, not frozen).

## 4. Dependencies
`shared.*` only (direction: modules → shared allowed). `app/main.py` composes this module (CORS + singleton + include_router). **Never imports `modules.execution/trust` state for rendering without contracts.**

## 5. Why it is separate
Bible isolation: delivery ring builds/tests on its own cadence; frontend + API evolve without touching the trading chain.

## 6. Deferred work (this folder is the future home)
M10.1 event_publisher/replay · M10.2 session/JWT + health + envelope middleware · M10.3 command parser · market/analytics/strategy/risk/orders REST per proposal inventory · WS bridge.

## 7. Tests
Backend: `tests/test_account_switch.py`, `tests/test_account_mode_two_gate.py` (M1.3 spec, drive this API). Frontend architecture tests: `frontend/tests/` (19). M10.1 tests planned at `tests/experience/test_m10.1_spec.py`.

## 8. Verify / run
`python -m app.main` (smoke GET/POST account) from `financial_analyst_app/` · `pytest tests/test_account_mode_two_gate.py -q`.

## 9. Open questions
Auth (session vs JWT — proposal §13 Open) · per-user vs process-global MODE gate (blocker #9) · envelope adoption (FastAPI `detail` → stable `error.code`).
