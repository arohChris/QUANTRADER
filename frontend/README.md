# Frontend (M10.4) — pure static UI. No backend Python lives here.

**Location:** `QUANTRADER2/frontend/` — **project root**, not inside `financial_analyst_app/`.

**Design bible:** [`docs/frontend_spec.md`](../docs/frontend_spec.md) — **REV2 institutional workstation** + **§0.1 file architecture lock**. Await human `lgtm frontend spec` (**ACTION 16F**).

## Architecture lock (§0.1 REV2.2 — one stack)

**Stack:** **Vite + React + TypeScript** (one toolchain — not React *or* Vite alone).  
**Not allowed:** big HTML · React god-file · Vite junk-drawer · JS-only app code after M10.4 opens.

**M10.4 (when opened):**

```
frontend/
  package.json · tsconfig.json · vite.config.ts · index.html
  features/<name>/     # .tsx + feature styles + tests
  design-system/ services/ state/ realtime/ types/ shared/ui/
```

**Now (Day-1, M10.4 closed):** static multi-file — no `npm` to open the panel:

```
frontend/
  design-system/foundations/   colors.css · typography.css · spacing.css
  services/                    account_api.js
  features/account_mode/       index.html · styles.css · main.js · guards.js
```

- HTML = markup only (`<link>` CSS + `<script type="module" src=main.js>`)
- Logic = small `.js` modules with relative `import`
- Shared tokens = `design-system/foundations/*.css`

## Rules
- **Zero backend imports** — no `.py`, no shared/, no modules/.
- Talks to API over HTTP only: `http://127.0.0.1:8000/api/v1/...`
- Backend never serves these files (JSON-only app under `financial_analyst_app/app/`).
- Tests: `financial_analyst_app/tests/m10_experience/` (architecture enforced there).
- **Philosophy:** information before decoration · context before navigation · evidence before conclusions · risk before execution · provenance on everything · panels not SaaS cards · command layer as front door.

## Current feature — account_mode (breaker panel)
| File | Role |
|------|------|
| `features/account_mode/index.html` | Thin markup (MODE + AUTO + guard dialogs) |
| `features/account_mode/styles.css` | Feature styles |
| `features/account_mode/main.js` | Status render, refresh, keys, wiring |
| `features/account_mode/guards.js` | 3-checkbox guard covers |
| `services/account_api.js` | `GET status` / `POST mode` / `POST auto-trade` |

## Status
- Design spec **REV2 DRAFT** + **§0.1 architecture lock** 2026-09-23 — **ACTION 16F** open.
- Breaker panel **split multi-file** under human direction while M2.8 spec under review.
- Full workstation shell / 10 environments: **gated** until human opens M10.4 after M2.8 clears.
