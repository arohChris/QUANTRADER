"""WHY: M2.14 Gap-Repair Coverage - tests BEFORE implementation (AGENTS.md 2 Step 3).

Written against `docs/specs/m2.14_gap_repair_coverage.spec.md` only, never against
`repair_detected_gaps.py` internals. They are expected to be RED on the current tree:
the defect being fixed is that `find_repairable` takes its lower bound from the AUDIT's
watermark, and nothing on the repair path reads `known_venue_gaps`. A green run here means
the safety net can see behind itself; a red run is the honest current state.

The headline test is `test_gap_behind_audit_watermark_is_still_a_candidate`. On the current
tree that returns 0 candidates where the spec requires 46-equivalent coverage.

Spec edges are named E1..E9 / ADV1..ADV2 in the spec's section 5 and mapped 1:1 below.
"""
from __future__ import annotations

import inspect
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from scripts.repair_detected_gaps import find_repairable, record_venue_absence

DERIV = "deriv"
INSTRUMENT = "CRYPTO.BTCUSD"
MIN = timedelta(minutes=1)


def _t(hh: int, mm: int = 0, *, day: int = 26) -> datetime:
    return datetime(2026, 9, day, hh, mm, tzinfo=UTC)


# --------------------------------------------------------------------------
# Fakes - hand-rolled, matching the house style (see test_m2.7_backfill_driver_spec.py)
# --------------------------------------------------------------------------


class FakeConn:
    """Minimal asyncpg surface. Dispatches on a substring of the SQL."""

    def __init__(
        self,
        stored: Sequence[datetime],
        *,
        known_absences: Sequence[datetime] = (),
        known_table_exists: bool = True,
        banned_substrings: Sequence[str] = (),
    ) -> None:
        self.stored = list(stored)
        self.known = list(known_absences)
        self.known_table_exists = known_table_exists
        self.banned = tuple(banned_substrings)
        self.absence_writes: list[tuple[str, str, datetime, str]] = []
        self.confirmations: list[tuple[str, datetime]] = []

    def _guard(self, query: str) -> None:
        for bad in self.banned:
            assert bad not in query, (
                f"this tool must not read {bad!r} - it is another component's trust "
                f"boundary, not a gap-coverage guarantee"
            )

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self._guard(query)
        if "candles_1m" in query and "interval_start" in query:
            if args:
                inst = args[0]
                rows = [{"interval_start": t} for t in self.stored]
                if inst != INSTRUMENT:
                    return []
                return rows
            return [{"interval_start": t} for t in self.stored]
        if "known_venue_gaps" in query:
            if not self.known_table_exists:
                raise RuntimeError(
                    'relation "known_venue_gaps" does not exist'
                )
            return [{"interval_start": t} for t in self.known]
        raise AssertionError(f"unexpected query: {query}")

    async def fetchval(self, query: str, *args: Any) -> Any:
        self._guard(query)
        if "count(*)" in query and "candles_1m" in query:
            key = (args[0], args[1])
            self.confirmations.append(key)
            return 1 if key in {(INSTRUMENT, t) for t in self.stored} else 0
        raise AssertionError(f"unexpected fetchval: {query}")

    async def execute(self, query: str, *args: Any) -> str:
        self._guard(query)
        if "known_venue_gaps" in query:
            inst, broker, minute, reason = args
            self.absence_writes.append((inst, broker, minute, reason))
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute: {query}")


class StubCalendar:
    def __init__(self, intervals: Sequence[tuple[datetime, datetime]]) -> None:
        self.intervals = list(intervals)

    def open_intervals(
        self, instrument: str, lo: datetime, hi: datetime, broker: str
    ):
        for a, b in self.intervals:
            x, y = max(a, lo), min(b, hi)
            if x < y:
                yield x, y

    def is_open(self, instrument: str, when: datetime, *, broker: str) -> bool:
        return any(a <= when < b for a, b in self.intervals)


class SpyFetcher:
    def __init__(self, venue_bars: Sequence[Any]) -> None:
        self.venue_bars = list(venue_bars)
        self.calls: list[tuple[str, datetime, datetime]] = []

    async def fetch(
        self, instrument: str, broker: str, start: datetime, end: datetime
    ) -> list[Any]:
        self.calls.append((instrument, start, end))
        return [b for b in self.venue_bars if start <= b.interval_start < end]


class SpyWriter:
    def __init__(self) -> None:
        self.written: list[Any] = []

    async def __call__(self, bars: list[Any]) -> None:
        self.written.extend(bars)


class Bar:
    """Minimal stand-in for a venue Candle; only `interval_start` is inspected here."""

    def __init__(self, interval_start: datetime) -> None:
        self.interval_start = interval_start
        self.open = Decimal("83500.00")
        self.high = Decimal("83510.00")
        self.low = Decimal("83490.00")
        self.close = Decimal("83505.00")


def _stored_with_hole() -> list[datetime]:
    """30 consecutive open minutes with 10:10 and 10:11 deliberately absent.

    WHY A HELPER: the hole has to exist in the fixture, not merely in the expectation.
    A contiguous stored set has nothing missing, so asserting on it would pass for the
    wrong reason.
    """
    return [_t(10) + MIN * k for k in range(30) if k not in (10, 11)]


@pytest.fixture(autouse=True)
def _patch_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: exactly the one-hour window the fixtures store data in.

    WHY NOT THE WHOLE DAY. A wider calendar makes every minute BEFORE the fixture's first
    stored bar look like a missing open minute, so the candidate list fills with phantom
    gaps and a precise assertion cannot be made. Keeping the window tight to the data is
    what lets these tests assert exact candidate lists.
    """
    monkeypatch.setattr(
        "scripts.repair_detected_gaps._calendar",
        lambda: StubCalendar([(_t(10), _t(11))]),
    )


# --------------------------------------------------------------------------
# E1 - the headline defect: coverage must not depend on the audit watermark
# --------------------------------------------------------------------------


async def test_gap_behind_audit_watermark_is_still_a_candidate() -> None:
    """E1. A hole sitting BEHIND the newest stored bar must still be found.

    Spec D1: the repair tool scans the full stored range. The current tree takes its lower
    bound from `data_audit_watermark.verified_through`, which equals the newest bar, and
    therefore returns nothing - measured 0 candidates where this asserts 2.
    """
    stored = _stored_with_hole()
    conn = FakeConn(stored)

    probes = await find_repairable(conn, INSTRUMENT, cap=50)

    # Minutes 10:10 and 10:11 were never stored.
    assert probes == [_t(10, 11), _t(10, 10)]


async def test_find_repairable_takes_no_lower_bound_parameter() -> None:
    """E1 (structural). There must be no `since` argument to get wrong again.

    The defect was a parameter that another component could fill with a value it does not
    mean the way this tool needs. Removing it makes the bug unrepresentable.
    """
    params = inspect.signature(find_repairable).parameters
    assert "since" not in params, (
        "find_repairable must not accept a caller-supplied lower bound; the audit "
        "watermark is a different component's trust boundary"
    )
    assert "cap" in params


async def test_future_audit_watermark_cannot_shrink_coverage() -> None:
    """ADV2. A `verified_through` in the future (clock skew) must change nothing.

    `FakeConn` raises on any query mentioning the watermark table, so if coverage ever
    becomes watermark-driven again this test fails instead of silently returning 0.
    """
    stored = _stored_with_hole()
    conn = FakeConn(stored, banned_substrings=("data_audit_watermark", "verified_through"))

    probes = await find_repairable(conn, INSTRUMENT, cap=50)

    assert len(probes) == 2


# --------------------------------------------------------------------------
# E2 / E3 - known_venue_gaps is the memory that makes a full scan affordable
# --------------------------------------------------------------------------


async def test_known_venue_gap_is_not_a_candidate() -> None:
    """E2. A minute already recorded as a venue absence is never re-probed."""
    stored = _stored_with_hole()
    conn = FakeConn(stored, known_absences=[_t(10, 10)])

    probes = await find_repairable(conn, INSTRUMENT, cap=50)

    assert probes == [_t(10, 11)]


async def test_venue_absence_is_recorded_as_a_baseline() -> None:
    """E3. A confirmed absence is written to `known_venue_gaps` so run 2 skips it."""
    conn = FakeConn([])

    await record_venue_absence(conn, INSTRUMENT, _t(10, 10), reason="venue_has_no_bar")

    assert conn.absence_writes == [
        (INSTRUMENT, DERIV, _t(10, 10), "venue_has_no_bar")
    ]


async def test_second_run_reports_no_new_candidates() -> None:
    """E9 (idempotence, and the observable proof that baselining works).

    Run 1 sees the gap and baselines it; run 2 must find nothing, so the tool converges
    instead of re-probing the same venue absences on every scheduled run forever.
    """
    stored = _stored_with_hole()
    known = [_t(10, 10)]
    first = FakeConn(stored, known_absences=known)
    assert await find_repairable(first, INSTRUMENT, cap=50) == [_t(10, 11)]

    # After run 1 records the absence, the remaining candidate is also baselined.
    known.append(_t(10, 11))
    second = FakeConn(stored, known_absences=known)
    assert await find_repairable(second, INSTRUMENT, cap=50) == []


# --------------------------------------------------------------------------
# E4 / E5 - the write gates are unchanged
# --------------------------------------------------------------------------


async def test_venue_has_the_bar_so_it_is_fillable_in_dry_run() -> None:
    """E4. Venue returns the minute, so a detect-only run reports `fillable`, writes nothing."""
    from scripts.repair_detected_gaps import repair_instrument

    conn = FakeConn([])
    fetcher = SpyFetcher([Bar(_t(10, 10))])
    writer = SpyWriter()

    found, filled, confirmed, fillable = await repair_instrument(
        conn, None, fetcher, writer, INSTRUMENT, [_t(10, 10)], dry=True
    )

    assert (found, filled, confirmed, fillable) == (1, 0, 0, 1)
    assert writer.written == [], "a detect-only run must never write"


async def test_venue_has_the_bar_so_it_is_written_and_confirmed() -> None:
    """E4. A real run writes the bar and confirms it from the database, not from intent."""
    from scripts.repair_detected_gaps import repair_instrument

    conn = FakeConn([_t(10, 10)])
    fetcher = SpyFetcher([Bar(_t(10, 10))])
    writer = SpyWriter()

    found, filled, confirmed, _ = await repair_instrument(
        conn, None, fetcher, writer, INSTRUMENT, [_t(10, 10)], dry=False
    )

    assert (found, filled, confirmed) == (1, 1, 1)
    assert [b.interval_start for b in writer.written] == [_t(10, 10)]


async def test_minute_outside_calendar_open_is_never_a_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E5. A missing minute inside a closure is not a gap and is never proposed.

    Storage ends at 10:00 and the calendar shuts at 10:00, so the 90-minute run of
    "missing" minutes after it is a closure. Proposing any of them would let a repair
    write a phantom bar into shut time if a venue response were ever re-anchored.
    """
    monkeypatch.setattr(
        "scripts.repair_detected_gaps._calendar",
        lambda: StubCalendar([(_t(0), _t(10))]),
    )
    stored = [_t(9) + MIN * k for k in range(60)]  # 09:00..09:59, market shut at 10:00
    conn = FakeConn(stored)

    probes = await find_repairable(conn, INSTRUMENT, cap=50)

    assert probes == [], "nothing outside an open interval may be proposed"


# --------------------------------------------------------------------------
# E6 / E7 - degenerate ranges must not raise
# --------------------------------------------------------------------------


async def test_empty_instrument_returns_empty_list() -> None:
    """E6. An instrument with zero stored bars yields no candidates and no exception."""
    conn = FakeConn([])
    assert await find_repairable(conn, INSTRUMENT, cap=50) == []


async def test_single_stored_bar_returns_no_candidates() -> None:
    """E7. `lo == hi` is a one-minute range; there is nothing inside it to be missing."""
    conn = FakeConn([_t(10)])
    assert await find_repairable(conn, INSTRUMENT, cap=50) == []


async def test_missing_known_venue_gaps_table_is_tolerated() -> None:
    """E8. Migration not applied must degrade to a full scan, not crash the safety net."""
    stored = _stored_with_hole()
    conn = FakeConn(stored, known_table_exists=False)

    probes = await find_repairable(conn, INSTRUMENT, cap=50)

    assert len(probes) == 2


# --------------------------------------------------------------------------
# E12 - the scan is exact, never sampled
# --------------------------------------------------------------------------


async def test_full_range_scan_is_exact_not_sampled() -> None:
    """The safety net must not sample. Every missing open minute must be enumerated.

    A sampled scan is how a hole survives: 5 sampled bars per instrument was exactly the
    weakness that let the watermark over-claim in the first place.
    """
    span = 60
    stored = [_t(10) + MIN * k for k in range(span) if k != 7 and k != 20]
    conn = FakeConn(stored)

    probes = await find_repairable(conn, INSTRUMENT, cap=1000)

    assert set(probes) == {_t(10, 7), _t(10, 20)}
    assert len(probes) == 2


# --------------------------------------------------------------------------
# ADV1 - a baseline row is a record of venue state, never a delete instruction
# --------------------------------------------------------------------------


async def test_baseline_row_for_a_present_minute_deletes_nothing() -> None:
    """ADV1. Adversarial: `known_venue_gaps` holds a minute we DO have.

    That is a stale or erroneous baseline. It must suppress nothing and, above all, must
    never be treated as licence to remove or rewrite the stored bar.
    """
    stored = _stored_with_hole()
    stale_baseline = [_t(10, 5)]  # a minute we legitimately hold
    conn = FakeConn(stored, known_absences=stale_baseline)

    probes = await find_repairable(conn, INSTRUMENT, cap=50)

    assert _t(10, 5) not in probes
    assert probes == [_t(10, 11), _t(10, 10)]
    assert conn.absence_writes == [], "a read-only scan must never write a baseline"
    assert conn.confirmations == [], "a read-only scan must never touch candles_1m"