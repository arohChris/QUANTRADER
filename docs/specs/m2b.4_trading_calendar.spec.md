# Spec: M2b.4 Trading Calendar — effective-dated sessions, holidays, DST-safe

> **Status:** DRAFT — awaiting human review. No implementation before tests are approved
> (`AGENTS.md` §2 Steps 1–4).
>
> **Authority:** `AGENTS.md`, `02b_M2b_Reference.md:146,219` (bible already specifies
> `TradingCalendar (effective-dated sessions/holidays/DST)` and `is_open(...)`; **never
> built**), `BUILD_STATUS.md`.
>
> **Naming law:** `m2b.4_trading_calendar.spec.md` — two levels, per `scripts/check_naming.py`.

---

## 1. Purpose

Answer, synchronously and deterministically, **"was this instrument tradable at this
instant?"** — so that a missing candle can be classified as *data loss* versus *legitimate
market closure*.

## 2. Why this exists (the failure it prevents)

M2.6 gap detection cannot distinguish a lost tick from a closed market. With a 25-asset
mixed universe this is not theoretical — measured live on 2026-09-27:

```
crypto (BTC, ETH, ...)   exchange_is_open = 1
forex  (all 25)          exchange_is_open = 0
commodity (all 4)        exchange_is_open = 0
```

Without a calendar, every weekend reads as ~17 hours of lost data per instrument, and the
gap detector would either block calculation forever or record permanent false gaps.

## 3. Scope

**In:** `is_open`, `next_open`, `session_for`, effective-dated session rules, holiday
exceptions, per-asset-class defaults with per-instrument override, the broker's live
`exchange_is_open` cross-check.

**Out:** holiday calendars auto-derived from the broker (that is a reconciliation concern,
not a reference fact); intraday bar boundaries (M2.4 owns those); any trading decision.

## 4. Signatures

```python
def is_open(instrument_id: str, at: datetime, broker: str = "deriv") -> bool
def next_open(instrument_id: str, at: datetime, broker: str = "deriv") -> datetime
def session_for(instrument_id: str, at: datetime, broker: str = "deriv") -> SessionRule
def broker_agrees(instrument_id: str, at: datetime, broker_open: bool) -> None | str
```

All **synchronous, deterministic, read-only** — never `async`, never I/O beyond the first
cached YAML load. The bible requires runtime lookups `<1µs` because they sit in the M2.2
per-tick and M5 per-risk hot paths (`02b_M2b_Reference.md:171-176`).

## 5. Invariants

1. **`at` must be tz-aware UTC.** A naive datetime is **rejected**, never assumed. `AGENTS.md` §3.8
2. **All session boundaries are defined in UTC.** See §7 — this is the DST-safe choice.
3. **Unknown instrument → typed error**, never "assume open" and never "assume closed".
   Failing open would let a strategy trade an asset whose hours we do not know.
4. **Unknown broker → typed error.**
5. `is_open` is **pure** — no clock read, no global state mutation. `at` is always passed in.
6. **Effective-dated.** A historical lookup at `at` uses the rule in force at `at`, not today's
   rule. This is the same `effective_at` discipline as `get_spec` (`02b_M2b_Reference.md`).
7. `next_open(at) > at` strictly, and is always a real open boundary — never `at` itself.

## 6. Session model

| Asset class | Rule | Rationale |
|---|---|---|
| `crypto` | `always_open: true` | trades 24/7; **any** missing minute is real data loss |
| `forex` | open Sun 21:00 UTC → close Fri 21:00 UTC, closed Sat + Sun<21:00 | standard week |
| `commodity` | open Sun 22:00 UTC → close Fri 21:00 UTC | metals settle later than FX |

Per-instrument override wins over the asset-class default. Holidays are an explicit,
**effective-dated** list — never inferred, because a guessed holiday silently invents data.

## 7. DST — the decision and why

`AGENTS.md` §3.8 requires DST correctness, naming `2026-03-29 Europe/London`.

**Decision: session boundaries are stored and evaluated in UTC only.**

A session boundary is a *moment*, not a wall-clock label. `Fri 21:00 UTC` is unambiguous
forever; `Fri 21:00 London` is a different instant before and after the transition. Storing
UTC removes the class of bug entirely rather than handling it.

**Trade-off, stated honestly:** if a future requirement needs a session expressed in
*local* time (e.g. "London open"), that needs a tz-aware, DST-correct local→UTC conversion,
and it must be added then — with its own tests for both sides of 2026-03-29. This spec does
not build that. It is not needed by anything we own today.

## 8. Edge cases

| # | Case | Required behaviour |
|---|---|---|
| E1 | crypto at any instant, incl. 03:00 Sunday | `True` |
| E2 | forex Sat 12:00 UTC | `False` |
| E3 | forex Sun 20:59 UTC | `False` |
| E4 | forex Sun 21:00 UTC | `True` — boundary is inclusive-open |
| E5 | forex Fri 21:00 UTC | `False` — boundary is exclusive-close |
| E6 | exact close instant (Fri 21:00:00.000000) | `False` |
| E7 | naive `at` | `ValueError` |
| E8 | `at` in a non-UTC tz | converted, instant preserved |
| E9 | unknown instrument | `UnknownInstrumentError` |
| E10 | unknown broker | typed error |
| E11 | holiday listed for that date | `False` even inside session hours |
| E12 | holiday on a day the market is already shut | `False`, no error |
| E13 | `next_open` during an open session | next boundary strictly after `at` |
| E14 | `next_open` at a boundary | the **following** boundary, never `at` |
| E15 | `next_open` spanning a holiday | skips it, returns the next real open |
| E16 | instrument with an override | override wins over class default |
| E17 | effective-dated rule changed before `at` | the older rule applies |
| E18 | `at` far in the past (2020) | uses the earliest rule in force |
| E19 | `broker_agrees` with broker open, calendar closed | returns a discrepancy string, **never** overrides the calendar silently |
| E20 | repeated calls, same args | identical result, no cache mutation |

## 9. Adversarial — not in §8, must fail safe

| Input | Must |
|---|---|
| `at` = year 9999 | `ValueError` — a session lookup that cannot terminate is a hang |
| `at` = year 1970 | valid if tz-aware; the rule in force then applies |
| `instrument_id` = `../../etc/passwd` | `UnknownInstrumentError`, no filesystem access |
| holiday entry with `close > open` | config **rejected at load**, not silently swapped |
| holiday `date` malformed | config **rejected at load** |
| session window with `open == close` | config **rejected at load** (would mean 24h or 0h, ambiguous) |
| overlapping holiday + session | holiday wins (closure beats session) |
| `next_open` with no future boundary within 7 days | `ValueError`, never an infinite loop |

## 10. Definition of correct

- For a 25-asset mixed universe, `is_open` agrees with the venue's own `exchange_is_open`
  at every sampled instant. `broker_agrees` exists precisely to measure this.
- A crypto minute with no candle is classified **data loss**; a forex Sunday is classified
  **closed**. Both from the same function.
- The `2026-03-29 Europe/London` transition changes **no** result, because boundaries are UTC.
- A historical backtest at `at=2026-03-15` uses the rule in force then, not today's.

## 11. Tests required before implementation

In `tests/m2b/test_m2b.4_spec.py`:

- one test per §8 edge, named after the case;
- the §9 adversarial cases, each asserting a specific raised type;
- **property test:** across 25 assets × 7 days × hourly sampling, `is_open` never raises and
  never returns a non-bool;
- a cross-check against the broker's published `exchange_is_open` (live, recorded as
  Step 7 evidence, not a unit test — it must not make the suite depend on the network);
- a determinism test: same input twice, same output, cache unmutated.

## 12. Line budget

Target ≤200 lines implementation. Over-budget is a `§3.14` deviation, flagged before it is
accepted, not after.

## 13. Open questions

1. **Holiday source.** Deriv does not publish a session calendar on the public market-data
   endpoint. Options: (a) a maintained `config/calendars/*.yaml` list, (b) learn closures
   empirically from the broker and record them, (c) both — a configured list that
   reconciliation can extend. **Recommend (c)**, because (a) alone rots and (b) alone
   misclassifies the first outage after any closure.
2. **Config granularity.** Class defaults with per-instrument override (recommended), or an
   explicit entry for all 25 assets? Defaults keep 25 assets to ~40 lines and make the
   override path the tested one.
