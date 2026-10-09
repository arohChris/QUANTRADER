"""
M2.6 Gap Calendar - Step 3 tests, written AGAINST docs/specs/m2.6_gap_calendar.spec.md.

Authority model (spec 3a): THE BROKER IS AUTHORITATIVE. The configured calendar is a
fallback for when the broker cannot be verified, and the asset-class default is the last
resort. A broker ERROR is UNKNOWN, never CLOSED - conflating "I could not ask" with "there
is nothing there" is the most dangerous bug available in this module.

The load-bearing tests are at the bottom: the weekend property sweep, and the fallback
cascade. Both are what a naive detector gets wrong.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from modules.m2_market_data.coverage import (
    CoverageVerdict,
    GapLedger,
    GapRun,
    GapState,
    SessionEvidence,
    classify_window,
    coverage_of,
    merge_runs,
)
from modules.reference.calendar import load_calendar
from modules.reference.reference import UnknownInstrumentError

# WHY a FIXED past week, not a live-relative one: AGENTS.md 3.5 forbids time-dependent tests.
# An earlier revision walked the week containing "now", so the sweep ran into the future and
# failed on any day before Sunday 21:00. Windows here are all safely historical.
MON = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)          # Monday
# CORRECTED 2026-09-29 to the venue`s published hours (trading_times): the week runs
# Mon 00:00Z -> Fri 20:55Z and BOTH weekend days are shut. These used to be Fri 21:00 and
# Sun 21:00, which the venue contradicts at both ends.
#
# FRI_CLOSE is 20:56, not 20:55, and the reason is measured: the venue publishes close
# "20:55:00" as the last trade instant, but the store holds a 1m bar with interval_start
# 20:55, so the minute 20:55-20:56 is tradable. 20:56 is the first minute that is SHUT,
# which is what a "from here on everything is closed" window has to start at.
FRI_CLOSE = datetime(2026, 9, 18, 20, 56, tzinfo=UTC)   # Friday 20:56 UTC, first shut minute
SAT = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
WEEK_OPEN = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)    # Monday 00:00 UTC
SUN = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)         # Sunday midday - shut all day
THU = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)         # an ordinary Thursday

FOREX, CRYPTO, METAL = "FX.EURUSD", "CRYPTO.BTCUSD", "COMMODITY.XAUUSD"
DERIV = "deriv"


class BrokerSaysClosed:
    """Authoritative broker: 'this instrument was shut for the whole window.'"""

    def evidence(self, instrument_id: str, broker: str, start: datetime, end: datetime):
        return [(start, end, SessionEvidence.BROKER, False)]


class BrokerSaysOpen:
    def evidence(self, instrument_id: str, broker: str, start: datetime, end: datetime):
        return [(start, end, SessionEvidence.BROKER, True)]


class BrokerUnreachable:
    """Broker could not be asked. This is UNKNOWN - it is NOT 'shut'."""

    def evidence(self, instrument_id: str, broker: str, start: datetime, end: datetime):
        return [(start, end, SessionEvidence.UNKNOWN, False)]


def _has(rows: list[GapRun], state: GapState) -> bool:
    return any(r.state is state for r in rows)


def _minutes(rows: list[GapRun]) -> int:
    return sum(r.minutes() for r in rows)


# ============================================================ AUTHORITY (spec 3a)
def test_broker_closed_overrides_calendar_open() -> None:
    """Broker is authoritative: even inside normal session hours, a broker 'shut' wins."""
    rows = classify_window(FOREX, DERIV, MON, MON + timedelta(hours=2), oracle=BrokerSaysClosed())
    assert not _has(rows, GapState.FILLING)
    assert _has(rows, GapState.CLOSED)
    assert all(r.evidence is SessionEvidence.BROKER for r in rows)


def test_broker_open_overrides_calendar_closed() -> None:
    """A venue outage on a Sunday is REAL data loss, not a closure - calendar must lose."""
    rows = classify_window(FOREX, DERIV, SAT, SAT + timedelta(hours=2), oracle=BrokerSaysOpen())
    assert _has(rows, GapState.FILLING), "broker says it traded; that is a real gap"
    assert all(r.evidence is SessionEvidence.BROKER for r in rows)


def test_calendar_used_only_when_broker_unverifiable() -> None:
    """Fallback to config happens, and is LABELLED as calendar-derived, never as broker."""
    rows = classify_window(FOREX, DERIV, SAT, SAT + timedelta(hours=2), oracle=BrokerUnreachable())
    assert _has(rows, GapState.CLOSED), "calendar knows Saturday is shut"
    assert all(r.evidence is SessionEvidence.CALENDAR for r in rows)


def test_broker_error_is_never_treated_as_closed() -> None:
    """THE critical safety test. An unreachable broker must not be laundered into 'shut'."""
    rows = classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=1), oracle=BrokerUnreachable())
    assert not _has(rows, GapState.CLOSED), "an unreachable broker is UNKNOWN, never CLOSED"
    assert _has(rows, GapState.FILLING), "must fall back to a candidate gap, not to silence"


def test_unknown_evidence_is_flagged_for_verification() -> None:
    rows = classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=1), oracle=BrokerUnreachable())
    assert all(r.needs_verification for r in rows), "unconfirmed gap must be escalated"


def test_broker_confirmed_gap_is_not_flagged_for_verification() -> None:
    rows = classify_window(FOREX, DERIV, MON, MON + timedelta(hours=1), oracle=BrokerSaysOpen())
    assert all(not r.needs_verification for r in rows), "broker already confirmed it"


def test_asset_class_default_is_the_last_resort() -> None:
    """With an unverifiable broker AND an unusable calendar, fall back to the asset class
    assumption - which assumes OPEN, because assuming 'shut' with no evidence is the one
    unrecoverable error."""

    class BrokenCalendar:
        def open_intervals(self, *a, **k):
            raise RuntimeError("reference data unavailable")

    rows = classify_window(FOREX, DERIV, MON, MON + timedelta(hours=1),
                           oracle=BrokerUnreachable(), calendar=BrokenCalendar())
    assert all(r.evidence is SessionEvidence.DEFAULT for r in rows)
    assert _has(rows, GapState.FILLING), "no evidence at all must still yield a candidate gap"


# ============================================================ closed vs gap
def test_weekend_window_on_forex_is_closed_not_a_gap() -> None:
    rows = classify_window(FOREX, DERIV, SAT, SAT + timedelta(hours=6))
    assert not _has(rows, GapState.FILLING), "a weekend is not lost data"


def test_friday_close_to_monday_open_emits_no_gap() -> None:
    """The exact case that breaks a naive detector: the whole weekend, entirely shut.

    Renamed 2026-09-29 - it said "Sunday open" because the old calendar opened Sunday 21:00.
    The venue opens Monday 00:00Z, so the shut run is Friday close -> Monday open."""
    rows = classify_window(FOREX, DERIV, FRI_CLOSE, WEEK_OPEN)
    assert not _has(rows, GapState.FILLING)


def test_crypto_weekend_is_still_a_gap() -> None:
    rows = classify_window(CRYPTO, DERIV, SAT, SAT + timedelta(hours=6))
    assert _has(rows, GapState.FILLING), "crypto trades 24/7; a weekend is real loss"


def test_sunday_evening_is_open_for_forex() -> None:
    """Regression for a real defect found 2026-09-27: the session OPENS Sunday 21:00, and a
    single midday probe classified 21:00-24:00 as shut - hiding 3h of real trading."""
    rows = classify_window(FOREX, DERIV, WEEK_OPEN, WEEK_OPEN + timedelta(hours=3))
    assert _has(rows, GapState.FILLING), "forex IS open Sunday 21:00 UTC"


def test_contiguous_hole_is_one_run() -> None:
    rows = classify_window(CRYPTO, DERIV, MON, MON + timedelta(minutes=10))
    filling = [r for r in rows if r.state is GapState.FILLING]
    assert len(filling) == 1, f"a contiguous hole must be ONE run, got {filling}"


def test_runs_never_span_a_closure() -> None:
    rows = classify_window(FOREX, DERIV, FRI_CLOSE, WEEK_OPEN + timedelta(hours=2))
    for r in rows:
        if r.state is GapState.FILLING:
            assert (r.end - r.start) < timedelta(hours=30), f"run spans closure: {r}"


def test_merges_touching_and_overlapping_runs() -> None:
    a = GapRun("FX.EURUSD", "deriv", MON, MON + timedelta(minutes=5), GapState.FILLING)
    b = GapRun("FX.EURUSD", "deriv", MON + timedelta(minutes=5), MON + timedelta(minutes=9),
               GapState.FILLING)
    assert len(merge_runs([a, b])) == 1


def test_disjoint_runs_stay_disjoint() -> None:
    a = GapRun("CRYPTO.BTCUSD", "deriv", MON, MON + timedelta(minutes=5), GapState.FILLING)
    b = GapRun("CRYPTO.BTCUSD", "deriv", MON + timedelta(minutes=30), MON + timedelta(minutes=35),
               GapState.FILLING)
    assert len(merge_runs([a, b])) == 2


# ============================================================ permanent
def test_permanent_gap_is_never_requeued() -> None:
    ledger = GapLedger()
    run = GapRun(CRYPTO, DERIV, MON, MON + timedelta(minutes=30), GapState.PERMANENTLY_MISSING)
    assert ledger.record_permanent([run], reason="venue has no data") == 1
    pending = ledger.enqueueable(classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=1)))
    assert all(r.state is not GapState.FILLING for r in pending)


def test_recording_twice_is_idempotent() -> None:
    ledger = GapLedger()
    run = GapRun(CRYPTO, DERIV, MON, MON + timedelta(minutes=10), GapState.PERMANENTLY_MISSING)
    assert ledger.record_permanent([run], reason="x") == 1
    assert ledger.record_permanent([run], reason="x") == 0
    assert len(ledger.permanent_runs(CRYPTO, DERIV)) == 1


def test_learned_closure_is_recorded_from_broker_silence() -> None:
    """A well-formed broker response with no bars IS a closure - and it is learnable."""
    ledger = GapLedger()
    rows = classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=2), oracle=BrokerSaysClosed())
    added = ledger.record_learned(rows)
    assert added == 1
    assert ledger.permanent_runs(CRYPTO, DERIV)[0].evidence is SessionEvidence.BROKER


def test_unverified_gap_is_never_learned_as_a_closure() -> None:
    """The counterpart: UNKNOWN must not be laundered into a permanent closure."""
    ledger = GapLedger()
    rows = classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=2), oracle=BrokerUnreachable())
    assert ledger.record_learned(rows) == 0, "cannot learn a closure from an unanswered question"


# ============================================================ window validation
def test_end_before_start_is_rejected() -> None:
    with pytest.raises(ValueError, match="end"):
        classify_window(CRYPTO, DERIV, MON + timedelta(hours=1), MON)


def test_zero_length_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="zero|length|end"):
        classify_window(CRYPTO, DERIV, MON, MON)


def test_future_window_is_rejected() -> None:
    far = datetime(2100, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="future"):
        classify_window(CRYPTO, DERIV, far, far + timedelta(hours=1))


def test_far_past_window_still_classifies() -> None:
    rows = classify_window(CRYPTO, DERIV, datetime(2020, 1, 1, tzinfo=UTC),
                           datetime(2020, 1, 2, tzinfo=UTC))
    assert _has(rows, GapState.FILLING)


def test_unknown_instrument_raises_typed_error() -> None:
    with pytest.raises(UnknownInstrumentError):
        classify_window("FX.NOPE", DERIV, MON, MON + timedelta(hours=1))


def test_unknown_broker_raises_typed_error() -> None:
    with pytest.raises(ValueError, match="broker"):
        classify_window(FOREX, "nope", MON, MON + timedelta(hours=1))


# ============================================================ holidays
def test_holiday_inside_session_hours_is_closed() -> None:
    cal = load_calendar(holidays={FOREX: [THU]})
    rows = classify_window(FOREX, DERIV, THU, THU + timedelta(hours=1), calendar=cal)
    assert not _has(rows, GapState.FILLING), "a holiday is a closure, not lost data"


# ============================================================ coverage verdict
def test_coverage_reports_worst_state() -> None:
    v = coverage_of(FOREX, DERIV, MON, MON + timedelta(hours=4),
                    present=lambda a, b: [a],
                    missing=lambda a, b: [(MON + timedelta(hours=1),
                                           MON + timedelta(hours=1, minutes=1))])
    assert isinstance(v, CoverageVerdict)
    assert v.state is GapState.FILLING
    assert not v.can_compute, "a number computed across a hole is the failure we prevent"


def test_rescan_is_idempotent() -> None:
    a = classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=6))
    b = classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=6))
    assert [(r.start, r.end, r.state) for r in a] == [(r.start, r.end, r.state) for r in b]


# ============================================================ adversarial
def test_ADV_ten_year_window_is_bounded() -> None:
    """A ten-year window is ~5.2M minutes. Materialising them does not finish - this is the
    defect that made the first build hang."""
    start, end = datetime(2016, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)
    rows = classify_window(CRYPTO, DERIV, start, end)
    assert len(rows) <= 8, f"run count not bounded: {len(rows)}"
    assert _minutes(rows) == int((end - start).total_seconds() // 60)


def test_ADV_naive_bounds_are_rejected() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        classify_window(CRYPTO, DERIV, datetime(2026, 9, 21), datetime(2026, 9, 22))  # type: ignore[arg-type]  # noqa: DTZ001 - naive ON PURPOSE; the test asserts it is rejected


@pytest.mark.parametrize("bad", ["2026-09-21", 1758422400, None, 3.5, object()])
def test_ADV_non_datetime_bounds_are_a_TypeError(bad: object) -> None:
    """A wrong TYPE and a naive-but-real datetime are different mistakes, so they raise
    different exception types and a caller can tell them apart."""
    with pytest.raises(TypeError, match="must be a datetime"):
        classify_window(CRYPTO, DERIV, bad, MON + timedelta(hours=1))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="must be a datetime"):
        classify_window(CRYPTO, DERIV, MON, bad)  # type: ignore[arg-type]


def test_ADV_known_gap_is_scoped_by_broker() -> None:
    ledger = GapLedger()
    ledger.record_permanent(
        [GapRun(CRYPTO, DERIV, MON, MON + timedelta(minutes=5), GapState.PERMANENTLY_MISSING)],
        reason="x")
    assert ledger.permanent_runs(CRYPTO, "mt5") == []


def test_ADV_absurd_reason_is_bounded() -> None:
    ledger = GapLedger()
    ledger.record_permanent(
        [GapRun(CRYPTO, DERIV, MON, MON + timedelta(minutes=5), GapState.PERMANENTLY_MISSING)],
        reason="x" * 10_000)
    assert len(ledger.permanent_runs(CRYPTO, DERIV)[0].reason) <= 500


def test_ADV_malformed_broker_evidence_is_ignored_not_trusted() -> None:
    """A broker that returns garbage must not be able to invent a closure."""
    class Garbage:
        def evidence(self, instrument_id, broker, start, end):
            return [(start, end, "not-an-enum", "maybe")]

    rows = classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=1), oracle=Garbage())
    assert not _has(rows, GapState.CLOSED), "garbage evidence must not become a closure"
    assert _has(rows, GapState.FILLING)


# ============================================================ property sweep
def test_property_a_gap_is_never_reported_while_the_market_is_shut() -> None:
    """THE regression that matters, stated precisely.

    An earlier revision asserted 'zero gap hours in a week', which was simply false - a trading
    week contains 120+ open hours. The real invariant is two-sided:

      1. every hour reported as a gap is an hour the venue was actually tradable, and
      2. the number of gap hours equals the number of open hours in the window.

    (1) is exactly what a naive detector violates, and (2) catches a detector that silently
    hides real trading.

    CORRECTED 2026-09-29: the two sides used to be counted in DIFFERENT UNITS - gap HOURS on
    one side, `open_minutes(...) // 60` on the other. That was safe only while the week
    closed on an exact hour (21:00), which made the minute count a multiple of 60. The venue
    actually closes Friday at 20:55, so 7015 open minutes floor to 116 while there are 117
    hourly sample points inside them, and the property failed on a rounding artefact that
    had nothing to do with gaps. Both sides are now MINUTES, and there is no rounding left
    to disagree about.
    """
    cal = load_calendar()
    week_end = MON + timedelta(days=7)
    for inst in (FOREX, METAL, CRYPTO):
        expected = cal.open_minutes(inst, MON, week_end)
        gap_minutes, t = 0, MON
        while t < week_end:
            rows = classify_window(inst, DERIV, t, t + timedelta(minutes=1))
            if _has(rows, GapState.FILLING):
                assert cal.is_open(inst, t), (
                    f"{inst}: gap reported at {t.isoformat()} while the market is SHUT - "
                    "closure must never be recorded as data loss"
                )
                # WHY count the open minutes INSIDE the hour rather than the hour itself:
                # the week now shuts Friday 20:55, so the final sampled hour is only 55
                # minutes of tradable time. Counting it as 60 overstates the gap side by 5
                # and the property fails on arithmetic rather than on gaps. Hand check for
                # FX: Mon-Thu 4*1440 = 5760, Fri 00:00-20:00 = 1200, Fri 20:00-20:55 = 55,
                # total 7015 - which is exactly what open_minutes reports.
                gap_minutes += sum(
                    1 for k in range(60) if cal.is_open(inst, t + timedelta(minutes=k))
                )
            t += timedelta(hours=1)
        assert gap_minutes == expected, (
            f"{inst}: {gap_minutes} open minutes reported as gaps but "
            f"{expected} open minutes in the window"
        )


def test_property_weekend_contributes_no_gap_minutes_at_all() -> None:
    """Explicitly: the closed stretch Sat 00:00 -> Sun 21:00 yields zero gap minutes for
    every session-based asset. Crypto is excluded because it genuinely trades then."""
    closed_start = MON + timedelta(days=5)          # Saturday 00:00
    # 45 hours, NOT 2d21h: forex reopens Sunday 21:00, and an over-long window would run on
    # into Monday - a trading day - making the assertion false for the right reason.
    for inst in (FOREX, METAL):
        rows = classify_window(inst, DERIV, closed_start, closed_start + timedelta(hours=45))
        assert _minutes([r for r in rows if r.state is GapState.FILLING]) == 0, (
            f"{inst}: a shut weekend must contribute zero gap minutes"
        )
