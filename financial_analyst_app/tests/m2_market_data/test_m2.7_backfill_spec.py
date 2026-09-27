"""
M2.7 Backfill - Step 3 tests, written AGAINST docs/specs/m2.7_backfill.spec.md.

Human directive 2026-09-27: "back fill should work for any type of holes."

The load-bearing idea is that "any type" means every hole SHAPE is ROUTED correctly, not that
every hole gets filled. Three outcomes exist - FILL, SKIP, TERMINAL - plus a fourth,
UNRESOLVED, which is neither. Confusing UNRESOLVED with either of the others is the most
dangerous bug available here, so most of these tests assert the NEGATIVE as well as the
positive (e.g. a closure must produce broker_requests == 0, not merely "no exception").

Written BEFORE implementation: this file must fail collection until
modules/m2_market_data/backfill.py exists.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from modules.m2_market_data.backfill import (
    BackfillCoordinator,
    Hole,
    HoleKind,
    bar_from,
    plan_from_runs,
    plan_windows,
)
from modules.m2_market_data.coverage import GapLedger, GapRun, GapState

MON = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
SAT = datetime(2026, 9, 19, 0, 0, tzinfo=UTC)
CRYPTO, FOREX = "CRYPTO.BTCUSD", "FX.EURUSD"
DERIV = "deriv"
MIN = timedelta(minutes=1)


# --------------------------------------------------------------------------- fakes
class FakeFetcher:
    """Records every call. `bars` is what it returns; `raises` makes it fail."""

    def __init__(self, bars: list[Any] | None = None, *, raises: Exception | None = None,
                 max_span: timedelta = timedelta(minutes=1001)) -> None:
        self.bars = bars or []
        self.raises = raises
        self.calls: list[tuple[str, str, datetime, datetime]] = []
        self._max_span = max_span

    @property
    def max_span(self) -> timedelta:
        return self._max_span

    async def fetch(self, instrument_id: str, broker: str,
                    start: datetime, end: datetime) -> list[Any]:
        self.calls.append((instrument_id, broker, start, end))
        if self.raises is not None:
            raise self.raises
        return list(self.bars)


def _bars(start: datetime, count: int, *, tick_count: int = 5) -> list[Any]:
    return [
        bar_from("CRYPTO.BTCUSD", DERIV, start + i * MIN, tick_count=tick_count)
        for i in range(count)
    ]


def _coord(fetcher: FakeFetcher, ledger: GapLedger | None = None) -> BackfillCoordinator:
    written: list[Any] = []
    return BackfillCoordinator(
        fetcher=fetcher, ledger=ledger or GapLedger(), write_backfill=written.append
    )


# ============================================================ T1-T3: genuine loss
@pytest.mark.asyncio
async def test_T1_contiguous_hole_is_filled() -> None:
    f = FakeFetcher(_bars(MON, 341))
    out = await _coord(f).run([Hole(CRYPTO, DERIV, MON, MON + timedelta(minutes=341),
                                    HoleKind.FILLABLE)])
    assert len(out.filled) == 1
    assert out.unresolved == () and out.terminal == ()
    assert out.broker_requests == 1


@pytest.mark.asyncio
async def test_T2_isolated_single_minute_is_filled() -> None:
    f = FakeFetcher(_bars(MON, 1))
    out = await _coord(f).run([Hole(CRYPTO, DERIV, MON, MON + MIN, HoleKind.FILLABLE)])
    assert len(out.filled) == 1
    assert out.broker_requests == 1


@pytest.mark.asyncio
async def test_T3_partial_return_leaves_a_remainder_and_does_not_claim_success() -> None:
    """The broker served 100 of 341 minutes. We must NOT mark the window filled - the other
    241 minutes are still lost and must come back for a re-scan."""
    f = FakeFetcher(_bars(MON, 100))
    out = await _coord(f).run([Hole(CRYPTO, DERIV, MON, MON + timedelta(minutes=341),
                                    HoleKind.FILLABLE)])
    assert out.remainder, "a partial fill must produce a remainder"
    # remainder entries are (instrument_id, start, end)
    _, rem_start, rem_end = out.remainder[0]
    assert rem_end > rem_start > MON + timedelta(minutes=100) - MIN
    # the remainder must begin where the supplied bars stopped
    assert rem_start >= MON + timedelta(minutes=100)


# ============================================================ T4: closure, NEVER fetched
@pytest.mark.asyncio
async def test_T4_closure_produces_zero_broker_requests() -> None:
    f = FakeFetcher()
    out = await _coord(f).run([Hole(FOREX, DERIV, SAT, SAT + timedelta(hours=45),
                                    HoleKind.CLOSED)])
    assert len(out.skipped) == 1
    assert f.calls == [], f"a closure must never reach the broker; got {f.calls}"
    assert out.broker_requests == 0


# ============================================================ T5: venue outage, terminal
@pytest.mark.asyncio
async def test_T5_well_formed_empty_response_is_terminal_and_learned() -> None:
    """A good response with no bars means the venue has nothing. Terminal, and recorded as
    learned so it is never asked again."""
    f = FakeFetcher([])  # no error, simply no data
    ledger = GapLedger()
    out = await _coord(f, ledger).run([Hole(CRYPTO, DERIV, MON, MON + timedelta(hours=2),
                                            HoleKind.FILLABLE)])
    assert len(out.terminal) == 1
    assert out.filled == ()
    assert ledger.permanent_runs(CRYPTO, DERIV), "an outage must be recorded permanently"
    # ...and a second run must not ask again
    f2 = FakeFetcher([])
    await _coord(f2, ledger).run([Hole(CRYPTO, DERIV, MON, MON + timedelta(hours=2),
                                              HoleKind.FILLABLE)])
    assert f2.calls == [], "a learned closure must never be re-requested"


# ============================================================ T6-T7: impossible windows
@pytest.mark.asyncio
async def test_T6_window_before_listing_is_terminal_not_invented() -> None:
    f = FakeFetcher()
    out = await _coord(f).run([Hole("CRYPTO.GHOST", DERIV,
                                    datetime(2020, 1, 1, tzinfo=UTC),
                                    datetime(2020, 1, 2, tzinfo=UTC),
                                    HoleKind.PRE_LISTING)])
    assert len(out.terminal) == 1
    assert f.calls == [], "pre-listing data must never be requested, let alone invented"


@pytest.mark.asyncio
async def test_T7_window_beyond_retention_is_terminal() -> None:
    f = FakeFetcher()
    out = await _coord(f).run([Hole(CRYPTO, DERIV, datetime(2005, 1, 1, tzinfo=UTC),
                                    datetime(2005, 1, 2, tzinfo=UTC),
                                    HoleKind.BEYOND_RETENTION)])
    assert len(out.terminal) == 1
    assert f.calls == [], "past the retention cap there is nothing to ask for"


# ============================================================ T8: broker failure, NOT closed
@pytest.mark.asyncio
async def test_T8_broker_failure_is_unresolved_never_closed_never_terminal() -> None:
    """THE highest-risk row. A broker hiccup recorded as 'market shut' permanently hides a
    real gap, and nothing would ever reveal it."""
    ledger = GapLedger()
    f = FakeFetcher(raises=ConnectionError("broker unreachable"))
    out = await _coord(f, ledger).run([Hole(CRYPTO, DERIV, MON, MON + timedelta(hours=2),
                                            HoleKind.FILLABLE)])
    assert len(out.unresolved) == 1
    assert out.terminal == (), "a broker error must NEVER become terminal"
    assert out.skipped == (), "a broker error must NEVER become a closure"
    assert ledger.permanent_runs(CRYPTO, DERIV) == [], (
        "a broker error must not be recorded as a permanent closure"
    )


@pytest.mark.asyncio
async def test_T8b_malformed_broker_response_is_unresolved_not_closed() -> None:
    class Malformed(FakeFetcher):
        async def fetch(self, *a, **k):  # type: ignore[override]
            self.calls.append(a)  # type: ignore[attr-defined]
            raise ValueError("malformed json")

    ledger = GapLedger()
    out = await _coord(Malformed(), ledger).run(
        [Hole(CRYPTO, DERIV, MON, MON + timedelta(hours=2), HoleKind.FILLABLE)])
    assert len(out.unresolved) == 1
    assert out.skipped == () and out.terminal == ()
    assert ledger.permanent_runs(CRYPTO, DERIV) == []


# ============================================================ T9: corrupt bar
@pytest.mark.asyncio
async def test_T9_zero_tick_bar_is_rejected_as_corrupt() -> None:
    bad = [bar_from(CRYPTO, DERIV, MON, tick_count=0)]
    out = await _coord(FakeFetcher(bad)).run(
        [Hole(CRYPTO, DERIV, MON, MON + MIN, HoleKind.FILLABLE)])
    assert out.filled == (), "a bar with no ticks is not data"
    assert len(out.unresolved) == 1


# ============================================================ T10: mixed window
@pytest.mark.asyncio
async def test_T10_mixed_window_fills_only_the_fillable_part() -> None:
    f = FakeFetcher(_bars(SAT, 30))
    out = await _coord(f).run([
        Hole(FOREX, DERIV, SAT, SAT + timedelta(hours=3), HoleKind.CLOSED),
        Hole(FOREX, DERIV, SAT + timedelta(hours=3), SAT + timedelta(hours=4), HoleKind.FILLABLE),
    ])
    assert len(out.skipped) == 1
    assert f.calls, "the fillable part must still be repaired"
    for _, _, start, end in f.calls:
        assert start >= SAT + timedelta(hours=3), "must not request across a closure"


# ============================================================ chunking / bounds
@pytest.mark.asyncio
async def test_long_window_is_chunked_to_the_venue_cap() -> None:
    span = timedelta(days=30)
    f = FakeFetcher([], max_span=timedelta(minutes=1001))
    out = await _coord(f).run([Hole(CRYPTO, DERIV, MON, MON + span, HoleKind.FILLABLE)])
    expected = -(-int(span.total_seconds() // 60) // 1001)  # ceil
    assert out.broker_requests == expected, (
        f"30d should be {expected} requests, made {out.broker_requests}"
    )
    for _, _, start, end in f.calls:
        assert end - start <= timedelta(minutes=1001)


@pytest.mark.asyncio
async def test_ten_year_window_is_bounded_not_one_giant_request() -> None:
    f = FakeFetcher([])
    out = await _coord(f, ).run([Hole(CRYPTO, DERIV, datetime(2016, 1, 1, tzinfo=UTC),
                                       datetime(2026, 1, 1, tzinfo=UTC), HoleKind.FILLABLE)])
    assert out.broker_requests > 5000, "a decade must be many chunks, never one request"
    for _, _, start, end in f.calls:
        assert end - start <= timedelta(minutes=1001)


# ============================================================ idempotency
@pytest.mark.asyncio
async def test_rerun_after_success_issues_no_further_requests() -> None:
    ledger = GapLedger()
    f1 = FakeFetcher(_bars(MON, 10))
    await _coord(f1, ledger).run([Hole(CRYPTO, DERIV, MON, MON + timedelta(minutes=10),
                                       HoleKind.FILLABLE)])
    f2 = FakeFetcher(_bars(MON, 10))
    out = await _coord(f2, ledger).run([Hole(CRYPTO, DERIV, MON, MON + timedelta(minutes=10),
                                            HoleKind.FILLABLE)])
    assert out.broker_requests == 0, "an already-filled window must not be re-fetched"


@pytest.mark.asyncio
async def test_duplicate_bars_in_one_response_are_deduplicated() -> None:
    dupes = _bars(MON, 5) + _bars(MON, 5)
    seen: list[Any] = []
    coord = BackfillCoordinator(
        fetcher=FakeFetcher(dupes), ledger=GapLedger(), write_backfill=seen.extend)
    await coord.run([Hole(CRYPTO, DERIV, MON, MON + timedelta(minutes=5), HoleKind.FILLABLE)])
    starts = [b.interval_start for b in seen]
    assert len(starts) == len(set(starts)), "duplicate bars must be written once"


@pytest.mark.asyncio
async def test_unsorted_bars_are_sorted_before_write() -> None:
    unsorted_bars = list(reversed(_bars(MON, 6)))
    seen: list[Any] = []
    coord = BackfillCoordinator(
        fetcher=FakeFetcher(unsorted_bars), ledger=GapLedger(), write_backfill=seen.extend)
    await coord.run([Hole(CRYPTO, DERIV, MON, MON + timedelta(minutes=6), HoleKind.FILLABLE)])
    assert [b.interval_start for b in seen] == sorted(b.interval_start for b in seen)


@pytest.mark.asyncio
async def test_bars_outside_the_requested_window_are_dropped() -> None:
    stray = _bars(MON + timedelta(days=5), 3)
    seen: list[Any] = []
    coord = BackfillCoordinator(
        fetcher=FakeFetcher(stray), ledger=GapLedger(), write_backfill=seen.extend)
    await coord.run([Hole(CRYPTO, DERIV, MON, MON + timedelta(minutes=10), HoleKind.FILLABLE)])
    assert seen == [], "the broker returned bars we did not ask for; they must be dropped"


# ============================================================ no-op + validation
@pytest.mark.asyncio
async def test_no_holes_means_no_broker_requests() -> None:
    f = FakeFetcher()
    out = await _coord(f).run([])
    assert out.broker_requests == 0
    assert f.calls == []


def test_zero_length_and_reversed_windows_are_rejected() -> None:
    for a, b in ((MON, MON), (MON + MIN, MON)):
        with pytest.raises(ValueError):
            plan_windows(Hole(CRYPTO, DERIV, a, b, HoleKind.FILLABLE))


def test_naive_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        plan_windows(Hole(CRYPTO, DERIV, datetime(2026, 9, 14), MON + timedelta(days=1),  # noqa: DTZ001 - naive ON PURPOSE
                          HoleKind.FILLABLE))  # type: ignore[arg-type]


def test_future_window_is_rejected() -> None:
    far = datetime(2100, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="future"):
        plan_windows(Hole(CRYPTO, DERIV, far, far + timedelta(hours=1), HoleKind.FILLABLE))


# ============================================================ classification wiring
def test_planner_routes_every_gap_state_to_exactly_one_action() -> None:
    """The M2b.6 four states must map onto the backfill taxonomy with no state unhandled."""
    runs = [
        GapRun(CRYPTO, DERIV, MON, MON + timedelta(minutes=5), GapState.FILLING),
        GapRun(FOREX, DERIV, SAT, SAT + timedelta(hours=2), GapState.CLOSED),
        GapRun(CRYPTO, DERIV, MON, MON + timedelta(minutes=9), GapState.PERMANENTLY_MISSING),
    ]
    plan = plan_from_runs(runs)
    kinds = {k: [h for h in plan if h.kind is k] for k in HoleKind}
    assert len(kinds[HoleKind.CLOSED]) == 1
    assert len(kinds[HoleKind.FILLABLE]) == 1
    assert len(kinds[HoleKind.ALREADY_TERMINAL]) == 1
