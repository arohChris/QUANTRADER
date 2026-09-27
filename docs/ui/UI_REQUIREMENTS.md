# UI Requirements Register

> **Purpose (human directive 2026-09-27):** *"everything must also have a UI entry, even if
> role-based. Always take notes when you see something that needs a UI — document it for later
> reference when you start UI work."*
>
> **Rule this file enforces:** a capability that a human must *see or operate* gets a row here
> **at the moment it is built**, not remembered later. Every row is role-gated, because a
> trading surface is not a read-only dashboard — it can move money.
>
> **Status:** UI work has not started. This is a requirements capture, not a design.
> Nothing here is built.

## Access model (applies to every row)

| Role | Sees | Operates |
|---|---|---|
| `viewer` | public/reference data only | **nothing** — unauthenticated, so no control authority at all |
| `trader` | own accounts, positions, PnL, market state | signals, orders, `AUTO`, `MODE` |
| `admin` | everything, incl. all users, config, audit | kill switches, circuit breakers, config, asset master |

**Non-negotiable for every control below:** the browser never talks to a store, never imports
backend Python, and never computes money. It calls the API gateway
(`financial_analyst_app/modules/experience/api/v1/`) and renders the envelope. A UI action
that can change risk state must be **structurally** refused server-side when the role or the
kill state forbids it — hiding a button is not enforcement.

---

## 1. Safety / kill switches — M5

The single most important surface. Standard: the operator must be able to stop trading and
know that they did.

| # | Requirement | Role | Notes |
|---|---|---|---|
| 1.1 | **Master ARM indicator**, impossible to miss when not armed | viewer+ | Live armed must be visually dominant, not a subtle badge |
| 1.2 | `AUTO` ON/OFF with current state unmistakable | trader | Trader may toggle. Rejected while holding a position — show *why* |
| 1.3 | `MODE` live/demo, both directions, with a confirm step for → live | trader | Armed state must be restated in the confirm dialog |
| 1.4 | **Five-scope kill switches:** platform / broker / **asset** / user / user_asset | admin | Per human directive 2026-09-27. Asset scope exists so bad Gold data does not stop Bitcoin trading |
| 1.5 | Active halts list — scope, reason, who set it, when | trader (own) + admin (all) | Latching state, not a transient flash |
| 1.6 | Manual **reset** with reason capture | admin | Never self-clears; never auto-resumes on restart |
| 1.7 | Circuit-breaker panel: threshold vs current, per rule | admin | Thresholds configurable; every tick evaluated |

## 2. Asset universe & class standard — M2b.5

| # | Requirement | Role | Notes |
|---|---|---|---|
| 2.1 | Asset list grouped by class, showing **inherited** vs **overridden** fields | admin | Must make inheritance visible or an operator cannot tell why Gold and Silver differ |
| 2.2 | Per-asset session hours + next open/close, with source badge (`config` vs `broker`) | trader | Disagreement between calendar and broker must be **surfaced**, never hidden |
| 2.3 | Holiday calendar view per class | trader | Show whether a closure is configured or learned |
| 2.4 | Add/remove an asset: dry-run showing exactly which fields are inherited | admin | "One edit" is a promise the UI should make visible |
| 2.5 | **Orphan rule report** — a rule pointing at a removed instrument | admin | From `list_orphans`; removal is the dangerous direction |
| 2.6 | Config validation errors with file + class + key | admin | Boot fails closed; the UI is where a human fixes it |

## 3. Data quality & gaps — M2.6

| # | Requirement | Role | Notes |
|---|---|---|---|
| 3.1 | Coverage heatmap per instrument over time | trader | Must distinguish `CLOSED` from `FILLING` — conflating them is the bug this slice exists to prevent |
| 3.2 | Gap list with state, **evidence source**, and `needs_verification` flag | admin | A gap we could not confirm is a different animal from a confirmed one |
| 3.4 | Known-impossible windows (`PERMANENTLY_MISSING`) with reason + who learned it | admin | Terminal state; reset is an explicit action |
| 3.5 | Live-vs-repair indicator: is the broker currently verifiable? | admin | Drives whether gaps are trustworthy right now |

## 4. Backfill & reconciliation — M2.7 / M12

| # | Requirement | Role | Notes |
|---|---|---|---|
| 4.1 | Backfill queue: window, state, attempts, last error | admin | Bounded retries visible, so a retry storm is obvious |
| 4.2 | Manual "repair this window" with cost estimate | admin | The 1001-minute broker cap must be visible before it is spent |
| 4.3 | Reconciliation report: local vs broker state divergence | admin | Per §3.9, never assume local is accurate |
| 4.4 | Watermark / lag per instrument, with staleness alarm | trader | The 10h-stale BTC case must be loud |

## 5. Market data & analytics — M2 / M3

| # | Requirement | Role | Notes |
|---|---|---|---|
| 5.1 | Candle + indicator chart with quality overlay | trader | `gapped` / `suspect` must render differently from clean data |
| 5.2 | Signal list with state: `EXPIRED` / `CANCELLED` / `BLOCKED` distinct from trade losses | trader | §3.4: a blocked signal is not a losing trade |
| 5.3 | Strategy status: shadow / dry-run / live, per strategy | trader | First deployment is always shadow (§3.10) |
| 5.4 | Feature/capability availability per asset | trader | Makes `UNAVAILABLE` legible instead of mysterious |

## 6. Accounts, orders, ledger — M6 / M7

| # | Requirement | Role | Notes |
|---|---|---|---|
| 6.1 | Positions + open risk, per `(user, broker)` | trader (own) | Cross-strategy risk must be visible in one place |
| 6.2 | Order lifecycle timeline with `correlation_id` | trader (own) + admin | `pending → acked → partially_filled → filled \| rejected \| cancelled` |
| 6.3 | Ledger with append-only indicator; PnL to the cent | trader (own) | `Decimal`, never float, all the way to the screen |
| 6.4 | Idempotency/conflict surface for retried orders | admin | At-least-once delivery must be visible, not hidden |

## 7. Audit & operations — M12

| # | Requirement | Role | Notes |
|---|---|---|---|
| 7.1 | Correlation trace: `tick → signal → risk → order → fill → ledger` | admin | §3.12: reconstruct why a trade happened without asking anyone |
| 7.2 | Config change history + rollback (`config_versions`) | admin | Reversibility is a §12 gate, so it must be operable |
| 7.3 | Health: ingest lag, Timescale/Valkey status, broker connectivity | admin | |
| 7.4 | Live/decision logs (ticks-only WS, never raw) | trader | §10/§24 retention: no per-tick analytics retention |

---

## Open questions for when UI starts

1. **Framework** — the legacy vanilla account page was deleted (architecture locks in
   `frontend/tests/test_frontend_architecture_locks.py`). A component framework is still an
   open decision; the gateway contract must not be designed around it.
2. **Auth before or after first screen?** No production `Principal`/OIDC exists yet. Every
   row above says "role", but roles are only meaningful once identity is real.
3. **Charting** — heavy dependency, or render server-side? Affects the analytics payload and
   the `<20ms` analytics budget if done naively.
4. **Which of these is genuinely MVP?** Rows 1.1–1.4 (kill switches) are the ones a
   professional would call non-negotiable; the rest can sequence behind them.
