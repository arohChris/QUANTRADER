"""
M2.7 backfill driver - load MONTHS of broker history into Timescale.

WHY this module exists: G0 exit (`AGENTS.md` 1) needs `scripts/gate0_walkforward.py`
plus a documented out-of-sample expectancy-after-fees number, and a walk-forward
cannot run on the 9.03 hours currently stored. `DerivHistoryFetcher` caps a single
request at 1000 minutes (`history.py:60`), so 3 months is ~130 sequenced requests -
and nothing sequenced them. This is that sequencer.

Spec: docs/specs/m2.7_backfill_driver.spec.md (human LGTM 2026-09-29, ACTION 75).
Tests: tests/m2_market_data/test_m2.7_backfill_driver_spec.py (written first, RED).

THE ONE RULE THAT MAKES THIS SAFE (spec 1):

  The broker is authoritative about its own data. The calendar is OUR config.
  When they disagree, the BROKER wins and the disagreement is REPORTED.

`backfill.py:315-322` turns an empty broker response into TERMINAL and calls
`record_permanent`. That is right for a caller whose windows it trusts. It is
CATASTROPHIC here, because our windows come from a calendar already measured to be
wrong: config says forex closes Friday 21:00, the broker closes 20:55. A
calendar-driven driver would request 20:55-21:00 every Friday, be refused, and write
a PERMANENT record that `_classify:245-248` honours forever - ~130 permanent holes,
each one masking any real gap in the same window. A backfill that invents permanence
from a config error is worse than no backfill: it converts a visible bug into
invisible data loss.

So permanence is refused STRUCTURALLY, not by convention: the driver writes through
`PermanentGuardLedger`, which converts any `record_permanent` attempt into a
CALENDAR_DISCREPANCY. The frozen `GapLedger` is never reached, and `inner
.permanent_attempts` (asserted empty by the tests) is the proof.

Honest cost, stated not hidden: a genuinely unrecoverable window is now RETRIED on a
later run rather than learned as permanent. That is the right trade - a wasted broker
call is cheap, a masked real gap is not - and the cursor bounds the retries.

FROZEN INPUTS, READ-ONLY: `backfill.py`, `history.py`, `coverage.py`, `calendar.py`
are all M2 slices marked `complete`. This module reads them and edits NONE of them.
"""
from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from modules.m2_market_data.coverage import GapRun, GapState

__all__ = [
    "BackfillDriver",
    "BackfillReport",
    "PermanentGuardLedger",
    "chunk_window",
    "plan_open_windows",
]

MIN = timedelta(minutes=1)
DEFAULT_MAX_SPAN = timedelta(minutes=1000)  # mirrors history.py SELF_IMPOSED_MAX_SPAN
DEFAULT_MIN_INTERVAL_MS = 250.0

#: A single invocation is bounded. A decade of 1m is ~5.2M rows and hundreds of venue
#: requests, so it must be split into deliberate invocations rather than attempted
#: blindly (spec A4). One year is ~525,600 rows - deliberately generous for a history
#: load, and far short of anything a mistyped date range could turn into.
MAX_RANGE = timedelta(days=365)


# ------------------------------------------------------------------ protocols


class _Fetcher(Protocol):
    @property
    def max_span(self) -> timedelta: ...

    async def fetch(
        self, instrument_id: str, broker: str, start: datetime, end: datetime
    ) -> Sequence[Any]: ...


class _Calendar(Protocol):
    def open_intervals(
        self, instrument_id: str, start: datetime, end: datetime, broker: str = ...
    ) -> list[tuple[datetime, datetime]]: ...


class _CursorStore(Protocol):
    """Sync OR async on purpose. The tests use a synchronous in-memory store (fast, and
    it makes the resume assertions readable), while the real store is asyncpg and must
    be awaited. Declaring one strict shape would force the tests to be async for no gain,
    or force production to block the event loop - so the driver awaits a result if one
    is awaitable, and the real cursor is genuinely non-blocking.
    """

    def load(self, instrument_id: str, broker: str) -> Any: ...

    def save(self, instrument_id: str, broker: str, reached: datetime) -> Any: ...


# ------------------------------------------------------------- pure functions


def _require_aware(value: Any, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{label} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be tz-aware (UTC); naive datetimes are rejected")
    return value.astimezone(UTC)


def chunk_window(
    start: datetime, end: datetime, max_span: timedelta
) -> list[tuple[datetime, datetime]]:
    """Split the HALF-OPEN window [start, end) into venue-sized half-open chunks.

    WHY half-open throughout (spec I3): a candle's `interval_start` is the START of
    its minute. Deriv returns a CLOSED set of starts, so `(last - first) / 60 + 1`
    is correct for what the broker sent and WRONG for our window - applying it here
    would manufacture a phantom missing minute at the end of every chunk. Verified
    against a second implementation in the tests.

    WHY a raise and not a clamp on a bad cap (spec A1): a zero or negative cap makes
    the cursor loop below either spin forever or step backwards.
    """
    start = _require_aware(start, "start")
    end = _require_aware(end, "end")
    if end <= start:
        raise ValueError(f"window has no length: {start.isoformat()}..{end.isoformat()}")
    if not isinstance(max_span, timedelta):
        raise TypeError(f"max_span must be a timedelta, got {type(max_span).__name__}")
    if max_span <= timedelta(0):
        raise ValueError(f"max_span must be > 0, got {max_span}")

    out: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        stop = min(cursor + max_span, end)
        out.append((cursor, stop))
        cursor = stop
    return out


def plan_open_windows(
    calendar: _Calendar,
    instrument_id: str,
    broker: str,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    """The tradable sub-intervals of [start, end), clipped, sorted and merged.

    WHY `open_intervals` and never `is_open` per minute: the calendar's own docstring
    records that failure ("a ten-year window is ~5.2M calls, which does not finish"),
    and reusing the SAME primitive is what stops the backfill and the gap detector from
    disagreeing about what was tradable. One call, O(days).
    """
    start = _require_aware(start, "start")
    end = _require_aware(end, "end")
    if end <= start:
        return []

    raw = calendar.open_intervals(instrument_id, start, end, broker=broker)
    clipped: list[tuple[datetime, datetime]] = []
    for item in raw or []:
        a = _require_aware(item[0], "interval start")
        b = _require_aware(item[1], "interval end")
        a, b = max(a, start), min(b, end)
        if a < b:
            clipped.append((a, b))
    if not clipped:
        return []

    clipped.sort(key=lambda p: p[0])
    merged = [clipped[0]]
    for a, b in clipped[1:]:
        last_a, last_b = merged[-1]
        if a <= last_b:  # overlapping OR merely adjacent - both are one request
            if b > last_b:
                merged[-1] = (last_a, b)
        else:
            merged.append((a, b))
    return merged


# ------------------------------------------------------------------- reporting


@dataclass(frozen=True, slots=True)
class BackfillReport:
    """Immutable account of one run. Frozen because a run is a FACT; a mutable report
    can be edited into a false claim after the fact."""

    instrument_id: str
    broker: str
    requested_start: datetime
    requested_end: datetime
    status: str = "running"
    requests: int = 0
    bars_written: int = 0
    closed_skipped: int = 0
    discrepancies: int = 0
    unresolved: int = 0
    forming_dropped: int = 0
    discrepancy_windows: tuple[tuple[datetime, datetime, str], ...] = ()
    duration_ms: float = 0.0

    def summary(self) -> str:
        return (
            f"[{self.status}] {self.instrument_id} {self.requested_start.isoformat()}"
            f"..{self.requested_end.isoformat()} | requests={self.requests}"
            f" bars_written={self.bars_written} closed_skipped={self.closed_skipped}"
            f" discrepancies={self.discrepancies} unresolved={self.unresolved}"
            f" forming_dropped={self.forming_dropped} ({self.duration_ms:.0f}ms)"
        )


# ---------------------------------------------------------- the safety guard


@dataclass(frozen=True, slots=True)
class Discrepancy:
    """A place where the calendar and the broker disagreed, or where the broker failed.

    WHY this exists instead of a permanent gap record: see the module docstring. The
    broker's silence about a window OUR calendar called tradable is a CONFIG bug, and
    it is reported as such rather than laundered into permanence.
    """

    start: datetime | None
    end: datetime | None
    reason: str
    runs: tuple[Any, ...] = ()


class PermanentGuardLedger:
    """A `GapLedger` PROXY that makes invariant I2 structural.

    The frozen `BackfillCoordinator` calls `record_permanent` on an empty response.
    Rather than instructing the caller to "remember not to", the driver passes THIS,
    so the frozen code physically cannot reach the real ledger. The tests assert on the
    INNER ledger, so a buggy guard fails them - testing only the guard would have
    proved the guard is well-behaved, not that the system is safe.
    """

    def __init__(self, inner: Any) -> None:
        # WHY `Any` and not `GapLedger`: this is a PROXY, and a proxy that demands the
        # concrete class cannot be given a test double - which would force every test to
        # build a real GapLedger, and the whole point of the spy is to prove the INNER
        # ledger is never reached. The three methods actually forwarded are the entire
        # contract the driver relies on, and each is exercised directly.
        self._inner = inner
        self.discrepancies: list[Discrepancy] = []

    def record_permanent(
        self, runs: Sequence[Any], *, reason: str, source: str = "config"
    ) -> int:
        """NEVER forwards. Records the attempt as a discrepancy and returns 0 new.

        Returns 0 rather than the run count on purpose: a caller that counts what it
        "learned" would believe it had made progress.
        """
        self.discrepancies.append(
            Discrepancy(start=None, end=None, reason=reason, runs=tuple(runs))
        )
        return 0

    def note_discrepancy(self, start: datetime, end: datetime, reason: str) -> None:
        self.discrepancies.append(Discrepancy(start=start, end=end, reason=reason))

    def record_filled(self, runs: Sequence[Any]) -> int:
        return self._inner.record_filled(runs)

    def is_filled(self, instrument_id: str, broker: str, start: datetime, end: datetime) -> bool:
        return self._inner.is_filled(instrument_id, broker, start, end)

    def permanent_runs(self, instrument_id: str, broker: str) -> list[Any]:
        return self._inner.permanent_runs(instrument_id, broker)


# ----------------------------------------------------------------- the driver


class BackfillDriver:
    """Sequence broker requests across a range, calendar-aware and resumable.

    Collaborators are all injected (`fetcher`, `calendar`, `ledger`, `cursor_store`,
    `clock`, `sleeper`, `write_bars`) so the whole slice is testable with no network
    and no database, per AGENTS.md 4.5 - the engine already existed; this is the
    missing sequencer, not a new engine.
    """

    def __init__(
        self,
        *,
        fetcher: _Fetcher,
        calendar: _Calendar,
        ledger: PermanentGuardLedger,
        cursor_store: _CursorStore,
        clock: Callable[[], datetime],
        sleeper: Callable[[float], Awaitable[None]],
        max_span: timedelta = DEFAULT_MAX_SPAN,
        min_interval_ms: float = DEFAULT_MIN_INTERVAL_MS,
        write_bars: Callable[[Sequence[Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        if not isinstance(max_span, timedelta):
            raise TypeError(f"max_span must be a timedelta, got {type(max_span).__name__}")
        if max_span <= timedelta(0):
            raise ValueError(f"max_span must be > 0, got {max_span}")
        if not isinstance(min_interval_ms, (int, float)) or isinstance(min_interval_ms, bool):
            raise TypeError(
                f"min_interval_ms must be a number, got {type(min_interval_ms).__name__}"
            )
        if min_interval_ms < 0:
            # A1/E12: refuse rather than clamp. A negative throttle silently becomes
            # "no throttle", which is a retry storm waiting for a 429.
            raise ValueError(f"min_interval_ms must be >= 0, got {min_interval_ms}")

        self._fetcher = fetcher
        self._calendar = calendar
        self._ledger = ledger
        self._cursor = cursor_store
        self._clock = clock
        self._sleeper = sleeper
        self._max_span = max_span
        self._min_interval_s = float(min_interval_ms) / 1000.0
        self._write_bars = write_bars

    # -- validation ----------------------------------------------------------------

    def _validate(self, start: datetime, end: datetime) -> tuple[datetime, datetime]:
        start = _require_aware(start, "start")
        end = _require_aware(end, "end")
        if end <= start:
            raise ValueError(f"window has no length: {start.isoformat()}..{end.isoformat()}")
        if end - start > MAX_RANGE:
            # A4: refuse BEFORE planning. Ten years of 1m is ~5.2M rows and hundreds of
            # venue requests; that must be a deliberate act, never a default.
            raise ValueError(
                f"range {end - start} exceeds the {MAX_RANGE} safety ceiling; a multi-year "
                "1-minute load must be requested deliberately"
            )
        if start > self._clock() + MIN:
            raise ValueError(
                f"window starts in the future: {start.isoformat()}; the future has no "
                "history to load"
            )
        return start, end

    # -- bar hygiene ----------------------------------------------------------------

    @staticmethod
    def _usable(bars: Iterable[Any], lo: datetime, hi: datetime, run_end: datetime
                ) -> tuple[list[Any], int]:
        """Split a broker response into (writeable bars, forming-bar count).

        Order is deliberate and load-bearing:
          1. FORMING first - a bar whose `interval_end` is past the run end is
             IN PROGRESS. `history.py` has no clock so it cannot reject one, and the
             store writes `ON CONFLICT DO NOTHING`, so a partial bar would be written
             once and NEVER corrected. It is counted before clipping, because a forming
             bar also sits at/after the window edge and would otherwise be silently
             absorbed by the clip and never reported (spec I5).
          2. clip to the half-open request window - an over-return must not write
             outside what was asked for (E6).
          3. dedupe + sort - the broker may repeat a minute (E7/E9).
          4. drop bad data - malformed is LOUD upstream; here a well-formed but
             nonsensical bar (non-positive or inverted) is DROPPED, and the window stays
             unresolved so it is retried (E10).
        """
        forming = 0
        seen: dict[datetime, Any] = {}
        for bar in bars or ():
            interval_end = getattr(bar, "interval_end", None)
            if interval_end is not None and interval_end > run_end:
                forming += 1
                continue
            at = getattr(bar, "interval_start", None)
            if at is None:
                continue
            if not (lo <= at < hi):
                continue
            o, h = getattr(bar, "open", None), getattr(bar, "high", None)
            low, c = getattr(bar, "low", None), getattr(bar, "close", None)
            prices = [p for p in (o, h, low, c) if p is not None]
            if len(prices) != 4:
                continue
            # WHY this is dropped rather than raised: `_price` in history.py already
            # raises on a MALFORMED wire value (the broker broke its contract - that is
            # LOUD upstream). What reaches here is well-formed but nonsensical, and the
            # project's rule is "malformed input is LOUD, well-formed but nonsensical
            # data is DROPPED" - the window then stays unresolved and is retried.
            if low is None or h is None or min(prices) <= 0 or low > h:
                continue
            seen[at] = bar
        return [seen[k] for k in sorted(seen)], forming

    # -- the run ---------------------------------------------------------------------

    async def run(
        self,
        *,
        instrument_id: str,
        broker: str,
        start: datetime,
        end: datetime,
        resume: bool = True,
    ) -> BackfillReport:
        import time

        t0 = time.perf_counter()
        start, end = self._validate(start, end)

        if resume:
            reached = self._cursor.load(instrument_id, broker)
            if inspect.isawaitable(reached):
                reached = await reached
            if reached is not None:
                reached = _require_aware(reached, "cursor")
                # E19: already past the range is a COMPLETE run with 0 requests, not an
                # error. The cursor must also never rewind the request plan.
                if reached >= end:
                    return BackfillReport(
                        instrument_id, broker, start, end, status="complete",
                        duration_ms=(time.perf_counter() - t0) * 1000.0,
                    )
                start = max(start, reached)

        try:
            spans = plan_open_windows(self._calendar, instrument_id, broker, start, end)
        except Exception:  # noqa: BLE001
            # A5: planning failed -> nothing requested, nothing written, cursor untouched.
            # The calendar is a collaborator, not a trusted oracle: a config or reference
            # fault must end the run as `failed` and say so, not be swallowed.
            return BackfillReport(
                instrument_id, broker, start, end, status="failed",
                duration_ms=(time.perf_counter() - t0) * 1000.0,
            )

        total_minutes = int((end - start) / MIN)
        open_minutes = sum(int((b - a) / MIN) for a, b in spans)
        closed_skipped = max(0, total_minutes - open_minutes)

        requests = 0
        written = 0
        discrepancies: list[tuple[datetime, datetime, str]] = []
        unresolved = 0
        forming = 0
        status = "complete"
        reached_max: datetime | None = None

        for span_a, span_b in spans:
            for a, b in chunk_window(span_a, span_b, self._max_span):
                # Throttle BETWEEN requests, never before the first or after the last
                # (spec E12). Injected, so tests never sleep for real (AGENTS.md 3.5).
                if requests and self._min_interval_s:
                    await self._sleeper(self._min_interval_s)
                requests += 1
                try:
                    raw = await self._fetcher.fetch(instrument_id, broker, a, b)
                except asyncio.CancelledError:
                    # I2's sibling: cancellation must NEVER be recorded as a data
                    # outcome. Swallowing it would wedge shutdown.
                    raise
                except Exception as exc:  # noqa: BLE001
                    # E13: an unknown broker outcome is UNRESOLVED, never terminal. The
                    # cursor is NOT advanced, so a resume re-requests this window.
                    unresolved += 1
                    status = "stopped_early"
                    discrepancies.append((a, b, f"fetch failed: {type(exc).__name__}"))
                    break

                usable, forming_n = self._usable(raw, a, b, end)
                forming += forming_n

                if not usable:
                    # THE HEADLINE BRANCH (spec 1 / I2). The calendar called this window
                    # tradable and the broker had nothing. That is a CONFIG disagreement,
                    # not proof the data is unrecoverable. The frozen coordinator would
                    # make it permanent; the guard makes it a reported discrepancy.
                    reason = "broker returned no usable bars for a calendar-OPEN window"
                    self._ledger.record_permanent(
                        [
                            GapRun(
                                instrument_id, broker, a, b,
                                GapState.PERMANENTLY_MISSING, reason=reason,
                            )
                        ],
                        reason=reason,
                    )
                    self._ledger.note_discrepancy(a, b, reason)
                    discrepancies.append((a, b, reason))
                    unresolved += 1
                    continue

                if self._write_bars is not None:
                    maybe = self._write_bars(usable)
                    if maybe is not None:
                        await maybe
                written += len(usable)
                # E13/A6: the cursor advances ONLY for bars confirmed written, so a
                # resume re-requests the unconfirmed tail rather than skipping it.
                committed = max(x.interval_end for x in usable)
                if reached_max is None or committed > reached_max:
                    reached_max = committed
                saved = self._cursor.save(instrument_id, broker, committed)
                if inspect.isawaitable(saved):
                    await saved
            else:
                continue
            break  # only an inner failure breaks the outer loop too

        return BackfillReport(
            instrument_id=instrument_id,
            broker=broker,
            requested_start=start,
            requested_end=end,
            status=status,
            requests=requests,
            bars_written=written,
            closed_skipped=closed_skipped,
            discrepancies=len(discrepancies),
            unresolved=unresolved,
            forming_dropped=forming,
            discrepancy_windows=tuple(discrepancies),
            duration_ms=(time.perf_counter() - t0) * 1000.0,
        )
