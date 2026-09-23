# Frontend Design Spec — QUANTRADER2 Institutional Workstation

Status: **FROZEN REV2.3 2026-09-23** — human `lgtm frontend spec` (**ACTION 16F DONE**).  
Location: `QUANTRADER2/docs/frontend_spec.md` (bible). Implementation at `QUANTRADER2/frontend/` under human-opened M10.4.  
Backend contract: M1.3 2-gate **MODE + AUTO** complete; kill = M5.5 reference; JSON-only REST under `/api/v1/…`.  
Supersedes cockpit-only REV1 sketch; REV2 folds human workstation vision 2026-09-23 (27-point brief).  
REV2.3 (2026-09-23 review): **Manual Trade Session**, **calculation boundary**, **LIVE manual confirm**, **MODE ≠ AUTO authorization**, layout `Dashboard`→`Workspace`.  
Day-1 panel chrome (same day): **left rail brand `QUANTRADER` + Role (X-Role) in side menu**; status chips in top rail.

> **One sentence:** This is **not a dashboard** — it is a **visual operating system** over intelligence, research, execution, risk, and algos: an institutional trading workstation (Bloomberg × quant hedge fund × aerospace control × premium AI) where every pixel answers *what is happening / why / what to investigate / what I can do / what happens if I do it*.

**Operational beauty over pretty.** Dense but controlled. Provenance before price.

---

## 0. Non-goals (Day-1 M10.4)

- No backend Python in `frontend/`; never served by `financial_analyst_app/app`.
- No marketing gradients, neon volume, social copy, hero images, SaaS card walls.
- No “one toggle” account switch (breaker panel only — already implemented).
- No bare AI chat bubbles; no naked prices (lineage on hover / inspector).
- No business logic in the client — consume contracts only (`MarketState`, `Signal`, `RiskState`, `AIAnalysis`, `SystemHealth`, `Workspace`, `Command`, …).
- Full workstation build opens only when human opens M10.4 after current backend slice (M2.8) clears.

### 0.1 File architecture lock (human 2026-09-23 REV2.2 — stack chosen)

**Stack (ONE choice):** **Vite + React + TypeScript** — not React *or* Vite as rivals; Vite is the build, React is the UI, TypeScript is required on both. One toolchain, feature-modular folders. Day-1 breaker panel stays **dependency-free static ES modules** until human opens M10.4 (panel opens with no `npm install`).

| Forbidden | Why |
|-----------|-----|
| **One big HTML + CSS (+ JS) file** | Unreviewable monolith |
| **One single React mess** (`App.jsx` god file, all features inline) | Does not scale, cannot extend |
| **Vite mess** (unordered dump, no feature boundaries) | Build without architecture is still a mess |
| **JS-only Vite/React** (no TypeScript) | No contract typing for M10.4 workstations |
| **No-feature-folder junk drawer** | Every new screen grows the same bucket |

**M10.4 tree (when human opens M10.4):**

```text
frontend/
  package.json                 # vite, react, react-dom, typescript, @types/*
  tsconfig.json · vite.config.ts · index.html
  design-system/               foundations · components · patterns · layouts
  features/
    account_mode/              # migrate Day-1 panel → .tsx + feature CSS
    command/ chart/ ai/ signals/ news/ risk/ system/ …
  services/                    # API clients — TS one domain per file
  state/ realtime/ types/ shared/ui/
```

**Rules:**

- **Feature-first** — each feature owns tsx, css/modules, tests; export a thin route/entry.
- **TypeScript only** under `features/`, `services/`, `state/`, `shared/` — no `.jsx` app code after M10.4 open.
- **Contracts only** — typed API; never recompute risk/PnL in UI (see §0.2 calculation boundary).
- Day-1 static layer: multi-file ES modules + split CSS (already in place).
- When M10.4 opens: `npm create vite`-style layout is fine; tests gain build check (`tsc` + `vite build`) and feature-boundary checks.

### 0.2 Calculation boundary (REV2.3 — lock)

> **Calculation boundary:** UI may perform presentation-only arithmetic that cannot diverge from a backend business rule. Broker-, instrument-, account-, currency-, margin-, or risk-dependent calculations must come from a **typed backend contract**. The UI must never invent authoritative risk, PnL, margin, sizing, or execution values.

Examples:

| Frontend OK (deterministic display) | Backend contract required |
|-------------------------------------|---------------------------|
| percent change on displayed prices | lot → notional / exposure |
| tick-count on visible rows | pip distance in money |
| formatting / rounding for display | estimated monetary risk · TP return · R:R |
| pure layout math | any margin, VaR, risk %, position sizing |

**Never** treat a failed optional calc as a blocker for a valid manual order path (see §4.1).

---

## 1. Product philosophy (lock)

| # | Principle |
|---|-----------|
| 1 | Information before decoration |
| 2 | Context before navigation |
| 3 | Evidence before conclusions |
| 4 | Actions next to information |
| 5 | Risk visible before execution |
| 6 | AI explains before it recommends |
| 7 | Every important state has a visible status |
| 8 | Everything important is inspectable |
| 9 | Everything inspectable has provenance |
| 10 | Workspaces are persistent |
| 11 | The command layer can operate the entire system |
| 12 | UI makes backend sophistication visible **without** forcing the user to understand the backend |

**Feel:** precision · speed · confidence without pretending certainty · density · hierarchy · state awareness · traceability · control · intelligence · professional seriousness.

---

## 2. Workstation Shell (not website chrome)

```text
┌───────────────────────────────────────────────────────────────────────────┐
│ SYSTEM STATUS   MARKET   MODE   RISK   SEARCH / COMMAND       USER / SYS │
├──────┬────────────────────────────────────────────────────────────┬───────┤
│      │                                                            │       │
│ NAV  │                    PRIMARY WORKSPACE                       │ INTEL │
│      │                                                            │       │
├──────┴────────────────────────────────────────────────────────────┴───────┤
│ EVENTS │ ORDERS │ POSITIONS │ ALERTS │ SYSTEM │ ACTIVITY │ TERMINAL       │
└───────────────────────────────────────────────────────────────────────────┘
```

- **Composable** workspace — not rigid grid: multi-chart, research-dominant, AI-dominant, risk-dominant layouts as split panes / resizable regions.
- Top strip: mode · risk · command · system health · user (from M1.3 + Day-2 health endpoints).
- Bottom dock: events / orders / positions / alerts / system / activity / terminal.
- **Narrow collapsing left nav** (operational modes, not “Dashboard/Settings”); collapsed = glyph rail, hover expands.
- Visual base: `#070A14` · 1px `rgba(255,255,255,.08)` borders · `backdrop-blur(16px)` · Inter 800 + JetBrains Mono 11px `.14em`.
- **Risk color only:** cyan = demo · red = live · amber = armed-but-paused / warning · green = healthy / auto ON · purple = AI · blue = information. Mostly neutral; semantic color only when it means something.
- **Panels > cards.** Cards only for real conceptual objects (TradePlan, Signal, Alert). Prefer dividers, dense tables, overlays, inspectors, split panes.
- Motion only for state: market ticks, new signals, gate changes, command exec, order lifecycle, AI steps, alerts.

### Left nav — operational modes (examples)

```text
◉ OVERVIEW
MARKETS  Markets · Watchlists · Screener · Heatmap
RESEARCH Research · AI Analyst · News · Calendar · Signals
TRADING  Trade · Positions · Orders · Strategies · Algorithms
RISK     Monitor · Exposure · Scenarios · Limits
SYSTEM   Models · Data · Agents · Automations · Logs
CONFIG   Workspace · Trading · Risk · AI · Integrations · System
```

Keys: `Cmd+K` command · `1-6` nav groups · feature keys (`G`/`L`/`N`/`A`) as in REV1 chart/breaker notes.

---

## 3. Command Center (front door)

Not `🔍 Search…` — a **command engine**:

```text
› Analyze BTC against NASDAQ for the last 90 days
```

- Sections: COMMAND · DATA · ACTION · AI (chip results, mono).
- Understands aliases: `BTC` / `BTC/USD` / `Bitcoin` / `bitcoin price`.
- Command language **and** NL both hit the same engine:

```text
/analyze BTC   ·  /compare BTC ETH  ·  /show risk  ·  /open EURUSD
/find momentum opportunities  ·  /explain today's market move
/run strategy backtest  ·  /show my exposure  ·  /why did this signal trigger?
/simulate 2% portfolio allocation
› Why is oil moving today?          # same engine as /research crude
```

- Day-1 frontend: palette shell + routing to real endpoints; full engine = M10.3 command layer (backend) — frontend never invents command results.

---

## 4. Environments (10 operating modes — one workstation)

```text
01  HOME / COMMAND CENTER
02  MARKET WORKSTATION          (chart heart)
03  RESEARCH WORKSTATION
04  AI INTELLIGENCE CENTER
05  SIGNAL / RECOMMENDATION CENTER
06  NEWS / EVENT INTELLIGENCE
07  TRADING & EXECUTION
08  PORTFOLIO & RISK
09  STRATEGY / ALGORITHM LAB
10  SYSTEM CONTROL CENTER
```

These are **modes of one shell**, not ten websites. Workspace memory: layouts, panels, assets, filters, AI context, chart settings, alerts, saved commands persist per named workspace (`Morning Research`, `FX Desk`, `Risk Monitoring`, …).

**Contexts, not pages:** `OBJECT → CONTEXT → WORKSPACE`  
Select **EUR/USD** → context graph (chart, technical, fundamentals, news, AI, signals, risk, orders, history) can appear in any workspace.

**Trade mode ladder** (top-level UI emphasis): `RESEARCH → SIMULATION → PAPER → LIVE` — **LIVE is unmistakably different** (persistent safety state: broker / risk / execution / kill). Maps to M1.3 gates: MODE+AUTO breakers already live in `features/account_mode/`.

### 4.0 MODE vs AUTO (REV2.3 — freeze semantics)

```text
MODE  (account/M1.3 gate 1)          AUTO  (bot enable/M1.3 gate 2)
 ├─ RESEARCH / SIMULATION            ├─ OFF → automated execution disabled;
 ├─ PAPER / DEMO                       │        **manual execution may remain available**
 └─ LIVE                             └─ ON  → automated execution may operate
                                              **if all other gates permit**

MODE + AUTO ≠ complete execution authorization.
Risk · kill switch · broker/execution state · permissions · armed-live still apply.
Frontend must never treat AUTO=ON alone as “system may trade.”
```

Switching MODE never implies AUTO; switching AUTO never implies MODE (M1.3 contract).

### 4.1 Manual Trade Session (REV2.3 — freeze — primary manual path when AUTO is OFF)

> **Manual Trade Session:** When **AUTO is OFF**, both Demo/Paper and Live environments expose a **manual trade session panel**. The panel accepts only the minimum execution inputs required by the applicable order contract (instrument, side, order type, quantity/lot size where applicable, entry parameters, SL, TP, and required execution fields). Optional calculations such as pip distance, estimated monetary risk, exposure, potential P&L, and R:R may be displayed when reliable **backend contract** data is available (§0.2). **Calculation failure must never block valid manual execution.** Manual execution must use the **same backend validation/execution contracts** as other orders and must **not** depend on the AUTO-trading configuration.

> **AUTO OFF does not mean trading UI disappears.** It means automated strategy execution is disabled; **manual execution remains available** subject to MODE, risk, execution, and kill controls.

**LIVE manual orders:** require an **explicit final execution confirmation** showing the **effective order parameters** and **applicable risk state** before submission (final inspection point — not a second global breaker).

---

## 5. Chart — analytical canvas

- Header: instrument · interval · LIVE · change · signal badge.
- Toolbar: Indicators | Draw | Compare | AI | Events | Models | Layout.
- Lake proof footer (REV1 lock): parquet path + `reference_snapshot_id` — **provenance before price**.
- Region select → AI verbs: *Explain this move* · *Find similar structures* · *What changed here?* · *Show news here* → Chart → Data → AI → News → Research → Decision → Execution in one surface.
- `lightweight-charts` 60fps; WS ticks not fetch spam; dual clock UTC + Lagos; `GAP_AFFECTED` amber treatment (REV1).

---

## 6. AI Intelligence Workspace (not ChatGPT)

Structured intelligence, not conversational filler:

```text
QUESTION → MARKET CONTEXT → TECHNICAL → FUNDAMENTAL → NEWS
→ ASSESSMENT → CONFIDENCE/EVIDENCE bar → [Challenge] [Deep Research] [Create Alert]
```

**Every conclusion has an evidence trail** (click to expand):

```text
Momentum deterioration detected
WHY?
✓ RSI divergence · volume contraction · regime change · DXY corr · 3 news
Evidence → Technical model | Regime | News | Correlation | Historical analogue
```

AI provenance footer mandatory: `prompt v · model · data cut UTC`.  
**AI may manipulate the workspace** (Day-2): e.g. build BTC/ETH split + correlation + analysis panel automatically after a compare request.

---

## 7. Signals — Signal Intelligence Board (not “BUY”)

Cards show *why*: direction, strength, confidence `0.82` mono, evidence count, risk, horizon, model agreement, regime, **invalidation condition**.  
Multi-factor bars (Technical / Fundamental / Sentiment / Risk). Actions: Investigate | Chart | Simulate.  
Contradiction banners never hidden. Approve path → M5 → M6 gated by MODE+AUTO + Day-2 risk (frontend does not invent `risk_ok`).

---

## 8. News — event intelligence stream

Table lines with impact bars, affected instruments, ingested_at, lineage.  
News → assets → price → volatility → signals linkage. Chart overlay: 1px amber vertical on matching watermark. No image cards.

---

## 9. Risk omnipresent + System state layer

- Portfolio strip always on: exposure, gross/net, VaR, drawdown, risk state.
- Per-trade: position, risk %, stop/target, **portfolio impact** bar.
- Status strip: MARKET · DATA · AI · EXECUTION · RISK · STRATEGIES · ALERTS — click → health drawer with latencies.
- Day-1: show M1.3 gates + kill `n/a` (M5.5); do not fake M5 metrics until APIs exist.

---

## 10. System Control Center + Inspector + density levels

- Config = system state (feeds, latency, health), not checkboxes. GitOps read-only YAML lineage (REV1 §7).
- **Universal Inspector** (right rail): click signal/order/position/AI/model/log → inspect without leaving workspace.
- **Three density levels:** L1 glance (market/risk/signals/alerts/system) → L2 investigate (chart/news/AI/risk) → L3 inspect (model, calc, source, timestamp, evidence, log).

**Wow moment (design target):** open → *“Market environment changed overnight — 3 material developments”* → click `USD regime shift` → system **already organized** charts, news, evidence, models, risk around that context. Not “here is your dashboard.”

---

## 11. Design system = infrastructure

**File layout = infra (§0.1 REV2.2):** **one stack = Vite + React + TypeScript**, feature-modular. Day-1 = multi-file static ES modules (no god HTML) until M10.4 opens; then migrate features to `.tsx` under `features/` + shared `design-system/`.

```text
/frontend
  design-system/
    foundations/  colors · typography · spacing · elevation · motion
    components/   Button · Input · CommandBar · DataTable · Chart · Metric
                  Signal · Alert · Status · Inspector · Panel · SplitPane · Tabs
    patterns/     TradingWorkspace · ResearchWorkspace · AIWorkspace · Risk · System
    layouts/      Terminal · Workspace · Fullscreen · SplitView
  features/<name>/   # self-contained (entry, UI, styles, tests)
  services/          # one domain per file (account_api, market_api, …)
  state/ · realtime/ · types/
```

Each component: purpose, states, variants, interactions, a11y, responsive, **data contract**, error, loading, empty.

**Typography:** modern sans (action) + legible mono (data). Hierarchy over giant type.

---

## 12. Five crisp rules (build checklist — from REV1, still lock)

1. **Keyboard-first:** `Cmd+K`, nav keys, `G/L/N/A` — hint `11px mono`
2. **Mono = truth, Inter = action**
3. **1% color, 99% grid**
4. **Lineage everywhere:** hover/inspector → watermark / REF / `ingested_at`
5. **Performance = crisp:** 60fps charts · virtualized feeds · WS not poll

---

## 13. Backend API touchpoints (M10.4 must use)

| UI surface | Endpoint / contract |
|------------|---------------------|
| Top rail MODE/AUTO/WILL_TRADE | `GET /api/v1/account/status` |
| MODE breaker | `POST /api/v1/account/mode` + `X-ROLE` `account.switch` |
| AUTO breaker | `POST /api/v1/account/auto-trade` + `X-ROLE` `trading.enable` |
| 403/409/422 | map typed errors — **never success toast on non-2xx** |
| Chart/lineage | M2 lake paths + `reference_snapshot_id` when exposed |
| AI / signals / news / risk / health | future M3–M7, M10, M12 contracts — consume, never recompute |
| No backend serving | static only |

**Architecture sketch:**

```text
APPLICATION SHELL
  → COMMAND ENGINE · CONTEXT ENGINE · WORKSPACE ENGINE
  → INTELLIGENCE LAYER (AI · DATA · NEWS · RISK · TRADING)
  → BACKEND SYSTEM (contracts only)
```

---

## 14. Already shipped vs gated

| Piece | Status |
|-------|--------|
| `features/account_mode/` MODE+AUTO breaker panel + guards | Split 2026-09-23 into **multi-file** thin HTML + `styles.css` + `main.js` + `guards.js` + `services/account_api.js` + design-system foundations |
| Full shell / Cmd+K / chart / AI workspace / 10 environments | **Gated** — human opens M10.4 after M2.8 backend slice |
| Design lock | **DONE 2026-09-23** — human `lgtm frontend spec` froze **REV2.3** (§0.1 + §0.2 + §4.0/§4.1 + Day-1 left rail QUANTRADER / Role) |

Related: `docs/specs/m1.3_account_mode_two_gate.spec.md` · `frontend/README.md` · `BUILD_STATUS.md` · `USER_ACTIONS.md`
