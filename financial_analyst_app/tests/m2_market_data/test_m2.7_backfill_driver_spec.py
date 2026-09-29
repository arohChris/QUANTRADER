"""
M2.7 backfill driver - Step 3 spec tests. WRITTEN AGAINST THE SPEC, BEFORE CODE.

Spec: docs/specs/m2.7_backfill_driver.spec.md (human LGTM 2026-09-29, ACTION 75).
Covers that spec's I1-I6, E1-E22, A1-A9, and the five correctness criteria in 7.

STEP 3 IS EXPECTED TO BE RED. `modules/m2_market_data/backfill_driver.py` does not
exist, so this module fails at IMPORT. That is the honest pre-implementation state
(AGENTS.md 2 Step 3) and it is recorded, not hidden.

THE API SURFACE PINNED HERE (the tests define the contract, since the spec fixed the
behaviour but deliberately left the signatures to Step 3):

    BackfillDriver(*, fetcher, calendar, coordinator_factory, cursor_store,
                   clock, sleeper, max_span, min_interval_ms) -> .run(...)
    BackfillReport  frozen: instrument_id, broker, requested_start, requested_end,
                   status, requests, bars_written, closed_skipped, discrepancies,
                   unresolved, forming_dropped, discrepancy_windows, duration_ms
    PermanentGuardLedger(inner) - a GapLedger PROXY that turns any record_permanent
                   attempt into a CALENDAR_DISCREPANCY instead of a permanent record.

`PermanentGuardLedger` is the mechanism that makes invariant I2 STRUCTURAL rather
than a convention: the frozen BackfillCoordinator will call record_permanent on an
empty broker response (backfill.py:315-322), and that is exactly the behaviour that
would turn our known-wrong Friday calendar into ~130 permanent holes. The driver
passes a guard so the coordinator cannot reach the real ledger.

LAW OBSERVED HERE:
  * every test asserts a SPECIFIC value, count, or exception type. No "no exception"
    only-assertions (AGENTS 3.5).
  * not money path, so no Decimal hand-calc sheet (AGENTS 3.1). Prices are
    placeholder Decimals; the slice moves no money.
  * NO wall-clock sleeping: `sleeper` is injected and asserted on (AGENTS 3.5).
  * the chunking math is cross-checked against a SECOND independent implementation
    (spec 7 / AGENTS 3.1) - interval arithmetic is exactly what one implementation
    gets consistently wrong.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from modules.m2_market_data.backfill import HistoryBar
from modules.m2_market_data.backfill_driver import (
    BackfillDriver,
    BackfillReport,
    PermanentGuardLedger,
    chunk_window,
    plan_open_windows,
)

DERIV = "deriv"
INSTR = "CRYPTO.BTCUSD"
MIN = timedelta(minutes=1)
T0 = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)


# ------------------------------------------------------------------- test doubles


def bar(start: datetime, px: str = "100") -> HistoryBar:
    d = Decimal(px)
    return HistoryBar(
        instrument_id=INSTR,
        broker=DERIV,
        interval_start=start,
        interval_end=start + MIN,
        open=d,
        high=d,
        low=d,
        close=d,
        tick_count=1,
        source="backfill:deriv",
    )


class SpyFetcher:
    """Counts broker requests. `responses` scripts a per-call outcome.

    A response entry may be:
      * a list[HistoryBar]  -> returned
      * an Exception        -> raised
      * None                -> treated as an EMPTY response (the T5 branch)
    """

    def __init__(self, *, max_span: timedelta = timedelta(minutes=1000)) -> None:
        self._max_span = max_span
        self.calls: list[tuple[datetime, datetime]] = []
        self.responses: list[Any] = []
        self.default: Any = []

    @property
    def max_span(self) -> timedelta:
        return self._max_span

    async def fetch(self, instrument_id: str, broker: str, start: datetime, end: datetime) -> list[HistoryBar]:
        assert instrument_id == INSTR, "driver asked for the wrong instrument"
        assert broker == DERIV, "driver asked for the wrong broker"
        self.calls.append((start, end))
        if self.responses:
            r = self.responses.pop(0)
        else:
            r = self.default
        if isinstance(r, BaseException):
            raise r
        return list(r or [])


class StubCalendar:
    """open_intervals is the ONLY calendar method the driver may use.

    Scripting it is the point: a per-minute `is_open` loop would not finish on a
    multi-month range, and reusing this primitive is what stops the driver and gap
    detection from disagreeing about what was tradable.
    """

    def __init__(self, intervals: Sequence[tuple[datetime, datetime]] | None = None) -> None:
        self.intervals = list(intervals or [])
        self.queries: list[tuple[datetime, datetime]] = []

    def open_intervals(self, instrument_id: str, start: datetime, end: datetime, broker: str = DERIV) -> list[tuple[datetime, datetime]]:
        self.queries.append((start, end))
        return [(max(a, start), min(b, end)) for a, b in self.intervals if min(b, end) > max(a, start)]


class SpyLedger:
    """A real GapLedger-shaped object that RECORDS whether permanence was attempted."""

    def __init__(self) -> None:
        self.permanent_attempts: list[Any] = []
        self.filled: list[Any] = []
        self._permanent: list[Any] = []
        self._filled: list[Any] = []

    def record_permanent(self, runs: Sequence[Any], *, reason: str, source: str = "config") -> int:
        self.permanent_attempts.append((tuple(runs), reason))
        self._permanent.extend(runs)
        return len(runs)

    def record_filled(self, runs: Sequence[Any]) -> int:
        self.filled.extend(runs)
        self._filled.extend(runs)
        return len(runs)

    def is_filled(self, instrument_id: str, broker: str, start: datetime, end: datetime) -> bool:
        return False

    def permanent_runs(self, instrument_id: str, broker: str) -> list[Any]:
        return list(self._permanent)


class MemoryCursor:
    def __init__(self, reached: datetime | None = None) -> None:
        self.reached = reached
        self.saves: list[datetime] = []

    def load(self, instrument_id: str, broker: str) -> datetime | None:
        return self.reached

    def save(self, instrument_id: str, broker: str, reached: datetime) -> None:
        self.reached = reached
        self.saves.append(reached)


class SpySleeper:
    """Throttle is injected, so no test ever sleeps for real."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now_value = now

    def __call__(self) -> datetime:
        return self.now_value


def bars_for(start: datetime, end: datetime) -> list[HistoryBar]:
    """Every 1m bar in the half-open window [start, end)."""
    out, cur = [], start
    while cur < end:
        out.append(bar(cur))
        cur += MIN
    return out


def always_open(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    return [(start, end)]


def build(
    fetcher: SpyFetcher,
    calendar: StubCalendar,
    *,
    ledger: SpyLedger | None = None,
    cursor: MemoryCursor | None = None,
    now: datetime | None = None,
    max_span: timedelta = timedelta(minutes=1000),
    min_interval_ms: float = 0.0,
) -> tuple[BackfillDriver, SpyLedger, MemoryCursor, SpySleeper]:
    inner = ledger or SpyLedger()
    guard = PermanentGuardLedger(inner)
    cur = cursor or MemoryCursor()
    sleeper = SpySleeper()
    driver = BackfillDriver(
        fetcher=fetcher,
        calendar=calendar,
        ledger=guard,
        cursor_store=cur,
        clock=Clock(now or datetime(2026, 9, 2, tzinfo=UTC)),
        sleeper=sleeper,
        max_span=max_span,
        min_interval_ms=min_interval_ms,
    )
    return driver, inner, cur, sleeper


async def run_driver(
    driver: BackfillDriver, start: datetime, end: datetime, *, resume: bool = True
) -> BackfillReport:
    return await driver.run(instrument_id=INSTR, broker=DERIV, start=start, end=end, resume=resume)


# ============================================================ pure chunking math


def test_chunk_window_is_lossless_and_exact_count() -> None:
    """E2 / spec 7.2: union(chunks) == original AND len(chunks) == ceil(n/cap)."""
    start = T0
    end = start + timedelta(minutes=1000 * 3 + 137)
    chunks = chunk_window(start, end, timedelta(minutes=1000))
    assert len(chunks) == 4, f"expected ceil(3137/1000)=4 chunks, got {len(chunks)}"

    # losslessness by set-of-minutes, i.e. NO boundary is invented or lost
    want = {start + i * MIN for i in range(int((end - start) / MIN))}
    got: set[datetime] = set()
    for a, b in chunks:
        assert a < b, "every chunk must be non-empty"
        cur = a
        while cur < b:
            got.add(cur)
            cur += MIN
    assert got == want, f"chunking lost or invented minutes: missing={len(want - got)} extra={len(got - want)}"


def test_chunk_window_second_independent_implementation() -> None:
    """Spec 7 / AGENTS 3.1: the chunking math is cross-checked against a SECOND
    implementation. Here: a naive minute-membership model with a different structure
    (count-then-slice) rather than a second copy of the same cursor loop. A
    boundary error that both copies share would cancel out; this one cannot."""
    def naive(span_minutes: int, cap_minutes: int) -> list[range]:
        n = -(-span_minutes // cap_minutes)  # ceiling division, different technique
        return [range(i * cap_minutes, min((i + 1) * cap_minutes, span_minutes)) for i in range(n)]

    for span, cap in ((1, 1000), (999, 1000), (1000, 1000), (1001, 1000), (3137, 1000), (60, 1)):
        chunks = chunk_window(T0, T0 + timedelta(minutes=span), timedelta(minutes=cap))
        got = [int((a - T0) / MIN) for a, _ in chunks]
        assert got == [r.start for r in naive(span, cap)], f"span={span} cap={cap}"


@pytest.mark.parametrize("span,cap", [(0, 1000), (10, 0), (10, -5)])
def test_A1_degenerate_chunk_params_raise(span: int, cap: int) -> None:
    """A1: a zero/negative cap must raise, never spin forever."""
    with pytest.raises(ValueError):
        chunk_window(T0, T0 + timedelta(minutes=span), timedelta(minutes=cap))


# ==================================================================== I1 closures


@pytest.mark.asyncio
async def test_I1_closed_window_is_never_requested() -> None:
    """I1 / E3: a fully-closed range yields ZERO broker calls. Asserted on the call
    count, not on a log line."""
    fetcher = SpyFetcher()
    calendar = StubCalendar([])  # nothing open at all
    driver, *_ = build(fetcher, calendar)

    rep = await run_driver(driver, T0, T0 + timedelta(days=7))

    assert fetcher.calls == [], f"a closure reached the broker: {fetcher.calls}"
    assert rep.requests == 0
    assert rep.bars_written == 0
    assert rep.closed_skipped == int(timedelta(days=7) / MIN)


@pytest.mark.asyncio
async def test_E4_weekly_closure_never_requested() -> None:
    """E4: only the open sub-interval is requested, never the weekend inside it.

    CORRECTED 2026-09-29 at implementation: the open window was originally 100 hours,
    which at the 1000-minute cap (E2) is SIX requests - so the assertion of 1 call
    contradicted this same spec's E2. The window is now smaller than one chunk, so the
    test isolates what it claims to test: that shut time is never requested.
    """
    open_from = T0
    open_to = T0 + timedelta(minutes=500)  # < the 1000-minute cap => exactly one request
    fetcher = SpyFetcher()
    calendar = StubCalendar([(open_from, open_to)])
    driver, *_ = build(fetcher, calendar)
    fetcher.default = bars_for(open_from, open_to)

    await run_driver(driver, T0, T0 + timedelta(days=7))

    assert len(fetcher.calls) == 1
    a, b = fetcher.calls[0]
    assert a == open_from and b == open_to, f"driver asked {a}..{b}, not the open window"


@pytest.mark.asyncio
async def test_E5_chunk_never_spans_a_session_boundary() -> None:
    """E5: with a cap larger than each session, each closed gap must split the requests
    rather than produce one request spanning shut time."""
    s1 = (T0, T0 + timedelta(minutes=30))
    s2 = (T0 + timedelta(hours=2), T0 + timedelta(hours=2, minutes=30))
    fetcher = SpyFetcher()
    driver, *_ = build(fetcher, StubCalendar([s1, s2]), max_span=timedelta(minutes=1000))
    fetcher.default = []

    await run_driver(driver, T0, T0 + timedelta(hours=3))

    assert len(fetcher.calls) == 2, f"expected one request per session, got {fetcher.calls}"
    for a, b in fetcher.calls:
        assert any(a >= x and b <= y for x, y in (s1, s2)), f"request {a}..{b} spans a closure"


# ================================================================ I2 no permanence


@pytest.mark.asyncio
async def test_I2_record_permanent_is_never_reached_on_the_real_ledger() -> None:
    """I2 / E11 / A7: THE HEADLINE SAFETY TEST.

    The frozen BackfillCoordinator marks an empty broker response TERMINAL and calls
    record_permanent (backfill.py:315-322). With our known-wrong calendar (forex
    closes Fri 20:55, config says 21:00) that would write a PERMANENT record for
    every Friday residue - ~130 of them - and _classify:245-248 would honour them
    forever, masking any real gap in the same window.

    The driver must therefore never reach the real ledger's record_permanent. Proved
    by a spy on the INNER ledger, not by trusting the guard.
    """
    fetcher = SpyFetcher()
    calendar = StubCalendar(always_open(T0, T0 + timedelta(minutes=10)))
    driver, inner, *_ = build(fetcher, calendar)
    fetcher.default = None  # broker has nothing - the T5 branch

    rep = await run_driver(driver, T0, T0 + timedelta(minutes=10))

    assert inner.permanent_attempts == [], (
        f"the driver reached the real ledger's record_permanent: {inner.permanent_attempts}"
    )
    assert rep.discrepancies >= 1, "an empty answer for a calendar-OPEN window must be reported"
    assert rep.unresolved >= 1


@pytest.mark.asyncio
async def test_A7_broker_bar_on_a_calendar_holiday_is_still_written() -> None:
    """A7: the broker is authoritative about its own data. A bar it returns is stored
    even if our calendar called the day shut; the DISAGREEMENT is reported, not the
    data discarded."""
    holiday = T0 + timedelta(days=1)
    fetcher = SpyFetcher()
    # calendar says only the holiday instant is open; broker answers anyway
    calendar = StubCalendar([(holiday, holiday + MIN)])
    driver, _, *_ = build(fetcher, calendar)
    fetcher.default = [bar(holiday)]

    rep = await run_driver(driver, holiday, holiday + MIN)

    assert rep.bars_written == 1, "a bar the broker served must be stored, not thrown away"


def test_permanent_guard_ledger_blocks_and_records() -> None:
    """The guard itself: record_permanent is converted to a discrepancy, and the
    inner ledger is never touched."""
    inner = SpyLedger()
    guard = PermanentGuardLedger(inner)
    guard.note_discrepancy(T0, T0 + MIN, "probe")

    class _Run:
        def __init__(self) -> None:
            self.state = "PERMANENTLY_MISSING"

    guard.record_permanent([_Run()], reason="broker returned no bars")

    assert inner.permanent_attempts == [], "guard leaked a permanence write to the inner ledger"
    assert guard.discrepancies, "the blocked attempt must be recorded as a discrepancy"


# ==================================================================== I3 half-open


@pytest.mark.asyncio
async def test_I3_no_phantom_trailing_minute_in_the_window() -> None:
    """I3: our windows are HALF-OPEN [start, end). The closed-set `+1` convention
    (correct for Deriv's returned candle set) would manufacture a phantom gap at the
    end of every window. The driver must ask for exactly (end-start) minutes."""
    start, end = T0, T0 + timedelta(minutes=5)
    fetcher = SpyFetcher()
    driver, *_ = build(fetcher, StubCalendar(always_open(start, end)))
    fetcher.default = bars_for(start, end)

    rep = await run_driver(driver, start, end)

    assert fetcher.calls == [(start, end)], f"window was not half-open: {fetcher.calls}"
    assert rep.bars_written == 5, f"expected 5 bars for a 5-minute half-open window, got {rep.bars_written}"


@pytest.mark.asyncio
async def test_E6_bars_outside_the_window_are_clipped() -> None:
    """E6: a broker over-return cannot write outside the requested window."""
    start, end = T0, T0 + timedelta(minutes=3)
    fetcher = SpyFetcher()
    driver, *_ = build(fetcher, StubCalendar(always_open(start, end)))
    fetcher.default = bars_for(start - MIN, end + MIN)  # 2 extra bars either side

    rep = await run_driver(driver, start, end)

    assert rep.bars_written == 3, f"expected 3 clipped bars, got {rep.bars_written}"


# ===================================================================== I5 forming


@pytest.mark.asyncio
async def test_I5_forming_bar_is_dropped_and_counted() -> None:
    """I5 / E8: a bar whose interval_end is in the future is PARTIAL. history.py has no
    clock so it cannot reject one, and `ON CONFLICT DO NOTHING` means a partial bar is
    written once and never corrected. It must be dropped and counted."""
    start, end = T0, T0 + timedelta(minutes=3)
    forming = bar(end, px="999")  # interval_end == end, i.e. not yet complete
    fetcher = SpyFetcher()
    driver, *_ = build(fetcher, StubCalendar(always_open(start, end)))
    fetcher.default = bars_for(start, end) + [forming]

    rep = await run_driver(driver, start, end)

    assert rep.forming_dropped == 1, f"forming bar was not dropped: {rep}"
    assert rep.bars_written == 3, "the complete bars must still be written"


@pytest.mark.asyncio
async def test_A9_future_bar_is_dropped() -> None:
    """A9: a bar dated in the future is never stored."""
    start, end = T0, T0 + timedelta(minutes=2)
    future = bar(datetime(2027, 1, 1, tzinfo=UTC))
    fetcher = SpyFetcher()
    driver, *_ = build(fetcher, StubCalendar(always_open(start, end)))
    fetcher.default = bars_for(start, end) + [future]

    rep = await run_driver(driver, start, end)

    assert rep.bars_written == 2
    assert rep.forming_dropped == 1


# ====================================================================== I4 resume


@pytest.mark.asyncio
async def test_I4_resume_does_not_requery_committed_windows() -> None:
    """I4 / E14: an interrupted run resumes without re-requesting. The two runs'
    broker-call sets must be DISJOINT and together cover the range exactly once."""
    start, end = T0, T0 + timedelta(minutes=300)
    cursor = MemoryCursor()

    # run 1: dies after the first chunk
    f1 = SpyFetcher()
    f1.default = bars_for(start, start + timedelta(minutes=1000))
    d1, _, _, _ = build(f1, StubCalendar(always_open(start, end)), cursor=cursor,
                        max_span=timedelta(minutes=100))
    await run_driver(d1, start, end)

    first_calls = list(f1.calls)
    assert first_calls, "run 1 made no requests"
    assert cursor.reached is not None, "the cursor must advance only for COMMITTED bars"

    # run 2: resumes
    f2 = SpyFetcher()
    f2.default = []
    d2, *_ = build(f2, StubCalendar(always_open(start, end)), cursor=cursor,
                   max_span=timedelta(minutes=100))
    await run_driver(d2, start, end)

    second_calls = list(f2.calls)
    for c in second_calls:
        assert c not in first_calls, f"resume re-requested an already-committed window {c}"


@pytest.mark.asyncio
async def test_E13_socket_error_stops_early_and_keeps_the_cursor_honest() -> None:
    """E13: a mid-run failure ends `stopped_early` and the cursor reflects only bars
    CONFIRMED written, so a resume re-requests the unconfirmed tail."""
    start, end = T0, T0 + timedelta(minutes=300)
    fetcher = SpyFetcher()
    fetcher.responses = [bars_for(start, start + MIN), ConnectionResetError("peer reset")]
    cursor = MemoryCursor()
    driver, *_ = build(fetcher, StubCalendar(always_open(start, end)), cursor=cursor,
                       max_span=timedelta(minutes=1))

    rep = await run_driver(driver, start, end)

    assert rep.status == "stopped_early", f"expected stopped_early, got {rep.status}"
    assert cursor.reached is None or cursor.reached <= start + MIN


@pytest.mark.asyncio
async def test_E19_cursor_ahead_of_end_is_a_no_op() -> None:
    """E19: an already-complete range is 0 requests and status=complete, not an error."""
    start, end = T0, T0 + timedelta(hours=1)
    fetcher = SpyFetcher()
    driver, *_ = build(fetcher, StubCalendar(always_open(start, end)),
                       cursor=MemoryCursor(reached=end + timedelta(days=1)))

    rep = await run_driver(driver, start, end)

    assert fetcher.calls == []
    assert rep.status == "complete"
    assert rep.requests == 0


# ====================================================================== idempotency


@pytest.mark.asyncio
async def test_correctness_5_rerun_is_idempotent() -> None:
    """Spec 7.5 - IDEMPOTENCY, restated honestly.

    CORRECTED 2026-09-29 at implementation. The original test asserted that a second
    run with a FRESH cursor issues 0 requests. That is unsatisfiable and was my error:
    the driver has no store reader, and the spec's own algorithm (3 step 2) says
    `--fresh` RE-PLANS the whole range. The two statements contradicted each other.

    What is actually guaranteed, and what is therefore asserted:
      * a RESUMED run whose cursor is already at the end issues 0 requests (E19,
        covered separately) - no re-requesting, no wasted venue calls;
      * a FRESH run re-requests but writes no DUPLICATE rows, because the store upsert
        is ON CONFLICT DO NOTHING.
    The zero-duplicate-rows half is proven against a REAL Timescale in the Step 7 verify
    script, since it is a property of the write, not of this loop.
    """
    start, end = T0, T0 + timedelta(minutes=50)
    cursor = MemoryCursor()

    f1 = SpyFetcher()
    d1, *_ = build(f1, StubCalendar(always_open(start, end)), cursor=cursor)
    f1.default = bars_for(start, end)
    first = await run_driver(d1, start, end)
    assert first.bars_written == 50
    assert len(f1.calls) == 1

    # Same cursor = the realistic case, since the cursor lives in Postgres, not in the
    # process. The run is already complete, so it must not touch the broker.
    f2 = SpyFetcher()
    d2, *_ = build(f2, StubCalendar(always_open(start, end)), cursor=cursor)
    second = await run_driver(d2, start, end)
    assert f2.calls == [], f"a resumed run re-requested: {f2.calls}"
    assert second.requests == 0
    assert second.status == "complete"


# ========================================================================= guards


@pytest.mark.asyncio
async def test_E12_throttle_is_applied_between_requests() -> None:
    """E12 / spec 6: a minimum interval between broker requests. Asserted on the
    injected sleeper, so no test ever sleeps for real (AGENTS 3.5)."""
    start, end = T0, T0 + timedelta(minutes=300)
    fetcher = SpyFetcher()
    fetcher.default = []
    driver, _, _, sleeper = build(
        fetcher, StubCalendar(always_open(start, end)),
        max_span=timedelta(minutes=100), min_interval_ms=250,
    )

    await run_driver(driver, start, end)

    assert len(fetcher.calls) == 3, f"expected 3 chunks, got {len(fetcher.calls)}"
    assert len(sleeper.calls) >= 2, f"throttle not applied between requests: {sleeper.calls}"
    assert all(s == 0.25 for s in sleeper.calls), f"unexpected throttle interval: {sleeper.calls}"


@pytest.mark.asyncio
async def test_E12_broker_error_is_unresolved_never_terminal() -> None:
    """E12: a 429/error is UNRESOLVED. Never terminal, never permanent."""
    start, end = T0, T0 + timedelta(minutes=10)
    fetcher = SpyFetcher()
    fetcher.default = RuntimeError("rate limited")
    driver, inner, *_ = build(fetcher, StubCalendar(always_open(start, end)))

    rep = await run_driver(driver, start, end)

    assert rep.unresolved >= 1
    assert inner.permanent_attempts == []


# ==================================================================== bad input


@pytest.mark.asyncio
async def test_E16_future_start_raises_before_any_io() -> None:
    """E16: the future has no coverage to load. Must raise BEFORE a broker call."""
    fetcher = SpyFetcher()
    driver, *_ = build(fetcher, StubCalendar(always_open(T0, T0 + timedelta(hours=1))),
                       now=datetime(2026, 9, 2, tzinfo=UTC))
    future = datetime(2026, 10, 1, tzinfo=UTC)

    with pytest.raises(ValueError):
        await run_driver(driver, future, future + timedelta(hours=1))
    assert fetcher.calls == [], "a request was made despite invalid input"


@pytest.mark.asyncio
async def test_E17_naive_window_raises() -> None:
    """E17: UTC only (AGENTS 3.8)."""
    fetcher = SpyFetcher()
    driver, *_ = build(fetcher, StubCalendar(always_open(T0, T0 + MIN)))
    with pytest.raises((ValueError, TypeError)):
        await run_driver(driver, datetime(2026, 9, 1, 0, 0), T0 + timedelta(hours=1))  # noqa: DTZ001 - naive on purpose
    assert fetcher.calls == []


@pytest.mark.asyncio
async def test_E1_empty_range_raises() -> None:
    """E1: start == end has no minutes; it must not reach the broker."""
    fetcher = SpyFetcher()
    driver, *_ = build(fetcher, StubCalendar([]))
    with pytest.raises(ValueError):
        await run_driver(driver, T0, T0)
    assert fetcher.calls == []


@pytest.mark.asyncio
async def test_A3_negative_throttle_raises() -> None:
    """A3: a negative throttle must be refused at construction, not silently clamped."""
    with pytest.raises(ValueError):
        BackfillDriver(
            fetcher=SpyFetcher(),
            calendar=StubCalendar([]),
            ledger=PermanentGuardLedger(SpyLedger()),
            cursor_store=MemoryCursor(),
            clock=Clock(T0),
            sleeper=SpySleeper(),
            max_span=timedelta(minutes=1000),
            min_interval_ms=-1.0,
        )


@pytest.mark.asyncio
async def test_A4_ten_year_range_needs_an_explicit_budget() -> None:
    """A4: a 10-year 1m load is ~5.2M rows. It must be a deliberate act, not a default."""
    fetcher = SpyFetcher()
    driver, *_ = build(fetcher, StubCalendar(always_open(T0, T0 + timedelta(days=3650))))
    with pytest.raises(ValueError):
        await run_driver(driver, T0, T0 + timedelta(days=3650))
    assert fetcher.calls == [], "a decade was planned before the budget check"


# ==================================================================== I6 reporting


@pytest.mark.asyncio
async def test_I6_discrepancy_windows_are_reported_not_absorbed() -> None:
    """I6: a calendar/broker conflict must appear in the report WITH its window.
    Silence is a failure - it is how the Friday bug hides."""
    start, end = T0, T0 + timedelta(minutes=10)
    fetcher = SpyFetcher()
    fetcher.default = None
    driver, *_ = build(fetcher, StubCalendar(always_open(start, end)))

    rep = await run_driver(driver, start, end)

    assert rep.discrepancy_windows, "a discrepancy was absorbed silently"
    win = rep.discrepancy_windows[0]
    assert win[0] == start and win[1] == end, f"discrepancy window not reported: {win}"


def test_backfill_report_is_frozen() -> None:
    """A run report is a fact, not a mutable scratchpad."""
    rep = BackfillReport(
        instrument_id=INSTR,
        broker=DERIV,
        requested_start=T0,
        requested_end=T0 + MIN,
        status="complete",
        requests=1,
        bars_written=1,
    )
    with pytest.raises(Exception):  # noqa: B017 - frozen: mutation must be refused
        rep.status = "failed"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_E21_empty_database_is_not_an_error() -> None:
    """E21: the driver is a LOADER, not a repairer. A range with nothing stored is the
    normal case, not a failure."""
    start, end = T0, T0 + timedelta(hours=2)
    fetcher = SpyFetcher()
    fetcher.default = bars_for(start, end)
    driver, *_ = build(fetcher, StubCalendar(always_open(start, end)))

    rep = await run_driver(driver, start, end)

    assert rep.status == "complete"
    assert rep.bars_written == 120


@pytest.mark.asyncio
async def test_E9_bars_are_sorted_and_deduplicated_before_write() -> None:
    """E7/E9: unsorted + duplicated broker output must produce one row per minute."""
    start, end = T0, T0 + timedelta(minutes=3)
    shuffled = [bar(start + 2 * MIN), bar(start), bar(start + 2 * MIN), bar(start + MIN)]
    fetcher = SpyFetcher()
    driver, *_ = build(fetcher, StubCalendar(always_open(start, end)))
    fetcher.default = shuffled

    rep = await run_driver(driver, start, end)

    assert rep.bars_written == 3, f"expected 3 unique minutes, got {rep.bars_written}"


@pytest.mark.asyncio
async def test_E10_zero_price_bar_is_dropped_and_stays_unresolved() -> None:
    """E10: bad data is dropped, and the window stays UNRESOLVED - never terminal."""
    start, end = T0, T0 + timedelta(minutes=2)
    bad = HistoryBar(
        instrument_id=INSTR, broker=DERIV,
        interval_start=start, interval_end=start + MIN,
        open=Decimal(0), high=Decimal(0), low=Decimal(0), close=Decimal(0),
        tick_count=1, source="backfill:deriv",
    )
    fetcher = SpyFetcher()
    driver, inner, *_ = build(fetcher, StubCalendar(always_open(start, end)))
    fetcher.default = [bad]

    rep = await run_driver(driver, start, end)

    assert rep.bars_written == 0, "a zero-price bar was written"
    assert rep.unresolved >= 1
    assert inner.permanent_attempts == [], "bad data became a permanent closure"


@pytest.mark.asyncio
async def test_A5_calendar_failure_is_failed_with_no_write() -> None:
    """A5: if planning raises, nothing is written and the cursor is untouched."""
    class ExplodingCalendar(StubCalendar):
        def open_intervals(self, *a: Any, **k: Any) -> list[tuple[datetime, datetime]]:
            raise RuntimeError("calendar unavailable")

    cursor = MemoryCursor()
    fetcher = SpyFetcher()
    driver, *_ = build(fetcher, ExplodingCalendar(), cursor=cursor)

    rep = await run_driver(driver, T0, T0 + timedelta(hours=1))

    assert rep.status == "failed", f"expected failed, got {rep.status}"
    assert fetcher.calls == []
    assert cursor.saves == [], "the cursor advanced despite a planning failure"


# ====================================================== planning (open intervals)


def test_plan_open_windows_uses_open_intervals_not_per_minute_probes() -> None:
    """The driver MUST ask for intervals, not call is_open per minute. A per-minute
    loop over 3 months is ~130k probes and does not finish (calendar.py:357).

    CORRECTED 2026-09-29 at implementation: the original spans were (T0, +100h) and
    (T0+2d, T0+2d+100h) against a 3-DAY window, so clipping pulled the first span to
    the whole 3 days, it then OVERLAPPED the second, and correct merging collapsed both
    to one. The data contradicted its own expectation. They are now disjoint.
    """
    spans = [
        (T0, T0 + timedelta(hours=10)),
        (T0 + timedelta(days=2), T0 + timedelta(days=2, hours=10)),
    ]
    calendar = StubCalendar(spans)
    planned = plan_open_windows(calendar, INSTR, DERIV, T0, T0 + timedelta(days=3))

    assert len(calendar.queries) == 1, f"open_intervals called {len(calendar.queries)} times"
    assert planned == spans, f"expected the two open windows unchanged, got {planned}"


def test_plan_open_windows_merges_overlapping_intervals() -> None:
    """Overlapping calendar output must not produce duplicate/overlapping requests."""
    a, b, c = T0, T0 + timedelta(hours=1), T0 + timedelta(hours=2)
    calendar = StubCalendar([(a, c), (a, b)])
    planned = plan_open_windows(calendar, INSTR, DERIV, a, c)
    assert planned == [(a, c)], f"overlapping intervals were not merged: {planned}"


# ============================================================ report completeness


def test_backfill_report_defaults_are_honest() -> None:
    """A report field that silently defaults to 0 when it should be unknown would
    report success for a run that did nothing."""
    rep = BackfillReport(
        instrument_id=INSTR, broker=DERIV,
        requested_start=T0, requested_end=T0 + MIN, status="running",
    )
    assert rep.requests == 0
    assert rep.bars_written == 0
    assert rep.discrepancies == 0
    assert rep.discrepancy_windows == ()
    assert rep.status == "running", "a fresh report must not claim completion"
