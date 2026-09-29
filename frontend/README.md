# Frontend (M10.4) — pure static UI. No backend Python lives here.

**Location:** `QUANTRADER2/frontend/` — **project root**, not inside `financial_analyst_app/`.

**Design bible:** [`frontend/specs/frontend_spec.md`](specs/frontend_spec.md) — **FROZEN REV2.3** (**ACTION 16F DONE** 2026-09-23).

## Architecture lock (§0.1 REV2.2 — one stack)

**Stack:** **Vite + React + TypeScript** (one toolchain — not React *or* Vite alone).  
**Not allowed:** big HTML · React god-file · Vite junk-drawer · JS-only app code after M10.4 opens.

**M10.4 (open):**

```
frontend/
  package.json · tsconfig.json · vite.config.ts · index.html
  app/                # React + TypeScript surfaces and composition root
  design-system/      # foundations/ (colors, typography, spacing)
  shared/  specs/  tests/
```

**Legacy Day-1 panel removed 2026-09-27.** The dependency-free vanilla-JS
`features/account_mode/` panel and `services/account_api.js` were deleted as
superseded by the React application. The architectural locks that guarded them
were preserved in `tests/test_frontend_architecture_locks.py` rather than being
deleted along with the panel.

## Rules
- **Zero backend imports** — no `.py`, no `shared/`, no `modules/`. Enforced by
  `test_frontend_has_no_backend_module_imports`.
- **No network I/O in components.** All HTTP belongs in `services/*_api.ts`.
  That layer does not exist yet — there is currently **no typed API client**.
- Backend never serves these files (JSON-only app under `financial_analyst_app/app/`).
- Tests: `frontend/tests/` (run `python -m pytest` inside `frontend/`).
- **Philosophy:** information before decoration · context before navigation · evidence before conclusions · risk before execution · provenance on everything · panels not SaaS cards · command layer as front door.

## Backend integration status

There is currently **no working frontend-to-backend integration.** The legacy
panel was the last one and it has been removed. The backend exposes four
account-gate routes that are curl-only, protected by a spoofable `X-Role` header
rather than real authentication.

The integration plan — OIDC auth, the response envelope, pagination, the tick
WebSocket, and the deliberate build order that places the API gateway last — is
documented in `FRONTEND_BACKEND_INTEGRATION_PROPOSAL.md` (Amendment 2).

## Status
- Design bible **FROZEN REV2.3** — **ACTION 16F DONE** 2026-09-23 (`lgtm frontend spec`).
- React + Vite + TypeScript app built: 9 role-gated surfaces, design system, news section.
- **131 tests passing**, ruff clean, typecheck and production build clean.
- Full workstation shell / 10 environments: **partially gated**; remaining surfaces
  await backend contracts (see the integration proposal, §7.4.2 and §14).
