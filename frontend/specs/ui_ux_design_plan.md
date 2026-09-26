# QUANTRADER2 Frontend UI/UX Design Plan

> **Status:** Design only. Open for amendment. This document does not authorize implementation.
>
> **Scope:** Frontend interface design and backend integration recommendations. No backend code, routes, schemas, or configuration changes are authorized by this plan.
>
> **Authentication:** Deferred. Google Sign-In is not part of the current UI slice. The `/login` surface remains a placeholder until the authentication slice is explicitly opened.
>
> **Current frontend slice:** M10.4.0 shell is implemented and awaiting review. M10.4.1 role-based navigation is the next planned implementation slice, with tests awaiting human LGTM.
>
> **Source authority:** `frontend/specs/frontend_spec.md` remains the frozen visual/product authority. This file expands the UI/UX design and remains amendable.

---

## 1. Design objective

Build an institutional trading workstation that answers five questions without requiring the user to understand the backend:

```text
What is happening?
Why is it happening?
What should I investigate?
What can I do?
What will happen if I do it?
```

The interface should feel like an operating system for markets, not a marketing dashboard. It is dense, fast, evidence-first, keyboard-friendly, and honest about uncertainty.

The design is built from patterns observed in professional products:

- TradingView: chart anatomy, symbol search, timeframe controls, indicators, drawings, watchlists, multi-chart layouts.
- Bloomberg: command-first navigation, dense workspaces, multi-panel monitoring, risk and order visibility.
- Koyfin: configurable dashboards, draggable/resizable widgets, linked watchlists and charts.

These are interaction patterns, not visual copies.

---

## 2. Product principles

1. **Evidence before conclusions.**
2. **Context before navigation.**
3. **Risk before execution.**
4. **Provenance before price.**
5. **One shared shell with contextual workspaces.**
6. **Every state is visible and inspectable.**
7. **Keyboard-first operation.**
8. **Monospace for truth; modern sans-serif for action.**
9. **No silent calculations or invented data.**
10. **Public, Trader, and Owner/Admin surfaces share a design system but never share private data.**

---

## 3. Access surfaces

| Surface | Viewer | Trader | Owner/Admin |
|---|---|---|---|
| `/` | Public market, analytics, research, health | Same plus private context | Same plus authorized system entry |
| `/login` | Deferred | Deferred | Deferred |
| `/app` | No private access | Own account/tenant | Authorized access |
| `/app/trading` | Hidden | Own trading | Authorized trading |
| `/app/settings` | Hidden | Own settings/approved strategies | Global settings where permitted |
| `/admin` | Hidden | Hidden | Owner/Admin system control |
| `/admin/system` | Hidden | Hidden | Global configuration and diagnostics |

Navigation hiding is only a UX measure. Backend authorization remains authoritative.

---

## 4. Application shell

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ SYSTEM STATUS   MARKET   MODE   RISK   SEARCH / COMMAND       USER / SYS │
├────────┬──────────────────────────────────────────────────────┬─────────────┤
│ NAV    │                 PRIMARY WORKSPACE                   │ INSPECTOR   │
│        │                                                      │             │
├────────┴──────────────────────────────────────────────────────┴─────────────┤
│ EVENTS │ ORDERS │ POSITIONS │ ALERTS │ SYSTEM │ ACTIVITY │ TERMINAL       │
└────────────────────────────────────────────────────────────────────────────┘
```

### 4.1 Top strip

Persistent items:

- System status;
- Market context;
- MODE;
- AUTO;
- Risk;
- Command search;
- User identity;
- Session state;
- Owner/Admin entry when authorized.

### 4.2 Left navigation

```text
OVERVIEW
MARKETS   Markets · Watchlists · Screener · Heatmap
RESEARCH  Research · AI Analyst · News · Calendar · Signals
TRADING   Trade · Positions · Orders · Strategies · Algorithms
RISK      Monitor · Exposure · Scenarios · Limits
SYSTEM    Models · Data · Agents · Automations · Logs
CONFIG    Workspace · Trading · Risk · AI · Integrations · System
```

### 4.3 Inspector

Right rail opens when a user selects:

- chart region;
- signal;
- trade plan;
- order;
- position;
- AI answer;
- strategy;
- health event;
- audit record.

The inspector shows the selected object, its state, evidence, lineage, and allowed next actions.

### 4.4 Bottom dock

```text
EVENTS
ORDERS
POSITIONS
ALERTS
SYSTEM
ACTIVITY
TERMINAL
```

The dock is a projection of backend-owned state. It is not a place to calculate new values.

---

## 5. Dashboard design

### 5.0 Research basis (2026-09-26)

Header, footer, theme, and icon decisions below are taken from current practice,
not invented:

| Area | Source | What we adopted |
|---|---|---|
| Header | NYSE PILLAR Trade Ops Portal user guide; MT4/5 Trade Terminal guide; Tabler (sticky navbar) | One sticky top bar: nav toggle + brand left, live status chips centre, icon actions + avatar right. Account/status summary always visible, no second status bar. |
| Header density | AdminLTE trading-dashboard guide | Trader screens show price chart, watchlist, order-entry and positions in one workspace; investor-style brief views suit a light theme. |
| Footer | UXPin footer guide; LogRocket; 1Footer; USWDS footer; Framer | Sitemap-lite: back-to-top, brand block, **4–6 link groups** with uppercase tracked headings, separate legal strip. Dynamic year via `new Date().getFullYear()`. Groups wrapped in `<nav>`; `<footer>` is the `contentinfo` landmark. |
| Footer spacing/type | 1Footer | Link line-height 1.6–1.8, ≥44px tap targets on mobile, generous vertical padding (2.5rem here), muted-but-AA link contrast. |
| Light theme | USWDS color tokens; WTW data-viz palette; Linear tokens | Slate ramp (`#f1f5f9` → `#ffffff` surfaces, `#0f172a` text) so body text clears 4.5:1 and graphical elements 3:1; one accent hue family plus separate semantic `--positive` / `--negative` for financial meaning. |
| Icons | Lucide accessibility guide | Icons are decorative (`aria-hidden` by default). The accessible name goes on the **button** (`aria-label` + `title` tooltip), never on the icon. Interactive targets stay ≥40px with 44px hit area. |

### 5.1 Viewer dashboard

Purpose: public market intelligence and research.

```text
Top status
├── Market overview
├── Primary chart (multi-asset)
├── Market status
├── Public signals/recommendations
├── Public AI insights
├── News
└── Predictive quality for today (estimated accuracy, mock until validated)
```

Public dashboards show **news and predictive quality for today** instead of generic
capability/health panels, and carry **no watchlist** — a watchlist is personal
state and does not belong on an unauthenticated surface. Estimated accuracy is
displayed as a clearly labelled mock estimate until the validated backend figure
is available.

### 5.2 Trader dashboard

Purpose: research, portfolio, signals, and execution awareness.

```text
Top status
├── Portfolio summary
├── Primary chart
├── Secondary chart / watchlist
├── Positions
├── Active signals
├── Risk state
├── Open orders
└── Recent activity
```

The trade pane is a **separate surface** from the home dashboard, reached at
`/app/trading`. It carries the execution chart, the execution checklist, and the
manual trade ticket; the home dashboard stays a read-only overview.

**Market display toolbar** (both chart panels, `.market-toolbar`): a **multi-asset
selector** (`role="group" aria-label="Select assets"`) built from
`MOCK_INSTRUMENTS` — **twelve** instruments (EUR/USD, GBP/USD, USD/JPY, AUD/USD,
USD/CAD, NZD/USD, BTC/USD, ETH/USD, SOL/USD, XAU/USD, XAG/USD, WTI/USD) as toggle
chips with `aria-pressed`, laid out with
`grid-template-columns: repeat(auto-fill, minmax(7.5rem, 1fr))` so **at least ten
fit on screen at once** on a wide viewport and the count per row follows the
viewport. At least one asset always stays selected. Selecting several assets draws
**one bright line per asset** on the same panel, coloured from a twelve-entry
`SERIES_COLORS`, with a `.series-legend` naming each line. Alongside it: a
**timeframe dropdown** (`aria-label="Select timeframe"`, `MOCK_TIMEFRAMES`:
1m/5m/15m/1H/4H/1D) that changes the visible window via `TIMEFRAME_WINDOW`, a
**session clock** showing `as_of` in UTC, and provenance notes (`as_of`, window
size, `indexed to 100 at window start`, `quality`, `reference_snapshot_id`).

**Price follows time, and stays in a drawdown.** The mock price is a pure function
of elapsed time: `pricePathAt(index)` = drift + two slow cycles minus a linear
decline that begins at `drawdownStart` (index 18). Consequences that matter:

- the same index always produces the same price, so ticking time appends the next
  point of one continuous path instead of jittering the last value;
- the visible tail of the series is a **sustained drawdown** — price peaks, then
  keeps falling as the clock advances, instead of a straight climb. The markets
  surface reports that drawdown as the asset's **trend of the day**;
- switching asset rescales that same path (`buildPoints`), so history is never
  rewritten and every asset shares one time shape;
- `pathPhase(index)` labels the current mock phase as `advance` or `drawdown` and
  is shown as a `.phase-chip` (green/red) in the toolbar.

**Different magnitudes, different precisions.** The catalogue deliberately spans
three magnitudes (0.66 AUD/USD → 149.62 USD/JPY → 64 250 BTC/USD) and four decimal
precisions (`decimals` 1–4). Every price on screen is rendered through
`formatPrice(value, decimals)`, so a row never mixes `1.0842` with float noise, and
the day-range bar tooltip is formatted the same way. The home overview chart plots
**absolute** prices with no rebasing: the legend carries each asset's real last
price, and when the selected assets are more than two magnitudes apart a
`scales differ — compare each asset in Markets` hint appears instead of silently
flattening the smaller series.

**Header:** `position: sticky` bar with brand + nav toggle on the left and the
icon action cluster (search, notifications, theme toggle, initials avatar) on the
right. The former top status strip (`Market: experiment`, `as_of`, `sequence`) is
**removed** — that telemetry belongs to the market panel footer where it is shown
next to the data it describes, not in global chrome.

Primary navigation is a **static, collapsible vertical sidebar**:
- **static** - pinned under the header (`position: sticky`, `height: calc(100dvh - 4rem)`,
  `overflow: hidden`), so the rail never scrolls and never leaves the DOM; page scroll
  is unchanged;
- **expanded** - menu items render as a vertical list down the left edge, each with a
  Lucide icon from `NAV_ICONS` (`frontend/app/nav_config.ts`) plus its text label;
- **collapsed** - a 2.6rem **glyph rail that hover-expands**, exactly as
  `frontend_spec.md:114` requires. Labels live in `.nav-label` and return on hover;
  every link also carries `title`, so no menu item is ever lost;
- **outside click or Escape** collapses to the glyph rail - it no longer removes the
  rail from the page;
- a `@media (max-height: 700px)` pass compacts padding, font size and gap so the
  tallest role (Admin, 9 items ~361px) fits a short window without clipping;
- the header toggle keeps `aria-expanded` and the `Toggle navigation` label, with the
  tooltip changing between "Collapse to icon rail" and "Expand navigation".


The **access bar** (role context) lives in the **footer**, not in the
navigation, so it never competes with the menu for rail space. The footer opens
with the **story** — three beats, no filler:

- **Built** — research-grade scaffolding: data contracts, risk gates, one market
  read once and reused everywhere;
- **Challenge** — one trader, one strategy, one honest number: expectancy after
  fees, proven out-of-sample, before anything is allowed to trade;
- **Need** — time and real data to validate the edge, then capital to size it.

Below it sit four short link groups (Access, Workspace, Build, Legal) and the legal
strip. It is **not a surface** — no fill of its own, no top border, no shadow, no
underlining on any footer link; it is the page background continuing, divided only
by spacing. The QUANTRADER logo is deliberately absent from the footer; branding
lives in the header only. The `/login`, `/app`, `/admin` entry-route links are gone:
those paths belong in the navigation, and the footer repeats only the pages a reader
wants at the end of a session (trade pane, markets, research).

Footer spacing follows the researched rhythm: 8px steps inside a group
(`gap: 0.6rem`, `min-height: 2rem` per link), 32px gutters between columns
(`gap: 2rem`), and a legal row spread edge to edge
(`justify-content: space-between`). Copy is deliberately short — a test caps the
footer at 220 words so it cannot drift back into filler.

**Research surface** (`/research` public, `/app/research` signed-in) follows the
documented command language and answer contract rather than inventing its own.

*Command language* comes from `frontend_spec.md` §5 and `ui_ux_design_plan.md` §7.5,
so the surface is ready for `POST /api/v1/commands` when M10.3 freezes:
`/analyze`, `/compare`, `/show`, `/open`, `/find`, `/explain`, `/why`, `/research`,
`/help`. Preset questions use the manual's own phrasing — **"Why did this signal
trigger?"** (`11_M11_Intelligence.md`), **"Why is oil moving today?"**
(`frontend_spec.md:153`).

*Answer shape* follows `frontend_spec.md` §6 and `ui_ux_design_plan.md` §8.1 —
**no bare chat bubbles**. Each answer is a structured block: question → lead
sentence → **Market context / Technical / Provenance** sections → **evidence bar**
(`met/total` plus the itemised evidence) → read-only actions (**Challenge**,
**Deep research**) → **provenance footer** `prompt v1 · mock model · data cut
<as_of>`. Status uses the documented vocabulary: `COMPLETE`, `UNVERIFIED`,
`INSUFFICIENT_CONTEXT` (a `/show risk` question returns `INSUFFICIENT_CONTEXT`
because no account or exposure exists to size).

*Capability boundary* is the M11 rule, verbatim: **explain, summarise, retrieve,
answer — never trades** (`System_Development_Manual.md:83`,
`11_M11_Intelligence.md:2`). The assistant offers no execution action, and every
answer states plainly what it does not have (no signal explainability, no model, no
feed). `buildContextReply` is deterministic and reads the same `pricePathAt` time
path as the charts, so an answer cannot contradict the screen. A persistent
`mock analyst · no model or feed connected` badge keeps the boundary explicit.
Styles live in `app/styles/research.css`.


**Sidebar** collapses on **any outside click or Escape**, not only via its own
toggle: a `mousedown` listener checks `sidebar.contains(target)` and closes when
the press lands outside the rail.

**Trade pane mode switch:** an Auto / Manual segmented control
(`role="group" aria-label="Trading mode"`, `aria-pressed` on the active segment).
The choice is the gate: **Manual** renders the manual ticket, **Auto** hides every
manual field and shows the auto-mode panel instead, so there is no way to submit a
hand-built ticket while the strategy owns the decision. Both paths stay
preview-only.

**Asset selection is a box, not a chip wall.** `.asset-box` is a bordered control in
the same language as the timeframe select; it opens a multi-select list
(`role="listbox" aria-multiselectable`) listing all twelve catalogue instruments,
closes on outside click or Escape, and reports "N selected · 12 available".

**Markets surface** (`/markets` for the public Viewer, `/app/markets` for signed-in
roles) is a **dashboard grid of one panel per instrument** — twelve panels, one per
`MOCK_INSTRUMENTS` entry, in `repeat(auto-fill, minmax(17rem, 1fr))` so the number on
screen follows the viewport. Each panel owns its own sparkline chart and states what
the market is doing: symbol, `advance` / `drawdown` phase chip, indexed level, depth
from the window high, window size and timeframe, and the `as_of` clock. Charts are
built once per asset and updated in place, so twelve live panels do not rebuild on
every tick.

**Header:** `position: sticky` bar in three zones — `header-left` (nav toggle,
brand mark `QT`, wordmark), `status-strip` (market state, `as_of`, sequence — now
inside the header rather than a second bar), and `header-actions` (search,
notifications with a `--negative` dot, theme toggle, initials avatar). Action
buttons use `.icon-button`: `min-width/min-height: 2.5rem` (40px control, 44px hit
area), `aria-label` + `title` on the button, icons left `aria-hidden`.

**Theme:** dark is the default; a toggle (`Toggle theme` icon button) switches to a
light palette via the `theme-light` class, applied to `document.body` as well as
the shell so the page background and scrollbars agree. Dark mode adds a bright
accent set — `--accent-cyan`, `--accent-violet`, `--accent-lime`, `--accent-amber`,
`--accent-magenta` — used for panel rules, status chips, watchlist, signals, state
chips, active nav, and a second dashed chart series (`CHART_PALETTE` `accent`).
Light mode maps every accent to a darker, contrast-safe value of the same hue and
adds `--shadow-card` / `--shadow-header` elevation plus a `--focus-ring` token, so
focus outlines are visible in both modes. Light-mode text is deliberately pushed
**darker and heavier** than dark mode (`--text: #020617`, `--muted: #1e293b`,
`--dim: #334155`, `--border: #b6c2d4`, `--text-weight-strong: 800`): a pale
background eats weight, so light mode buys its contrast back with tone and
`font-weight` on headings, labels, body copy, links and data readouts
(`app/styles/light-type.css`). `--positive` / `--negative` are reserved for
financial direction and are never used as decoration. No surface or text colour is
hardcoded outside the token set.

**Roles:** `Viewer` (unauthenticated, public), `Trader` (signed-in tenant user),
`Admin` (highest application privilege). The privileged role is named **Admin** —
not "Owner/Admin" — to match the vocabulary used in navigation, footers, and the
integration proposal.

### 5.3 Trader surfaces — Portfolio, Orders, Settings

Built from the **frozen contracts** so the surface survives the real endpoints without
a redesign (`frontend/app/mock_trader_data.ts` maps every field):

| Surface | Contract source | What is shown |
|---|---|---|
| Portfolio | `portfolio.py` (`Position`, `PortfolioState`), `risk.py` (`RiskDecision`) | cash by currency · equity · margin used · margin available · leverage · positions table (symbol, side, qty, avg entry, unrealised, realised, last trade plan) · exposure by symbol · last risk decision with its limit checks |
| Orders | `order.py` (`Order`, `OrderIntent`), `execution.py` (`ExecutionResult`, `Fill`) | trade chain · the eight-state machine with legal transitions · orders table (client_order_id, symbol, side, type, qty, filled, price, SL, TP, state, capability check, timestamp) · execution detail (broker order id, avg fill price, latency, slippage) · fill rows with commission |
| Settings | `account_mode.py` + `docs/specs/m1.3_account_mode_two_gate.spec.md`, `shared/core/config.py` | two execution gates (MODE / AUTO / live armed / bot will trade) · three-layer risk limits with hard boundaries · broker account references · kill switch status · display density L1–L3 |

Rules the surfaces obey, taken from the manual rather than invented:

- **Side is the sign of `qty`** — there is no `side` field on a position
  (`portfolio.py:14`).
- **No `filled_qty`** — filled quantity is summed from `ExecutionResult.fills`,
  because the order contract has no such field (`execution.py:34`).
- **UNKNOWN is never resolved client-side** — the state chip says *query before
  retry*, because an ambiguous broker response must be re-queried, not guessed
  (`AGENTS.md §3.2`).
- **client_order_id is the idempotency key** — a retry of the same trade plan reuses
  it so the broker deduplicates (`order.py:44`).
- **Money, margin, exposure and risk percentages are backend-owned**
  (`frontend_spec.md:61`) — displayed read-only, badge-labelled, never recomputed.
  Only percent-of-a-known-number and formatting happen in the browser.
- **Secrets are never displayed, for any role** — account slots show an env **name**,
  never a value (`config.py:55`).
- **Kill switch is read-only for Trader** and never auto-resumes after a daily-loss
  breach (`AGENTS.md §3.3`).
- **Colour is never the only signal** — every state chip carries its word, every
  P&L figure carries a sign.
- **Lineage on hover** — correlation and trace ids on every order row, trade plan on
  every position (`frontend_spec.md:21`, provenance before price).
- **Viewer sees none of it** — `/app/portfolio`, `/app/orders` and `/app/settings`
  resolve to the access preview for Viewer, and Viewer navigation carries no private
  entry (`FRONTEND_BACKEND_INTEGRATION_PROPOSAL.md:107`, `:233`).

### 5.4 Owner/Admin dashboard

Purpose: operational control across authorized system scope.

```text
System status
├── Tenant health summary
├── Data freshness
├── Strategy health
├── Risk and kill state
├── Latency and queue depth
├── Configuration versions
├── Audit activity
└── Support actions
```

### 5.4 Saved workspace model

Recommended saved workspaces:

```text
Morning Research
FX Desk
Active Trading
Risk Monitor
AI Research
Owner System View
```

A workspace stores:

- layout;
- selected instrument;
- timeframe;
- chart layout;
- visible panels;
- filters;
- selected context;
- density level.

Workspace persistence is a later backend/settings contract. Until then, the frontend may use local-only layout state and must label it accordingly.

---

## 6. Chart design

### 6.1 Chart anatomy

```text
Symbol search · Timeframe · Chart type · Indicators · Compare · Draw · AI · Layout
```

Main chart:

- candles/price;
- forming/closed state;
- quality badge;
- gap/stale badge;
- source and provider;
- watermark;
- reference snapshot;
- as-of timestamp.

Lower panes:

- backend-provided indicators;
- RSI;
- EMA/SMA;
- ATR;
- volume only when genuinely available;
- MACD;
- ADX;
- other backend analytics.

### 6.2 Chart layouts

Support:

```text
1 chart
2 charts
4 charts
Chart + watchlist
Chart + risk
Chart + signal stream
Chart + AI inspector
```

### 6.3 Chart behavior

- instrument/timeframe selection updates linked charts;
- charts may be linked or independent;
- drawings and layouts are separate from market data;
- indicator controls select backend analytics, not browser calculations;
- no fetch polling when a stream is available later;
- live forming data is visibly provisional;
- finalized data is visibly distinct.

### 6.4 Recommendation markers

Markers represent backend events:

```text
RECOMMENDED
VALIDATED
PLAN_CREATED
RISK_APPROVED
SUBMITTED
ACKNOWLEDGED
PARTIALLY_FILLED
FILLED
CLOSED
```

Do not color an open trade as winning/losing based on client-side price movement. Closed/settled result coloring must come from backend ledger state.

---

## 7. Search and Command Center

### 7.1 Global search

Use `Cmd+K` or `Ctrl+K`.

Search result groups:

```text
COMMAND
DATA
ACTION
AI
```

### 7.2 DATA results

- instruments;
- markets;
- timeframes;
- signals;
- trade plans;
- news;
- research;
- portfolio;
- orders;
- reports.

### 7.3 ACTION results

- preview trade;
- confirm trade;
- arm approved strategy;
- pause strategy;
- disarm strategy;
- create alert;
- switch approved environment;
- kill/resume for Owner/Admin.

### 7.4 AI results

- explain signal;
- explain market move;
- summarize portfolio;
- summarize research report;
- compare assets;
- challenge AI conclusion;
- deep research.

### 7.5 Search examples

```text
EURUSD
BTC
/open EURUSD
/analyze EURUSD 5m
/compare BTC ETH
/show risk
/show my exposure
/why did this signal trigger?
/research crude
/run strategy backtest
Why is this signal active?
```

### 7.6 Search safety

- Viewer never sees unauthorized action results.
- Trader sees approved own-account actions only.
- Owner/Admin sees authorized global actions.
- Free-text trade intent must route to the explicit M10 trade flow, not directly to AI.
- AI results remain read-only and cannot create, confirm, or modify orders.

---

## 8. AI Intelligence Center

### 8.1 Layout

```text
Question
→ selected context
→ context summary
→ answer
→ evidence
→ validation
→ limitations
→ actions
```

### 8.2 Evidence presentation

Every material claim should link to:

- source object;
- source field;
- source timestamp;
- data cut;
- watermark;
- reference snapshot;
- quality;
- capability status;
- validator result.

### 8.3 AI statuses

```text
COMPLETE
UNVERIFIED
INSUFFICIENT_CONTEXT
BLOCKED
UNAVAILABLE
CONFLICT
CANCELLED
FAILED
```

### 8.4 AI actions

Read-only:

- investigate;
- open chart;
- open signal;
- open trade plan;
- compare assets;
- challenge answer;
- request deep research.

Write-like actions such as alert creation or trade actions require a separate backend contract and are not part of the AI answer.

---

## 9. Trading Workspace

The Trading Workspace is one surface with conditional panels.

### 9.1 State model

```text
Environment: RESEARCH | PAPER | LIVE
Execution:   MANUAL | AUTO
Automation:  OFF | ARMED | RUNNING | PAUSED
```

These are independent. `LIVE` does not imply `AUTO`; `AUTO` does not bypass risk or kill state.

### 9.2 Manual panel

- instrument;
- side;
- order type;
- quantity;
- entry;
- stop loss;
- take profit;
- time-in-force;
- recommendation-to-ticket action;
- backend preview;
- effective-parameter confirmation;
- order state;
- partial fills;
- rejection/unknown state.

### 9.3 Automatic panel

- approved strategy;
- broker/account scope;
- disabled/shadow/armed/running/paused state;
- instrument/timeframe scope;
- risk budget;
- exposure limits;
- maximum trades;
- session limits;
- arm/pause/disarm;
- kill state;
- recommendation and execution stream.

The browser may request arming, but the backend scheduler and M5/M6 decide whether anything fires.

---

## 10. Feedback and state design

Every surface must define:

- loading;
- empty;
- stale;
- disconnected;
- replaying;
- unauthorized;
- unavailable;
- proxy warning;
- insufficient data;
- rejected;
- unknown;
- retryable error;
- non-retryable error;
- success.

No non-2xx response may produce a success toast.

Writes require:

```text
action
→ preview
→ explicit confirmation
→ backend response
→ visible state transition
```

---

## 11. Visual system

### 11.1 Color semantics

- cyan: demo/paper/research;
- red: live or destructive warning;
- amber: armed-but-paused, warning, degraded;
- green: healthy or confirmed state;
- blue: information;
- purple: AI;
- neutral: primary UI surfaces.

Color must not be the only carrier of meaning. Every state also has text and/or an icon.

### 11.2 Typography

- Inter or equivalent modern sans-serif for actions;
- JetBrains Mono or equivalent for data, prices, IDs, timestamps, and lineage;
- dense spacing;
- no oversized marketing typography.

### 11.3 Panels

Prefer:

- split panes;
- tables;
- inspectors;
- status strips;
- dockable panels;
- dense rows.

Use cards only for real conceptual objects such as a Signal, TradePlan, Alert, or AI answer.

---

## 12. Accessibility and responsiveness

- keyboard-first navigation;
- visible focus states;
- semantic buttons and links;
- ARIA labels on icon controls;
- text alternatives for state colors;
- screen-reader status announcements;
- reduced-motion mode;
- responsive collapse of the left rail;
- stacked chart/watchlist layout on small screens;
- touch targets for chart and table controls;
- no critical information available only through hover.

---

## 13. Frontend/backend integration recommendations

The frontend should expose service boundaries now, but call real APIs only after contracts freeze.

Recommended future service files:

```text
services/session_api.ts
services/account_api.ts
services/market_api.ts
services/analytics_api.ts
services/command_api.ts
services/intelligence_api.ts
services/strategy_api.ts
services/trade_plan_api.ts
services/risk_api.ts
services/order_api.ts
services/portfolio_api.ts
services/execution_api.ts
services/ledger_api.ts
services/health_api.ts
```

Rules:

- features never call `fetch`;
- features never open WebSockets;
- services own transport and typed errors;
- `realtime/` owns sequencing, replay, and stale state;
- mock services remain the default until backend contracts freeze;
- real data must preserve `as_of`, `quality`, `lifecycle`, and provenance;
- trade writes use backend-issued idempotency identity;
- AI queries send only the prompt and safe object references; backend builds private context.

---

## 14. Design-to-implementation order

1. M10.4.0 shell review/merge.
2. M10.4.1 role-based shell and navigation.
3. M10.4.2 Viewer dashboard surface.
4. M10.4.3 Trader dashboard surface.
5. M10.4.4 Owner/Admin dashboard surface.
6. M10.4.5 Trading Workspace manual/auto panels.
7. M10.4.6 chart and analytics surfaces using mock candles/analytics.
8. M10.4.7 realtime snapshot/delta UI after M10.1 contract freezes.
9. M10.4.8 Command Center and M10.3 integration.
10. M11 AI Intelligence Center after M10.2/M10.3/M11 contracts freeze.
11. Future news, calendar, research, backtest, alerts, and workspace persistence.

---

## 15. Current design decisions

- Google Sign-In/auth is deferred.
- One shared shell is preferred over separate applications.
- Viewer, Trader, and Owner/Admin surfaces use one design system.
- Manual and Auto trading share one Trading Workspace.
- Chart, search, command, AI, and backend data contracts are mocked until frozen.
- The frontend never calculates authoritative business values.
- Owner/Admin has the highest application privilege but never receives secrets.
- Streams arrive later through the backend M10.1/M10.2 layer; the frontend will not connect directly to market-data infrastructure.

**Design status:** open for human amendment.  
**Implementation status:** no implementation authorized by this document.
