# QUANTRADER2 Frontend — Developer Hand-off (Builder → You)

> **Audience:** frontend developer taking over M10.4 delivery. Read this **once, in full** — it transfers *how the Builder thinks*, not just what exists.
> **Date:** 2026-09-25 · **Builder status:** stepped off frontend per human directive; active on backend integration. Frontend is yours now.
> **Authority chain (highest wins):** repo-root `AGENTS.md` → `BUILD_STATUS.md` → `frontend/specs/frontend_spec.md` (FROZEN REV2.3) → `frontend/FRONTEND_BUILD_STATUS.md` → this file. Integration draft: `FRONTEND_BACKEND_INTEGRATION_PROPOSAL.md` (amendable — NOT frozen).

---

## 1. Mental model — the 8 laws you must think with

1. **One system, three planes — the browser touches exactly one.** The product = QUERY plane (REST from our API) + STREAM plane (WS updates) + BROKER gateway (Deriv, account info + trade execution). You only ever build against plane 1 (and later plane 2). Deriv, the trading chain, tokens, vault — all live behind our backend; full design: `docs/architecture_integration_layer.md` (backend doc, read §1–§3 for context).
2. **The swap-point doctrine.** Features NEVER call `fetch`. All HTTP goes through `services/*_api.ts` — one module per backend domain. When contracts freeze, **only the service file changes; feature code must not.** The doctrine is unchanged, but as of 2026-09-27 it has **zero implementations**: the last one was a vanilla-JS file, now deleted. The `services/` layer is the first deliverable of the gateway work. See `FRONTEND_BACKEND_INTEGRATION_PROPOSAL.md` §6.2.
3. **Nothing is real until it's frozen.** Backend publishes an OpenAPI snapshot → a frontend test pins your generated types to it → drift fails CI, not production. The proposal §7 table (`Proposed → frozen`) is the scoreboard. Never wire to an endpoint just because it answers on localhost. No `openapi.json` exists yet.
4. **UI hides, backend enforces.** `X-Role` is local-demo only and may never branch a feature (it dies when OIDC lands). You disable/omit UI per role for UX; the backend re-authorizes every single call. A hidden button is *never* a security control.
5. **Zero business math in the browser.** No RSI/PnL/margin/sizing — backend computes, you render **with provenance**: `as_of`, quality, staleness always visible. A number without its timestamp is a lie in a trading UI. Forming candles / provisional analytics are visibly provisional (badges), never presented as final.
6. **Realtime: ticks only (amended 2026-09-27).** One multiplexed socket per browser; the client subscribes to `market.ticks.{instrument_id}.{timeframe}` topics. Tick frames carry the **absolute latest value** plus a per-topic `sequence` — full replace, **not** JSON-Patch. Discard any frame whose `sequence` is not greater than the last applied value, which makes reconnect safe with no replay buffer. **Tick topics are public and carry no `user_id`**: market data is stored once per `provider + instrument` and fanned out to all tenants of that broker. The per-tenant gap-free pipe (sequence/replay/`RECONNECTING`) is **deferred**, so private state is fetched over REST on navigate. The client **never** builds a candle from ticks — candles come from `GET /api/v1/market/candles`.
7. **Failures are visible, success is earned.** No success on non-2xx: typed backend response required, confirmation dialog for writes, error state (never a success toast on a 403). Kill/resume/live-arm flows get elevated confirmation.
8. **Serial slices.** One slice at a time, strictly: tests written first → **human LGTM** → implement → all gates green → **human merge**. You never self-approve, never self-merge, never "fix the tests" to make an impl pass — the tests encode the spec (AGENTS §2, the repo's blocking law).

## 2. Quickstart + definition of done

```bash
cd frontend
npm install            # once (lockfile committed; node_modules/ gitignored)
npm run dev            # vite dev server
npm run typecheck      # tsc --noEmit  (must pass)
npm run build          # tsc && vite build → dist/ (must pass)
python -m pytest       # architecture tests, 19/19 green (frontend/pytest.ini)
python -m ruff check tests
```

**DoD for every slice:** `pytest` all green, **0 skipped/xfail** · `ruff` 0 · `typecheck` clean · `build` green · **zero backend files touched** · no secrets · human reviews + merges. Verified toolchain: Node v24.21.0, npm 11.19.0.

## 3. Hard rules (violation = reject)

1. **This folder ships browser code only.** No backend Python, no `shared/`, no `modules/`, no secrets/tokens, no credentialed broker URLs. `.py` is permitted **only in `frontend/tests/`** — tests are dev tooling, never shipped (`dist/` gets HTML/CSS/JS only).
2. **One stack:** Vite + React + TypeScript, feature-first folders. No second toolchain, no `.jsx`, no god-files (>40KB rejected; shell files <20KB — tests enforce).
3. **Features never call `fetch`** (law 2). Same for WebSocket — no client outside the future `realtime/` module.
4. **No business math** (law 5). Presentation-only arithmetic (e.g., sorting, deltas for display) is fine.
5. **Mock-only until contracts freeze** (law 3). `app/mock_data.ts` + mock services now.
6. **Never trust client role** (law 4). `X-Role` local demo only.
7. **No success on non-2xx** (law 7).

## 4. What exists today

```text
frontend/
  package.json tsconfig.json vite.config.ts index.html   # M10.4.0 toolchain (npm install/typecheck/build GREEN)
  app/main.tsx app/App.tsx app/mock_data.ts               # shell: /login /app /admin — Viewer/Trader/Owner/Admin
  app/styles.css                                         # M10.4.2 design-system and responsive shell styling
  app/nav_config.ts                                        # M10.4.1 role-based navigation config
  shared/ui/index.ts                                     # primitives land in a later M10.4 slice
  design-system/foundations/                             # colors/spacing/typography css
  specs/                                                 # frontend specs
  tests/test_frontend_architecture_locks.py              # 7 — architecture invariants (no page-specific tests)
  tests/test_m10.4_shell_spec.py                         # 7  — shell acceptance
  pytest.ini ruff.toml .gitignore                         # test/ruff/ignore config (importlib mode: REQUIRED for dotted test names)
```

> **Removed 2026-09-27 (Amendment 2):** `features/account_mode/` (Day-1 breaker panel) and
> `services/account_api.js`. The vanilla-JS panel was superseded by the React app. Its
> architectural locks were **moved** into `tests/test_frontend_architecture_locks.py` —
> including `test_frontend_has_no_backend_module_imports`, the guard that stops a browser
> bundle from importing backend Python. That lock must not be lost.
>
> `components/`, `realtime/`, `state/`, and `types/` were empty and untracked scaffolds;
> they have been removed so that layout tests only assert directories that survive a clean
> checkout.

- **M10.4.0 status:** implemented; tests LGTM'd 2026-09-25; frontend green; typecheck+build green; awaiting human impl review + merge (`FRONTEND_BUILD_STATUS` Merge Log).
- **M10.4.1 status:** complete; tests and implementation LGTM'd 2026-09-26; awaiting human commit/merge.
- **M10.4.2 status:** complete; tests and implementation LGTM'd 2026-09-26; awaiting human commit/merge.
- **M10.4.3 status:** queued for Trader Dashboard and Chart Experiment; tests must be written before implementation.
- **No working frontend-to-backend integration remains.** The Day-1 panel was the last
  consumer and is gone. The four account-gate routes are curl-only. Building the typed
  `services/*_api.ts` layer is the first task of the gateway work — see
  `FRONTEND_BACKEND_INTEGRATION_PROPOSAL.md` §6.2.
- **Backend account API facts (live now):** GET needs no permission; POST needs `X-Role: admin` (403 viewer/unknown/missing — no default); 409 gate-state conflict; 422 invalid body; responses are spec-frozen (no envelope yet). Local demo only. `X-Role` is a client-supplied header and is **not authentication**.

## 5. Your slice playbook (next = M10.4.3)

Order is fixed by the `FRONTEND_BUILD_STATUS` table: **M10.4.3** Trader → .4 Owner/Admin → .5 Trading Workspace → .6 charts → .7 realtime → .8 Command Center. M10.4.2 Public Viewer dashboard is implemented, tested, and awaiting human implementation review.

Per slice:
1. Read the slice row + frozen spec section it maps to.
2. **Write tests first** in `frontend/tests/test_m10.4.N_<short>.spec.py` style (mimic `test_m10.4_shell_spec.py`: assert files/strings/boundaries, scan for forbidden content, no mere "doesn't throw" asserts).
3. **Stop — get human LGTM on the tests** (AGENTS Step 4; you must not implement first).
4. Implement only what the tests demand; keep files small (<20KB, >40KB hard-rejected).
5. Run the §2 gates; update `FRONTEND_BUILD_STATUS` row; **human merges.**

**M10.4.3 can start now:** Trader surfaces need no backend; session stays mock (integration rule 6); the backend session contract (`GET /api/v1/session`) is Phase 2, not a blocker for mock-driven Trader UI.

## 6. When backend contracts freeze (integration rules)

1. **Types from the contract, CI-checked** — pin `types/` to the OpenAPI snapshot; drift fails CI.
2. **Dev proxy:** add `server.proxy: {'/api': 'http://127.0.0.1:8000'}` in `vite.config.ts` (single origin; backend CORS allowlist is `127.0.0.1:8000` + `file://` only).
3. **Envelope:** consume `{data, meta{correlation_id, as_of, schema_version, sequence, next_cursor}, error{code, retryable, details}}` — stable `error.code`, **never** FastAPI `detail` strings.
4. **Idempotency:** trade writes carry the **backend-issued** correlation/plan id in `Idempotency-Key` — never mint a fresh random key per retry.
5. **Realtime only after M10.1 freezes:** one `realtime/` module owns snapshot/gap/replay/`STALE`; features subscribe.
6. **Session first:** Phase-2 auth starts at `GET /api/v1/session`; until then session is mock. No feature branches on `X-Role`.
7. **Per-user safety:** MODE/AUTO are currently process-global on the backend (known blocker #9) — don't build per-tenant assumptions on it until the session/tenant contract lands.

## 7. What's happening behind you (context, no action needed)

- Builder is now on **backend integration**: M10.2 routes live in `modules/experience/api/v1/router.py` (composition root `app/main.py`), M11 placeholder exists, design doc `docs/architecture_integration_layer.md` (envelope/session/WS/Deriv gateway + gate law: no execution code before G0).
- Backend suite: **534/534** (its M3.1 tests are honest RED pending a human change list — unrelated to you).
- Your blockers are tracked in `FRONTEND_BUILD_STATUS → Known Frontend Blockers` (3,4,5,6,7 = backend contracts not yet frozen; all are *integration-time* blockers, not build-time — slices .1–.4 run on mocks).

## 8. Where to log work

Update `frontend/FRONTEND_BUILD_STATUS.md` (row per slice + Merge Log). Repo-root `BUILD_STATUS.md` stays backend/gate authority — don't edit it. Human owns all commits/merges.

**Anti-goals (the things that get a slice rejected):** weakening an architecture test to pass · adding a backend import · putting `.py` outside `frontend/tests/` · calling `fetch` inside a feature · building a second toolchain · implementing before test LGTM · claiming "done" without gates + human merge.
