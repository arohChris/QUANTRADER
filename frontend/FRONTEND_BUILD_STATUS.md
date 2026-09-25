# FRONTEND BUILD STATUS — QUANTRADER2

> **Frontend-only delivery tracker.** This file does not replace the repository-wide `BUILD_STATUS.md`; that file remains the system source of truth for backend gates, module status, and project-wide sequencing.
>
> **Purpose:** Track M10.4 frontend implementation independently. Frontend work may be developed, tested, and prepared for review without changing backend modules or authorizing backend implementation.
>
> **Merge authority:** The human. No frontend commit, push, or merge is performed by the Builder without explicit human approval.
>
> **HANDOFF 2026-09-25:** Builder stepped off frontend per human directive ("leave FRONTEND for now") — **frontend developer continues per `frontend/HANDOFF.md`** (transfers the Builder's full mental model: 8 laws, slice playbook, integration rules, live-backend facts). Builder active on backend integration track (`docs/architecture_integration_layer.md`).

---

## Current Frontend Phase: M10.4.3 Trader Dashboard & Chart Experiment — awaiting human implementation review

**Entry:** 2026-09-26  
**Backend gate:** Does not advance or bypass G0.  
**Backend changes in this slice:** None.  
**Backend interfaces:** Documented for later integration; no endpoint or contract is invented in frontend code.

---

## Frontend Design Authority

| Source | Role |
|---|---|
| ``frontend/specs/frontend_spec.md`` | Frozen visual/product/design authority, REV2.3 |
| `frontend/FRONTEND_BACKEND_INTEGRATION_PROPOSAL.md` | Open-to-amendment integration and access proposal |
| `frontend/FRONTEND_BUILD_STATUS.md` | This frontend-only delivery tracker |
| `BUILD_STATUS.md` | Repository-wide source of truth; frontend cannot override it |

The current design authority defines:

- Vite + React + TypeScript;
- feature-first folders;
- no backend Python imports;
- backend does not serve static frontend files;
- no business calculations in the browser;
- provenance and safety state remain visible;
- one shared shell with public, authenticated, and Owner/Admin surfaces.

---

## Frontend Sub-Modules

Status values are limited to:

`not started` · `spec` · `contract` · `tests` · `tests approved` · `implemented` · `implemented, not verified` · `complete`

| Module | Sub | Spec/design | Tests | Test status | Backend dependency | Frontend status | Human review | Merge status |
|---|---|---|---:|---|---|---|---|---|
| M10.4 | Design lock | ✓ ``frontend/specs/frontend_spec.md`` REV2.3 | — | — | none | complete | ✓ ACTION 16F | reference only |
| M10.4 | Day-1 account panel | ✓ existing account_mode feature | 11 | existing account architecture tests | M1.3 account API only | implemented | prior design/implementation review | not a full M10.4 merge |
| M10.4.0 | Vite/React/TypeScript shell | ✓ frozen design + integration proposal | 8 (`frontend/tests/test_m10.4_shell_spec.py`) | **8/8 PASS** + typecheck + build green 2026-09-25; whole frontend **27/27** | none; mock data only | implemented, not verified | tests LGTM ✓ 2026-09-25 (impl review pending) | pending human merge |
| M10.4.1 | Role-based shell and navigation | ✓ access proposal + UI/UX design plan | 8 (`frontend/tests/test_m10.4.1_role_nav_spec.py`) | **8/8 PASS** + typecheck + build green 2026-09-26 | none; mock data only | **complete** | ✓ tests + impl LGTM 2026-09-26 | pending human commit/merge |
| M10.4.2 | Public Viewer dashboard | ✓ role proposal + UI/UX design plan | 8 (`frontend/tests/test_m10.4.2_viewer_dashboard_spec.py`) | **8/8 PASS** + typecheck + build green 2026-09-26; whole frontend **35/35** | none; mock data only | **complete** | ✓ tests + impl LGTM 2026-09-26 | pending human commit/merge |
| M10.4.3 | Authenticated Trader shell | ✓ role proposal + UI/UX design plan + §5.0 research basis | 26 (`frontend/tests/test_m10.4.3_trader_dashboard_spec.py`) | **26/26 PASS** + typecheck + build green 2026-09-26; whole frontend **64/64** | none; mock data only | **implemented, not verified** | ✓ tests LGTM 2026-09-26 (12); impl LGTM 2026-09-26 + 7 amendment rounds; newest tests awaiting re-LGTM | pending human commit/merge |
| M10.4.4 | Owner/Admin workspace | ✓ role proposal | 0 | not started | global admin APIs later | not started | pending | blocked |
| M10.4.5 | Trading Workspace manual/auto UI | ✓ unified workspace amendment | 0 | not started | M4/M5/M6 contracts later | not started | pending | blocked |
| M10.4.6 | Market/analytics chart surfaces | ✓ frontend design | 0 | not started | M2/M3 APIs later | not started | pending | blocked |
| M10.4.7 | Realtime snapshot/delta UI | ✓ synchronization proposal | 0 | not started | M10.1 contract later | not started | pending | blocked |
| M10.4.8 | Command Center | ✓ command design | 0 | not started | M10.3 later | not started | pending | blocked |

---

## Last Completed Slice — M10.4.2 Public Viewer Dashboard

### Scope

- design-system foundations wired into the Vite app;
- dark responsive application shell;
- public market workspace;
- mock watchlist, public intelligence, **news**, and **prediction quality (estimated accuracy)** panels;
- loading, empty, stale, and provenance presentation states;
- no network calls, WebSocket, or backend authority.

### Test file

`frontend/tests/test_m10.4.2_viewer_dashboard_spec.py`

**Status:** 8 tests LGTM'd (human 2026-09-26), **8/8 pass**; `npm run typecheck` + `npm run build` green; whole frontend suite **35/35**; implementation LGTM recorded 2026-09-26. Pending human commit/merge.

---

## Current Slice — M10.4.3 Trader Dashboard & Chart Experiment

### Scope

- responsive shell refinements;
- larger readable data/action text where needed;
- **researched sticky header** (`app-header`): nav toggle + `QT` brand mark left,
  status chips centre (market / `as_of` / sequence), icon actions right (search,
  notifications with dot, theme toggle, initials avatar) — NYSE PILLAR / MT4/5 /
  Tabler pattern;
- **Lucide icons** for every nav item and header action; icons decorative
  (`aria-hidden` by default), accessible name + `title` on the **button**, 40px
  control / 44px hit area — Lucide accessibility guidance;
- access bar moved into the primary navigation section;
- collapsible primary navigation sidebar: vertical menu when open, sidebar hides
  itself entirely when collapsed (toggle stays in the top bar, `aria-expanded`,
  label `Toggle navigation`);
- **market display toolbar** on both chart panels: **multi-asset selector**
  (`aria-label="Select assets"`, `aria-pressed` chips over `MOCK_INSTRUMENTS`, at
  least one always selected) drawing one bright `SERIES_COLORS` line per asset with
  a `.series-legend`, timeframe dropdown (`MOCK_TIMEFRAMES` + `TIMEFRAME_WINDOW`
  visible window), UTC session clock from `as_of`, and provenance notes; selection
  is presentation-only via `withSymbol` / `withTimeframe` (no network, no calc);
- **Viewer trimmed to public data only**: watchlist panel removed (personal state
  has no place on an unauthenticated surface), panel renamed to **Predictive
  quality for today** with "Estimated accuracy today";
- **top status strip removed** from the header (`Market: experiment`, `as_of`,
  `sequence`); that telemetry now lives in the market panel footer beside the data
  it describes;
- **research surface with a command-style chat** (`app/ResearchChat.tsx`, routes
  `/research` and `/app/research`): a command bar (`/price`, `/trend`, `/context`,
  `/compare`, `/quality`, `/help`), six predetermined questions one click away, and
  answers that surface **context blocks** (asset, last, day move, from high, day
  range, position in range, phase, `as_of`, provenance) instead of prose, with
  follow-up chips so the conversation continues. `buildContextReply` reads the same
  `pricePathAt` mock path as the charts, so the chat cannot contradict the screen; a
  persistent `mock analyst · no model or feed connected` badge keeps the boundary
  explicit. Styles in `app/styles/research.css`;
- **research surface aligned to the manual, not invented** — command language taken
  from `frontend_spec.md` §5 / `ui_ux_design_plan.md` §7.5 (`/analyze`, `/compare`,
  `/show`, `/open`, `/find`, `/explain`, `/why`, `/research`, `/help`) so the surface
  is ready for `POST /api/v1/commands` when M10.3 freezes, and preset questions use the
  manual's own phrasing ("Why did this signal trigger?", "Why is oil moving today?").
  Answers follow `frontend_spec.md` §6 — **no bare chat bubbles**: question → lead →
  Market context / Technical / Provenance sections → **evidence bar** (`met/total`) →
  read-only **Challenge** / **Deep research** actions → **provenance footer**
  `prompt v1 · mock model · data cut <as_of>`, with the documented status vocabulary
  `COMPLETE` / `UNVERIFIED` / `INSUFFICIENT_CONTEXT` (`/show risk` honestly returns
  `INSUFFICIENT_CONTEXT`). The M11 boundary is enforced in the copy: explain,
  summarise, retrieve, answer — never trades; no execution action exists;
- **footer tells the story instead of filler** — three beats (`Built` · `Challenge` ·
  `Need`) in `.footer-story`, then four short link groups and the legal strip. The
  per-group essay paragraphs are gone, and a test caps the footer at 220 words so it
  cannot drift back into filler;
- **footer rebuilt on the researched rhythm** — no surface (no fill, no top border,
  no shadow), no underlining on any footer link, and the `/login` `/app` `/admin`
  entry-route links removed (they belong in the navigation). The footer keeps its
  structure and gains real copy: a lead `.footer-about` paragraph, one or two
  sentences per group, and a `.footer-note` disclaimer, with 8px link steps inside a
  group, 32px column gutters, and a legal row spread edge to edge;
- **sidebar closes on any outside click or Escape** — a `mousedown` listener tests
  `sidebar.contains(target)`; the toggle button is no longer the only way out;
- **trade pane Auto / Manual switch** — segmented control
  (`role="group" aria-label="Trading mode"`); Manual renders the ticket, Auto hides
  every manual field and shows the auto-mode panel, so a hand-built ticket cannot
  exist while the strategy owns the decision;
- **asset selection is a box, not a chip wall** — `.asset-box` in the same control
  language as the timeframe select, opening a multi-select list
  (`role="listbox" aria-multiselectable`) with all twelve instruments, closing on
  outside click or Escape;
- **markets handles different magnitudes and precisions** — every price goes through
  `formatPrice(value, decimals)` (catalogue spans 0.66 → 64 250 and 1–4 decimals),
  the home overview plots absolute prices with a `scales differ` legend hint instead
  of a silent rebase, and the markets sizing is compact (`minmax(16rem)` cards,
  0.9rem padding, 4.5rem sparklines, 1rem trend value);
- **app split into feature modules** — `MarketPanel.tsx` (asset box, toolbar, chart),
  `HomeDashboard.tsx`, `TradePane.tsx`, `MarketsGrid.tsx`, leaving `App.tsx` at 12 kB
  (shell, header, sidebar, routes, footer); tests read the app through a shared
  `_read_app_source()` helper so the Viewer guards cover every module;
- **role renamed to `Admin`** — the privileged role is `Admin`, not `Owner/Admin`,
  across `ACCESS_ROLES`, `NAV_BY_ROLE`, routes, and the role tests
  (`test_m10.4.1_role_nav_spec.py`, `test_m10.4_shell_spec.py`);
- **markets surface** at `/markets` and `/app/markets`: a dashboard grid of **one
  panel per instrument** (twelve, from `MOCK_INSTRUMENTS`) in
  `repeat(auto-fill, minmax(17rem, 1fr))`, so the count on screen follows the
  viewport. Each panel states what the market is doing — symbol, `advance` /
  `drawdown` phase chip, indexed level, depth from the window high, window size and
  timeframe, `as_of` clock — and owns a sparkline built once and updated in place.
  Lives in `app/MarketsGrid.tsx` to keep `App.tsx` under the 20 kB modularity cap;
- **price follows time, and stays in drawdown** — `pricePathAt(index)` is a pure
  function of the point index (drift + two cycles − a linear decline from
  `drawdownStart`), so ticking time appends the next point of one continuous path,
  the visible tail is a sustained drawdown, and switching asset rescales that same
  path instead of rewriting history;
- **footer is tonal, logo removed** — footer uses the page background (`--bg`) with
  one hairline instead of its own surface/shadow, and the QUANTRADER brand block
  was deleted from the footer (branding stays in the header);
- **light mode text has more power** — `--text: #020617`, `--muted: #1e293b`,
  `--dim: #334155`, `--border: #b6c2d4`, `--text-weight-strong: 800`, plus a
  light-only typography layer (`app/styles/light-type.css`) that re-weights headings,
  labels, body copy, links and data readouts;
- **stylesheet split into ordered partials** under `frontend/app/styles/`
  (`tokens`, `base`, `shell`, `panels`, `market`, `footer`, `light-type`,
  `responsive`) with `app/styles.css` as the aggregator, so no single file trips the
  20 kB modularity cap; both spec test files read the assembled stylesheet through a
  shared `_read_styles()` helper;
- access bar (role context + entry links) relocated to the **footer**, which follows
  the researched sitemap-lite pattern (UXPin / LogRocket / 1Footer / USWDS):
  back-to-top (ArrowUp) → `.footer-inner` (max-width 84rem, centred, even 4-column
  rhythm) inside `<nav aria-label="Footer navigation">` → legal strip with
  `new Date().getFullYear()` + Privacy / Terms / Status, pinned with
  `margin-top: auto` in a `min-height: 100vh` flex shell;
- light theme rebuilt on a slate ramp (`#f1f5f9`/`#ffffff` surfaces, `#0f172a` text)
  so body text clears WCAG AA 4.5:1 and graphical elements 3:1, with darker
  contrast-safe accent variants, `--shadow-card`, `--shadow-header`, `--focus-ring`
  and reserved `--positive` / `--negative` financial semantics (USWDS / WTW / Linear
  token guidance);
- dark bright accent set `--accent-cyan / -violet / -lime / -amber / -magenta`
  used for panel rules, status chips, watchlist, signals, state chips, active nav,
  and a second dashed reference series on the chart; light mode maps each accent
  to a darker contrast-safe value;
- `HomeDashboard` and `TradePane` as **separate surfaces** (`/app` vs `/app/trading`);
- time-based mock market experiment for chart display via `lightweight-charts` (`createChart` + `LineSeries`);
- Trader-only manual trade input ticket with preview/confirm states;
- no backend calls, no business math, no secrets, and no execution.

### Test file

`frontend/tests/test_m10.4.3_trader_dashboard_spec.py`

**Status:** whole frontend suite **77/77**; `python -m ruff check tests`, `npm run typecheck`, and `npm run build` green 2026-09-26. Implementation LGTM recorded 2026-09-26 with nine post-review amendment rounds: (1) collapsed rail must never hide menu items, (2) vertical menu + sidebar hides itself when collapsed, access bar moved to the footer, (3) light/dark theme switch with dark default, (4) site-style footer + coherent light palette + bright dark accents, (5) research-driven sticky header, sitemap-lite footer, AA-safe light tokens, and Lucide icon buttons, (6) market toolbar with asset/timeframe controls + session clock and an evenly-rhythmed footer, (7) multi-asset market, watchlist removed from the public Viewer, *Predictive quality for today*, top status strip deleted, footer logo removed, 12-asset catalogue, tonal footer, stronger light text, stylesheet split, markets dashboard grid, and the role renamed to **Admin**, (8) markets heatmap + ranked leaderboard with the drawdown reported as the trend of the day, indexed section removed, boxed asset selector, Auto/Manual trade switch, outside-click sidebar close, researched footer copy and spacing, magnitude-aware price formatting, and compact markets sizing, (9) research chat rebuilt on the documented command language and answer contract (evidence bar, status vocabulary, provenance footer, read-only actions) and the footer rewritten as a concise three-beat story. Earlier test files were amended where the design changed on purpose: `test_m10.4.2_viewer_dashboard_spec.py` (`topbar` → `app-header`, `status-strip` removed + a guard that the top chips are gone, surfaces list trimmed, `font-variant-numeric` dropped, whole-app source instead of `App.tsx`), `test_m10.4.1_role_nav_spec.py` and `test_m10.4_shell_spec.py` (`Owner/Admin` → `Admin`, plus an explicit "not Owner/Admin" assertion), and both spec files assemble the partial stylesheets. The newest tests await human re-LGTM. Not committed/merged.

---

## Amendment — M10.4.2 Viewer panels

Per human direction 2026-09-26, the public Viewer dashboard shows **News** and
**Prediction quality (estimated accuracy)** instead of generic Capability/Health
panels. The estimate is labelled as a mock until the backend supplies a
validated figure. `frontend/specs/ui_ux_design_plan.md` §5.1 updated to match;
`frontend/tests/test_m10.4.2_viewer_dashboard_spec.py` updated accordingly.

---

## Earlier Slice — M10.4.1 Role-based Shell & Navigation

### Scope

This slice is backend-independent:

- role-aware navigation configuration;
- Viewer, Trader, and Owner/Admin navigation surfaces;
- `/login`, `/app`, and `/admin` entry points;
- accessible primary navigation labels and active state;
- mock session presentation;
- no network calls;
- no WebSocket calls;
- no backend file changes;
- no dependency on M2, M3, M4, M5, M6, or M7 implementation.

### Test file written before implementation

`frontend/tests/test_m10.4.1_role_nav_spec.py`

Planned checks:

1. Role-aware navigation config exists and remains frontend-only.
2. Viewer, Trader, and Owner/Admin surfaces are defined.
3. Viewer navigation remains read-only and public.
4. Trader navigation exposes own trading and settings surfaces.
5. Owner/Admin navigation exposes system, audit, configuration, and kill/resume surfaces.
6. App wires role-aware navigation with accessible labels/current state.
7. Navigation remains mock-driven with no backend authority.
8. Navigation remains modular and small.

**Status:** 8 tests LGTM'd (human 2026-09-26), **8/8 pass**; `npm run typecheck` + `npm run build` green; whole frontend suite **27/27**; implementation LGTM recorded 2026-09-26. Pending human commit/merge.

---

## Backend Interfaces Deferred

The UI may show these surfaces as disabled, planned, or mock-state placeholders, but must not call or invent the backend contract.

| Backend area | Deferred contract | Current frontend behavior |
|---|---|---|
| Authentication | Session/JWT, user, role, tenant, permissions | Role surface placeholder only |
| Market data | Instrument list, candles, live deltas, provenance | Mock chart/data state only |
| Analytics | Feature requests, `AnalyticsResult`, `as_of`, capability | Mock analytics state only |
| Strategy | Strategy list, enable/arm/pause/disarm, versions | Mock strategy state only |
| Signals | Signal list, explainability, freshness, shadow state | Mock recommendation state only |
| Trade plans | Entry, SL, TP, timing, lineage | Mock plan inspection only |
| Risk | Portfolio, margin, exposure, risk preview/decision | Mock safety state only |
| Orders | Preview, confirm, state machine, idempotency | No submission path |
| Executions | Fills, partial fills, unknown/reconciliation | Mock activity state only |
| Ledger/PnL | Realized/unrealized PnL, audit ledger | No financial calculation or display claim |
| Kill/resume | Owner/Admin emergency state and commands | Disabled placeholder only |
| Realtime | Authenticated WS snapshot/delta/replay | No WebSocket client in this slice |
| Commands | Parser, jobs, command permissions | Disabled command surface only |
| Configuration | Versioned Owner/Admin configuration APIs | Disabled redacted status only |

The integration proposal is the single place to document these interfaces until backend contracts are frozen.

---

## Access Model

| Surface | Viewer | Trader | Owner/Admin |
|---|---|---|---|
| Public market/analytics/research | Read | Read | Read |
| Private portfolio/orders/risk/ledger | Hidden | Own tenant | Authorized cross-tenant scope |
| Manual order preview/confirm | Hidden | Own account | Authorized |
| Approved strategy arm/pause | Hidden | Own broker/account | Global |
| Kill/resume | Hidden | Read-only status | Full control |
| Global configuration | Hidden | Own effective config | Full versioned control |
| Secrets/vault contents | Never | Never | Never |
| Audit/activity | Public activity only | Own account activity | Authorized operational audit |

Owner/Admin is the highest application privilege. The UI must show scope and audit privileged actions, but credentials, tokens, vault contents, and private keys remain redacted.

---

## Frontend Acceptance Gates

A frontend slice is not ready for merge until:

- human review of the written tests;
- all slice tests pass;
- no tests skipped or xfailed;
- `npm run typecheck` passes;
- `npm run build` passes;
- frontend-only changes are isolated;
- no backend files changed;
- no secrets or backend imports introduced;
- role visibility tests pass;
- loading, empty, stale, and error states exist;
- accessibility checks pass;
- human implementation review completed;
- human explicitly approves merge.

## Future Verification Commands

After implementation and test approval:

```text
npm install
npm run typecheck
npm run build
npm test
```

The exact scripts must be defined by the approved M10.4 implementation slice. No command is run before the test and implementation gates are approved.

---

## Known Frontend Blockers

1. ~~M10.4 shell tests require human LGTM before implementation.~~ **RESOLVED 2026-09-25** (tests LGTM).
2. ~~Full M10.4 toolchain files do not yet exist.~~ **RESOLVED 2026-09-25** (scaffold + typecheck/build green).
3. Backend session/auth contract is not frozen.
4. Market/analytics APIs are not implemented for frontend consumption.
5. M10.1 WebSocket snapshot/delta/replay contract is parked.
6. M4/M5/M6/M7 execution contracts are not implemented.
7. Owner/Admin configuration and audit APIs are not implemented.
8. ~~Node package installation and build verification have not begun.~~ **RESOLVED 2026-09-25** (npm install 69 pkgs; typecheck + build green; suite 19/19).

---

## Merge Log

No frontend merge has been performed.

| Date | Slice | Tests | Typecheck | Build | Human approval | Result |
|---|---|---|---|---|---|---|
| — | M10.4.0 Shell | 8/8 | Green | Green | Pending | Not merged |
| 2026-09-26 | M10.4.1 Role navigation | 8/8 | Green | Green | ✓ impl LGTM | Complete; not committed/merged |
| 2026-09-26 | M10.4.2 Public Viewer dashboard | 8/8 | Green | Green | ✓ impl LGTM | Complete; not committed/merged |
| 2026-09-26 | M10.4.3 Trader dashboard, trade pane, collapsible sidebar, researched header/footer, multi-asset market, markets dashboards, Admin role, AA light theme, Lucide icons | 64/64 (suite) | Green | Green | ✓ tests LGTM (12) + ✓ impl LGTM; newest tests awaiting re-LGTM | Implemented, not verified; not committed/merged |

---

## Known Issues

| Issue | Status | Note |
|---|---|---|
| Blank page immediately after the stylesheet was split into `app/styles/*.css` partials | Resolved by a browser refresh | Vite's dev module graph was stale after the CSS import graph changed; a refresh re-fetched the new partials. No code fault — `pytest` 64/64, `tsc --noEmit` and `vite build` were green throughout. If it recurs, restart the dev server (`npm run dev`) rather than debugging the app. |
| `styles.css` was briefly corrupted by a bad PowerShell line-range slice | Fixed, rules restored | Caught by the 20 kB modularity test. All dropped rules re-authored and split into ordered partials; no rule was lost in the final state. |

---

## Amendment Log

This file is amendable as frontend delivery progresses. Any change to backend contracts, roles, or execution safety must also be recorded in the integration proposal and approved through the repository change process.

**Last updated:** 2026-09-26  
**Owner:** Builder frontend delivery tracker  
**Human merge authority:** Required
