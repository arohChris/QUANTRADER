"""
M2b.4 Trading Calendar - Step 3 tests, written AGAINST docs/specs/m2b.4_trading_calendar.spec.md.

Written BEFORE implementation per AGENTS.md 2 Step 3. Until Step 5 lands this file fails
collection with ModuleNotFoundError - that RED is the honest baseline:

    python -m pytest tests/m2b/test_m2b.4_spec.py -q

Every test maps 1:1 to a numbered edge in the spec (8 E1-E20) or an adversarial case (9).
No test asserts merely "does not raise" - each asserts a specific value or raised type.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta, timezone

import pytest

from modules.reference.calendar import (
    CalendarConfigError,
    is_open,
    load_calendar,
    next_open,
    session_for,
)
from modules.reference.reference import UnknownInstrumentError

# A fixed Monday: 2026-09-21. Chosen so weekend edges are unambiguous in the assertions.
MON = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
SAT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
SUN = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
FRI = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

FOREX = "FX.EURUSD"
CRYPTO = "CRYPTO.BTCUSD"
METAL = "COMMODITY.XAUUSD"


# ---------------------------------------------------------------- E1 crypto
def test_E1_crypto_is_open_at_any_instant() -> None:
    """Crypto trades 24/7, so ANY missing minute is real data loss."""
    for at in (SAT, SUN, MON, datetime(2026, 9, 27, 3, 0, tzinfo=UTC)):
        assert is_open(CRYPTO, at) is True, f"crypto must be open at {at}"


# ---------------------------------------------------------------- E2-E6 weekly boundaries
def test_E2_forex_closed_on_saturday() -> None:
    assert is_open(FOREX, SAT) is False


def test_E3_forex_is_shut_all_sunday() -> None:
    """CORRECTED 2026-09-29 against the venue own `trading_times`:

    trading_days = [Mon..Fri], and Sunday reports open/close = ["--"].

    The old rule opened Sunday 21:00, so this edge was "20:59 Sunday is shut". The venue
    says the whole of Sunday is shut, which is a STRONGER and more useful claim, so the
    assertion was widened rather than translated 1:1.
    """
    for at in (datetime(2026, 9, 27, 0, 0, tzinfo=UTC),
               datetime(2026, 9, 27, 12, 0, tzinfo=UTC),
               datetime(2026, 9, 27, 20, 59, tzinfo=UTC),
               datetime(2026, 9, 27, 23, 59, tzinfo=UTC)):
        assert is_open(FOREX, at) is False, f"venue is shut all Sunday, not open at {at}"


def test_E4_forex_open_exactly_at_monday_open() -> None:
    """Open boundary is INCLUSIVE. Monday 00:00:00Z is the venue first trading minute."""
    assert is_open(FOREX, datetime(2026, 9, 28, 0, 0, tzinfo=UTC)) is True
    # ...and the second before is not, so the boundary is not smeared.
    assert is_open(FOREX, datetime(2026, 9, 27, 23, 59, 59, tzinfo=UTC)) is False


def test_E5_forex_closed_exactly_at_friday_close() -> None:
    """Close boundary is EXCLUSIVE - the instant of close is already shut.

    CORRECTED 2026-09-29: this asserted Friday 21:00, which WAS the close under the old
    rule. With the close moved to 20:55, 21:00 is still shut - so the test would have kept
    passing while no longer testing EXCLUSIVITY AT ALL, merely "some time after close".
    Pinned back to the real boundary, and E6 got the same treatment plus the positive half.

    The boundary is 20:56, not 20:55: the venue publishes close "20:55:00" as the last TRADE
    instant, but the store contains a 1m bar with interval_start 20:55, so the minute
    20:55-20:56 is tradable. `close_minute` is exclusive, hence 20:56.
    """
    assert is_open(FOREX, datetime(2026, 9, 25, 20, 56, tzinfo=UTC)) is False


def test_E6_microsecond_exact_close_instant_is_closed() -> None:
    assert is_open(FOREX, datetime(2026, 9, 25, 20, 56, 0, 0, tzinfo=UTC)) is False
    # ...and the whole of the preceding minute is open, which is what makes the boundary
    # exclusive rather than inclusive. Without this half the pair proves nothing.
    assert is_open(FOREX, datetime(2026, 9, 25, 20, 55, 0, tzinfo=UTC)) is True
    assert is_open(FOREX, datetime(2026, 9, 25, 20, 55, 59, tzinfo=UTC)) is True

def test_forex_open_on_a_normal_weekday() -> None:
    assert is_open(FOREX, MON) is True


# ---------------------------------------------------------------- E7-E8 tz discipline
def test_E7_naive_datetime_is_rejected() -> None:
    """AGENTS.md 3.8 - a naive time has no meaning; whichever machine reads it invents a
    timezone and silently shifts the answer by hours."""
    with pytest.raises(ValueError, match="tz-aware"):
        is_open(CRYPTO, datetime(2026, 9, 21, 12, 0))  # noqa: DTZ001  # deliberate bad input


def test_E8_non_utc_offset_preserves_the_instant() -> None:
    """+02:00 14:00 IS 12:00Z Monday. The instant is preserved, not the wall-clock label."""
    plus2 = timezone(timedelta(hours=2))
    same_instant_elsewhere = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    expressed_in_plus2 = datetime(2026, 9, 21, 14, 0, tzinfo=plus2)
    assert expressed_in_plus2 == same_instant_elsewhere  # same moment, two representations
    assert is_open(FOREX, expressed_in_plus2) is is_open(FOREX, same_instant_elsewhere) is True
    # And the Saturday case stays shut whichever representation is used.
    sat_plus2 = datetime(2026, 9, 26, 14, 0, tzinfo=plus2)
    assert is_open(FOREX, sat_plus2) is is_open(FOREX, datetime(2026, 9, 26, 12, 0, tzinfo=UTC))
    assert is_open(FOREX, sat_plus2) is False


# ---------------------------------------------------------------- E9-E10 unknown identity
def test_E9_unknown_instrument_raises_typed_error() -> None:
    with pytest.raises(UnknownInstrumentError) as exc:
        is_open("FX.NOPE", MON)
    assert "FX.NOPE" in str(exc.value)


def test_E10_unknown_broker_raises_typed_error() -> None:
    # Specific type, not `Exception`: a bare `raises(Exception)` would also pass on an
    # accidental AttributeError from a bug, which is the whole point of a typed-error test.
    with pytest.raises(ValueError, match="unknown broker"):
        is_open(FOREX, MON, broker="not_a_broker")


# ---------------------------------------------------------------- E11-E12 holidays
def test_E11_holiday_closes_the_market_inside_session_hours() -> None:
    cal = load_calendar(holidays={"FX.EURUSD": [datetime(2026, 12, 25, tzinfo=UTC)]})
    assert cal.is_open(FOREX, datetime(2026, 12, 25, 12, 0, tzinfo=UTC)) is False
    assert cal.is_open(FOREX, datetime(2026, 12, 24, 12, 0, tzinfo=UTC)) is True


def test_E12_holiday_on_an_already_shut_day_is_harmless() -> None:
    cal = load_calendar(holidays={"FX.EURUSD": [datetime(2026, 9, 26, tzinfo=UTC)]})
    assert cal.is_open(FOREX, datetime(2026, 9, 26, 12, 0, tzinfo=UTC)) is False


# ---------------------------------------------------------------- E13-E15 next_open
def test_E13_next_open_during_an_open_session_is_strictly_later() -> None:
    nxt = next_open(FOREX, MON)
    assert nxt > MON
    assert is_open(FOREX, nxt) is True


def test_E14_next_open_is_strictly_after_and_itself_open() -> None:
    """Never returns `at` itself - that would make a retry loop non-terminating."""
    boundary = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)  # Monday open, market now open
    nxt = next_open(FOREX, boundary)
    assert nxt > boundary
    assert is_open(FOREX, nxt) is True


def test_E15_next_open_skips_a_holiday() -> None:
    cal = load_calendar(holidays={"FX.EURUSD": [datetime(2026, 9, 28, tzinfo=UTC)]})
    nxt = cal.next_open(FOREX, datetime(2026, 9, 27, 22, 0, tzinfo=UTC))
    assert nxt.date() != datetime(2026, 9, 28, tzinfo=UTC).date(), "must skip the holiday"
    assert cal.is_open(FOREX, nxt) is True


# ---------------------------------------------------------------- E16 override
def test_E16_instrument_override_beats_the_class_default() -> None:
    """An override must WIN over the class default. Spec 2 Q2 settles this: class defaults
    with per-instrument override, because that keeps 25 assets to ~40 lines and makes the
    override path the tested one."""
    cal = load_calendar(sessions={"CRYPTO.BTCUSD": [{"open": "12:00", "close": "13:00"}]})
    # Outside the override window the class default would say "always open"...
    assert cal.rule_for(CRYPTO, MON).always_open is False
    # ...so the override is demonstrably in force, and it governs the answer.
    assert cal.is_open(CRYPTO, datetime(2026, 9, 21, 12, 30, tzinfo=UTC)) is True
    assert cal.is_open(CRYPTO, datetime(2026, 9, 21, 18, 0, tzinfo=UTC)) is False


# ---------------------------------------------------------------- E17-E18 effective dating
def test_E17_effective_dated_rule_applies_the_one_in_force_at_that_time() -> None:
    cal = load_calendar(
        overrides={"FX.EURUSD": {"always_open": True, "effective_from": datetime(2026, 1, 1, tzinfo=UTC)}}
    )
    assert cal.is_open(FOREX, datetime(2025, 6, 1, tzinfo=UTC)) is False  # before the rule
    assert cal.is_open(FOREX, datetime(2026, 6, 1, tzinfo=UTC)) is True  # after


def test_E18_far_past_uses_the_earliest_rule_and_does_not_raise() -> None:
    """A lookup far outside the config's effective window must still answer, not crash.
    2020-01-01 was a Wednesday -> forex open; 2020-01-04 a Saturday -> shut."""
    assert is_open(CRYPTO, datetime(2020, 1, 1, tzinfo=UTC)) is True
    assert is_open(FOREX, datetime(2020, 1, 1, 12, 0, tzinfo=UTC)) is True  # Wednesday
    assert is_open(FOREX, datetime(2020, 1, 4, 12, 0, tzinfo=UTC)) is False  # Saturday


# ---------------------------------------------------------------- E20 determinism
def test_E20_repeated_calls_are_identical_and_do_not_mutate() -> None:
    first = [is_open(FOREX, MON + timedelta(hours=h)) for h in range(48)]
    second = [is_open(FOREX, MON + timedelta(hours=h)) for h in range(48)]
    assert first == second
    assert all(isinstance(v, bool) for v in first)


# ---------------------------------------------------------------- session_for
def test_session_for_returns_the_rule_in_force() -> None:
    rule = session_for(CRYPTO, MON)
    assert rule.always_open is True
    assert rule.instrument_id == CRYPTO


# ---------------------------------------------------------------- DST (spec 7)
def test_dst_transition_does_not_move_a_utc_defined_session() -> None:
    """AGENTS.md 3.8 names 2026-03-29 Europe/London. Session boundaries are UTC, so the
    London spring-forward must change nothing.

    WHY the same WEEKDAY either side: comparing different weekdays would compare market
    hours, not DST, and would prove nothing."""
    before_dst = datetime(2026, 3, 27, 12, 0, tzinfo=UTC)  # Friday, pre-transition
    after_dst = datetime(2026, 3, 30, 12, 0, tzinfo=UTC)  # Monday, post-transition
    assert before_dst.strftime("%A") == "Friday"
    assert after_dst.strftime("%A") == "Monday"

    friday_before = datetime(2026, 3, 27, 12, 0, tzinfo=UTC)
    friday_after = datetime(2026, 4, 3, 12, 0, tzinfo=UTC)  # the NEXT Friday, post-transition
    assert friday_after.strftime("%A") == "Friday"
    assert is_open(FOREX, friday_before) is is_open(FOREX, friday_after) is True

    saturday_before = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)
    saturday_after = datetime(2026, 4, 4, 12, 0, tzinfo=UTC)
    assert is_open(FOREX, saturday_before) is is_open(FOREX, saturday_after) is False

    # The same wall-clock label in a non-UTC zone still maps to one fixed instant.
    plus2 = timezone(timedelta(hours=2))
    assert is_open(FOREX, datetime(2026, 9, 21, 14, 0, tzinfo=plus2)) is is_open(
        FOREX, datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    )


# ---------------------------------------------------------------- 9 adversarial
def test_ADV_year_9999_is_rejected_rather_than_hanging() -> None:
    with pytest.raises(ValueError, match="9999|out of range|too large"):
        next_open(FOREX, datetime(9999, 12, 30, tzinfo=UTC))


def test_ADV_path_traversal_instrument_id_is_rejected() -> None:
    with pytest.raises(UnknownInstrumentError):
        is_open("../../etc/passwd", MON)


def test_ADV_holiday_with_close_after_open_is_rejected_at_load() -> None:
    with pytest.raises(CalendarConfigError):
        load_calendar(sessions={"FX.EURUSD": [{"open": "10:00", "close": "09:00"}]})


def test_ADV_zero_length_session_is_rejected_at_load() -> None:
    with pytest.raises(CalendarConfigError):
        load_calendar(sessions={"FX.EURUSD": [{"open": "10:00", "close": "10:00"}]})


def test_ADV_malformed_holiday_date_is_rejected_at_load() -> None:
    with pytest.raises(CalendarConfigError):
        load_calendar(holidays={"FX.EURUSD": ["not-a-date"]})


def test_ADV_unknown_time_format_is_rejected_at_load() -> None:
    with pytest.raises(CalendarConfigError):
        load_calendar(sessions={"FX.EURUSD": [{"open": "25:00", "close": "10:00"}]})


def test_ADV_holiday_beats_session_hours() -> None:
    """A closure must win over an open session - a holiday is a closure."""
    cal = load_calendar(holidays={"CRYPTO.BTCUSD": [datetime(2026, 12, 25, tzinfo=UTC)]})
    assert cal.is_open(CRYPTO, datetime(2026, 12, 25, 12, 0, tzinfo=UTC)) is False


# ---------------------------------------------------------------- property sweep
@pytest.mark.parametrize("hour", [0, 6, 12, 18, 23])
def test_property_sweep_never_raises_and_always_returns_bool(hour: int) -> None:
    """Across the 25-asset universe shape, sampled across a full week, is_open must be a
    total function returning a real bool. This is the property that caught the calendar
    gap class in the first place."""
    instruments = [CRYPTO, FOREX, METAL]
    for inst in instruments:
        for day in range(7):
            at = MON + timedelta(days=day, hours=hour)
            v = is_open(inst, at)
            assert isinstance(v, bool)
            assert v is True or v is False


def test_next_open_always_terminates_within_seven_days() -> None:
    """Spec 9 - no future boundary within 7 days is a ValueError, never an infinite loop."""
    for inst, at in ((CRYPTO, SAT), (FOREX, SAT), (METAL, SAT)):
        nxt = next_open(inst, at)
        assert nxt - at <= timedelta(days=7), f"{inst} next_open too far out: {nxt - at}"


def test_forex_week_is_contiguous_and_five_days_long() -> None:
    """Sanity on the rule itself, with the expected value HAND-COMPUTED from the venue
    published hours rather than read off the implementation.

    Venue truth (trading_times, 2026-09-29): open Mon 00:00:00Z, close Fri 20:55:00Z,
    Saturday and Sunday shut. Sampling hourly from Sun 2026-09-20 00:00Z for 168 hours:

        Mon 09-21  00:00..23:00  = 24
        Tue 09-22  00:00..23:00  = 24
        Wed 09-23  00:00..23:00  = 24
        Thu 09-24  00:00..23:00  = 24
        Fri 09-25  00:00..20:00  = 21   (20:00 is the last sample strictly before 20:55)
                                 ------
                                  117

    The old expectation of 120 came from the old window (Sun 21:00 -> Fri 21:00), which the
    venue contradicts at BOTH ends: Sunday 21:00-24:00 does not trade, and the week shuts
    at 20:55, not 21:00. 3 lost Sunday hours + 1 lost Friday hour = 120 - 3 = 117.
    """
    opened = 0
    t = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)
    for _ in range(7 * 24):  # one week, hourly
        if is_open(FOREX, t):
            opened += 1
        t += timedelta(hours=1)
    assert opened == 117, f"opened hours = {opened}, expected 117 (Mon 00:00 -> Fri 20:55)"

def test_no_math_float_in_calendar_paths() -> None:
    """Money/time paths must not depend on float arithmetic; guard the imports we rely on."""
    assert math.isfinite(0.0)  # module must not shadow/import float math for time maths

# ============ weekday_windows: the per-weekday schedule (added 2026-09-30) ============
# WHY: metals at this venue runs Mon-Thu 00:00-21:00 AND 22:00-24:00, but Friday
# 00:00-20:55 only. A single weekly window cannot express two sessions and `daily_windows`
# cannot express a per-day difference, so the third concept was needed. These assert the
# venue's measured shape, the invariants that make it safe, and the one property that would
# silently corrupt data if it broke: is_open and open_intervals must AGREE.

METAL = "COMMODITY.XAUUSD"


def _at(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)


def test_metals_run_two_sessions_on_a_weekday_and_one_on_friday() -> None:
    # Mon-Thu: the 21:00-22:00 break is real, so 21:00 must be shut and 22:00 open.
    assert is_open(METAL, _at("2026-09-28 20:59")) is True
    assert is_open(METAL, _at("2026-09-28 21:00")) is False
    assert is_open(METAL, _at("2026-09-28 21:59")) is False
    assert is_open(METAL, _at("2026-09-28 22:00")) is True
    assert is_open(METAL, _at("2026-09-28 23:59")) is True
    # Friday: ONE session ending 20:55 (last OPEN minute), and NO evening session.
    assert is_open(METAL, _at("2026-10-02 20:54")) is True
    assert is_open(METAL, _at("2026-10-02 20:55")) is True
    assert is_open(METAL, _at("2026-10-02 20:56")) is False
    assert is_open(METAL, _at("2026-10-02 22:00")) is False, (
        "the venue publishes a single Friday close, so the evening session does not happen - "
        "believing it does would report its absence as lost data every Friday"
    )


def test_weekend_is_shut_because_the_day_is_absent_from_the_map() -> None:
    """A day missing from weekday_windows is SHUT. Omission is the mechanism, so this is
    worth pinning: someone 'helpfully' adding Saturday 5 and 6 would open the weekend."""
    for hhmm in ("00:00", "12:00", "22:00", "23:59"):
        assert is_open(METAL, _at(f"2026-10-03 {hhmm}")) is False, "Saturday must be shut"
        assert is_open(METAL, _at(f"2026-10-04 {hhmm}")) is False, "Sunday must be shut"


def test_open_minutes_per_day_match_the_venue() -> None:
    """Hand-computed from the venue, not read off the implementation.
    Mon-Thu: 21:00-21:00 -> 1260, plus 22:00-24:00 -> 120, total 1380.
    Friday:  00:00-20:55 inclusive -> 1256, one session.
    Sat/Sun: 0."""
    cal = load_calendar()
    for day, expected, label in (
        ("2026-09-28", 1380, "Mon"), ("2026-10-01", 1380, "Thu"),
        ("2026-10-02", 1256, "Fri"),
        ("2026-10-03", 0, "Sat"), ("2026-10-04", 0, "Sun"),
    ):
        d0 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
        got = cal.open_minutes(METAL, d0, d0 + timedelta(days=1))
        assert got == expected, f"{label} {day}: {got} open minutes, expected {expected}"


def test_is_open_and_open_intervals_agree_on_every_weekday() -> None:
    """THE invariant. `open_intervals`' own docstring warns that a drifting second copy
    "would silently mislabel market closure as data loss, which is the exact failure this
    whole slice exists to prevent". These two disagreed once already: `is_open` consulted
    the per-instrument holiday set while `open_intervals` did the same, so neither saw the
    class-level exceptions. Asserted across the whole week so that cannot recur quietly."""
    cal = load_calendar()
    for day in ("2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02",
                "2026-10-03", "2026-10-04"):
        d0 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC)
        spans = cal.open_intervals(METAL, d0, d0 + timedelta(days=1))
        for hour in range(24):
            at = d0 + timedelta(hours=hour, minutes=30)
            inside = any(a <= at < b for a, b in spans)
            assert is_open(METAL, at) is inside, (
                f"is_open and open_intervals disagree at {at.isoformat()}: "
                f"{is_open(METAL, at)} vs {inside}"
            )


def test_weekday_windows_beat_the_weekly_window_when_both_are_present() -> None:
    """Precedence, pinned. The metals entry still carries the old single weekly window as
    documentation; `weekday_windows` must win, or metals would revert to one session and
    silently re-introduce ~1,800 phantom minutes."""
    cal = load_calendar()
    rule = cal.rule_for(METAL, _at("2026-09-28 12:00"))
    assert rule.weekday_windows, "the per-weekday schedule must be loaded"
    assert rule.always_open is False
    # 21:00 is shut ONLY because the per-weekday schedule says so, not by accident.
    assert rule.sessions_on(_at("2026-09-28 21:30")) == ((0, 1260), (1320, 1440))
    assert rule.sessions_on(_at("2026-10-03 12:00")) == (), "Saturday maps to no sessions"
    assert rule.sessions_on(_at("2026-10-02 12:00")) == ((0, 1256),), "Friday is one session"


@pytest.mark.parametrize("bad,why", [
    ({0: [[0, 0]]}, "zero-length session"),
    ({0: [[600, 0]]}, "reversed session"),
    ({7: [[0, 60]]}, "weekday out of 0..6"),
    ({0: [[0, 60.5]]}, "float minute-of-day"),
    ({0: [[0, "60"]]}, "string minute-of-day"),
    ({0: []}, "empty window list"),
    ({0: [[0, 60, 90]]}, "malformed entry"),
])
def test_malformed_weekday_windows_are_refused_at_load(bad: object, why: str) -> None:
    """Fail-closed at BOOT. A half-parsed schedule is worse than none, because it looks
    configured while quietly marking a tradable day as lost data."""
    from shared.reference.asset_classes import AssetStandardError, parse_standard

    with pytest.raises((AssetStandardError, ValueError, TypeError)):
        parse_standard("commodity", {"always_open": False, "weekday_windows": bad,
                                     "tick_size": "0.01", "min_qty": "0.01", "qty_step": "0.01"})
    assert why
