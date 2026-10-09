"""M2.7 Backfill — repair real data loss, and REFUSE to repair what is not loss.

WHY this module exists (human directive 2026-09-27: *"back fill should work for any type of
holes"*): the measured store holds a real 341-minute hole in `candles_1m`. We can now detect
it precisely (M2b.6); this is the part that puts the data back.

THE CENTRAL RULE, and the reason this is not a simple loop:

    "any type of hole" means every hole SHAPE is ROUTED correctly — it does NOT mean fill
    every hole.

A backfill that fills everything is more dangerous than one that fills nothing. It requests
Saturday metals from a venue that never had them, and invents bars for periods before an
instrument was listed — corruption that is permanent, silent, and will later be traded on.
So there are four outcomes, not two:

    FILL        the venue has the data; restore it
    SKIP        the market was shut; there is nothing to restore  -> never contact the broker
    TERMINAL    the venue can never supply it (outage, pre-listing, past retention)
    UNRESOLVED  we could not find out                              -> retry later

`UNRESOLVED` is the one that gets people in trouble. A broker timeout is NOT a closure and
NOT terminal. Recording it as either permanently hides a real gap behind a guess, and the
error is invisible afterwards. That is why `unresolved` and `remainder` are separate fields on
the outcome: a remainder is ours to re-scan, an unresolved window is the broker's to serve
later, and conflating them destroys that distinction.

NOT NEW ARCHITECTURE (`AGENTS.md` 4.5): `GapAnalyzer`, `ChunkOrchestrator` and
`RecoveryEngine` already exist in `recovery.py` and the engine already takes injected
`choose_source` / `fetch` / `write_backfill`. This supplies the missing routing and the
fetcher seam, and adds no second engine.

I/O: every collaborator is injected, so this module is fully testable offline and performs no
I/O of its own. Spec: docs/specs/m2.7_backfill.spec.md
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from modules.m2_market_data.coverage import GapLedger, GapRun, GapState

__all__ = [
    "BackfillCoordinator",
    "BackfillOutcome",
    "HistoryBar",
    "Hole",
    "HoleKind",
    "bar_from",
    "plan_from_runs",
    "plan_windows",
]

_MINUTE = timedelta(minutes=1)
_MAX_WINDOW = timedelta(days=3660)


class HistoryBar(BaseModel):
    """One repaired 1-minute bar. Frozen, extra forbidden: a repaired bar is a FACT that must
    not be mutable in flight between the fetcher and the store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_id: str
    broker: str
    interval_start: datetime
    interval_end: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    #: WHY this survives Candle 2.0.0 while the contract field is gone: it is the INPUT to the
    #: T9 presence guard in `_clip` (`tick_count <= 0` -> not data), which is a real rule with
    #: real tests. `1` therefore means "the venue attested nothing", NOT "one tick". It is
    #: internal to this module and is never written to a `Candle` or to the store.
    tick_count: int = 1
    #: WHY provenance is carried on the bar: a repaired bar must be distinguishable from a
    #: live-ingested one forever, or a later audit cannot tell what was reconstructed.
    source: str = "backfill"


def bar_from(instrument_id: str, broker: str, at: datetime, *, tick_count: int = 5,
             price: str = "100.0") -> HistoryBar:
    """A well-formed bar for tests and fixtures. Prices are exact Decimals (AGENTS.md 3.1)."""
    p = Decimal(price)
    return HistoryBar(
        instrument_id=instrument_id, broker=broker,
        interval_start=at, interval_end=at + _MINUTE,
        open=p, high=p, low=p, close=p, tick_count=tick_count,
    )


class HoleKind(str, Enum):
    """The routing taxonomy. One state per hole shape — the table in the spec is the law."""

    FILLABLE = "fillable"              # T1/T2 real loss, venue has it
    CLOSED = "closed"                  # T4 market shut - never contact the broker
    OUTAGE = "outage"                  # T5 venue outage - terminal, learned
    PRE_LISTING = "pre_listing"        # T6 no data can exist
    BEYOND_RETENTION = "beyond_retention"  # T7 past the venue's depth
    ALREADY_TERMINAL = "already_terminal"  # T5 second pass - do not re-ask


@dataclass(frozen=True, slots=True)
class Hole:
    """A window to consider, already classified. `kind` is the routing decision."""

    instrument_id: str
    broker: str
    start: datetime
    end: datetime
    kind: HoleKind


def _require_aware(value: Any, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{label} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be tz-aware (UTC); naive datetimes are rejected")
    return value.astimezone(UTC)


def _validate_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    start, end = _require_aware(start, "start"), _require_aware(end, "end")
    if end == start:
        raise ValueError("window has zero length: end == start")
    if end < start:
        raise ValueError(f"end {end.isoformat()} precedes start {start.isoformat()}")
    if end - start > _MAX_WINDOW:
        raise ValueError(f"window {end - start} exceeds the {_MAX_WINDOW} ceiling")
    if start > datetime.now(tz=UTC) + _MINUTE:
        # WHY refuse rather than "nothing to do": the future has no coverage to repair, and an
        # empty answer would be indistinguishable from "already complete".
        raise ValueError(f"window starts in the future: {start.isoformat()}")
    return start, end


def plan_windows(hole: Hole, max_span: timedelta | None = None) -> list[tuple[datetime, datetime]]:
    """Split a fillable hole into venue-sized chunks. Validation happens here, once."""
    start, end = _validate_window(hole.start, hole.end)
    if hole.kind is not HoleKind.FILLABLE:
        return [(start, end)]
    if max_span is None or max_span <= timedelta(0):
        return [(start, end)]
    out: list[tuple[datetime, datetime]] = []
    cursor = start
    while cursor < end:
        stop = min(cursor + max_span, end)
        out.append((cursor, stop))
        cursor = stop
    return out


def plan_from_runs(runs: Sequence[GapRun], ledger: GapLedger | None = None) -> list[Hole]:
    """Map M2b.6's four states onto the routing taxonomy, with no state unhandled.

    A state that reaches here without a mapping is a bug, so it is a typed error rather than
    a silently ignored window — an unrouted hole is an unrepaired hole that nobody is told
    about.
    """
    ledger = ledger if ledger is not None else GapLedger()
    out: list[Hole] = []
    for run in runs:
        if run.state is GapState.CLOSED:
            kind = HoleKind.CLOSED
        elif run.state is GapState.PERMANENTLY_MISSING:
            kind = HoleKind.ALREADY_TERMINAL
        elif run.state is GapState.FILLING:
            # A window already recorded as impossible must not be re-asked, whatever its
            # current state says. The ledger is the authority on terminality.
            known = ledger.permanent_runs(run.instrument_id, run.broker)
            kind = HoleKind.ALREADY_TERMINAL if any(k.touches(run) for k in known) \
                else HoleKind.FILLABLE
        else:
            raise ValueError(f"unroutable gap state {run.state!r} for {run.instrument_id}")
        out.append(Hole(run.instrument_id, run.broker, run.start, run.end, kind))
    return out


class HistoryFetcher(Protocol):
    """Supplies repaired bars for a window. `max_span` is the venue's per-request cap."""

    @property
    def max_span(self) -> timedelta: ...

    async def fetch(self, instrument_id: str, broker: str,
                    start: datetime, end: datetime) -> list[HistoryBar]: ...


@dataclass(frozen=True, slots=True)
class BackfillOutcome:
    """What happened, per routing class. `unresolved` and `remainder` are deliberately
    separate: one is the broker's to serve later, the other is ours to re-scan."""

    filled: tuple[tuple[str, datetime, datetime], ...] = ()
    skipped: tuple[tuple[str, datetime, datetime], ...] = ()
    terminal: tuple[tuple[str, datetime, datetime], ...] = ()
    unresolved: tuple[tuple[str, datetime, datetime], ...] = ()
    remainder: tuple[tuple[str, datetime, datetime], ...] = ()
    broker_requests: int = 0
    bars_written: int = 0


class BackfillCoordinator:
    """Routes each hole to FILL / SKIP / TERMINAL / UNRESOLVED, filling what it safely can.

    WHY a coordinator and not a loop in the caller: the routing decision is the correctness of
    this slice, and it must be in ONE place. A closure reaching the broker, or a broker error
    becoming a closure, are both silent-corruption bugs that only a single decision point can
    prevent.
    """

    def __init__(
        self,
        *,
        fetcher: HistoryFetcher,
        ledger: GapLedger,
        write_backfill: Callable[[Sequence[HistoryBar]], Awaitable[None] | None],
        retention: timedelta | None = None,
        listed_from: Callable[[str], datetime | None] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._ledger = ledger
        self._write = write_backfill
        # Retention depth is MEASURED, not assumed. None disables the check rather than
        # guessing a depth that would silently terminalise repairable windows.
        self._retention = retention
        self._listed_from = listed_from
        self._now = now or (lambda: datetime.now(tz=UTC))

    # -- routing ------------------------------------------------------------------------
    def _classify(self, hole: Hole) -> HoleKind:
        """Re-derive the kind from facts the caller may not have known."""
        if hole.kind in (HoleKind.CLOSED, HoleKind.ALREADY_TERMINAL,
                         HoleKind.PRE_LISTING, HoleKind.BEYOND_RETENTION):
            return hole.kind
        # The ledger is the authority on terminality AND on what has already been repaired.
        # Checking only `kind` would let a caller relabel a terminal window as FILLABLE and
        # cause a request we promised never to make.
        start, end = _validate_window(hole.start, hole.end)
        known = self._ledger.permanent_runs(hole.instrument_id, hole.broker)
        if any(k.touches(GapRun(hole.instrument_id, hole.broker, start, end,
                                GapState.PERMANENTLY_MISSING)) for k in known):
            return HoleKind.ALREADY_TERMINAL
        if self._ledger.is_filled(hole.instrument_id, hole.broker, start, end):
            return HoleKind.ALREADY_TERMINAL
        if self._listed_from is not None:
            listed = self._listed_from(hole.instrument_id)
            if listed is not None and hole.end <= listed:
                # T6: the window ends before the instrument existed. Nothing to fetch, and
                # asking would invite the broker to invent a pre-listing bar.
                return HoleKind.PRE_LISTING
        if self._retention is not None:
            oldest = self._now() - self._retention
            if hole.end <= oldest:
                # T7: entirely outside the venue's retention. Measured, not assumed.
                return HoleKind.BEYOND_RETENTION
        return HoleKind.FILLABLE

    async def run(self, holes: Sequence[Hole]) -> BackfillOutcome:
        filled: list[tuple[str, datetime, datetime]] = []
        skipped: list[tuple[str, datetime, datetime]] = []
        terminal: list[tuple[str, datetime, datetime]] = []
        unresolved: list[tuple[str, datetime, datetime]] = []
        remainder: list[tuple[str, datetime, datetime]] = []
        requests = 0
        written = 0

        for hole in holes:
            start, end = _validate_window(hole.start, hole.end)
            kind = self._classify(hole)
            label = (hole.instrument_id, start, end)

            if kind is HoleKind.CLOSED:
                # T4: NEVER contact the broker for a closure. This is the single cheapest
                # line of code here and the most expensive to get wrong.
                skipped.append(label)
                continue
            if kind is HoleKind.ALREADY_TERMINAL:
                skipped.append(label)
                continue
            if kind in (HoleKind.PRE_LISTING, HoleKind.BEYOND_RETENTION):
                terminal.append(label)
                continue

            for chunk_start, chunk_end in plan_windows(hole, self._fetcher.max_span):
                requests += 1
                raw_bars = None
                try:
                    raw_bars = await self._fetcher.fetch(
                        hole.instrument_id, hole.broker, chunk_start, chunk_end
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    # T8: a broker failure is UNKNOWN. Not a closure, not terminal, ever.
                    # Recording it as either would permanently hide a real gap.
                    unresolved.append((hole.instrument_id, chunk_start, chunk_end))
                    continue

                usable = self._usable(raw_bars or (), hole.instrument_id,
                                      chunk_start, chunk_end)
                if not usable:
                    if raw_bars:
                        # The venue ANSWERED with something, and every bar in it was unusable.
                        # That is not "the venue has no data" - it is garbage, and garbage must
                        # never be laundered into a permanent closure, because that would hide
                        # a real gap forever behind a corrupt response.
                        unresolved.append((hole.instrument_id, chunk_start, chunk_end))
                    else:
                        # T5: a well-formed response with no bars means the venue has nothing.
                        # Terminal and learned, so it is never asked again.
                        terminal.append((hole.instrument_id, chunk_start, chunk_end))
                        self._ledger.record_permanent(
                            [GapRun(hole.instrument_id, hole.broker, chunk_start, chunk_end,
                                    GapState.PERMANENTLY_MISSING)],
                            reason="broker returned no bars for the window",
                        )
                    continue

                maybe = self._write(usable)
                if maybe is not None:
                    await maybe
                written += len(usable)
                filled.append((hole.instrument_id, chunk_start, chunk_end))
                # WHY record only a FULLY covered chunk as repaired: recording a partial chunk
                # as filled would make the idempotency check skip the minutes we never got.
                if not self._uncovered(chunk_start, chunk_end,
                                       [b.interval_start for b in usable]):
                    self._ledger.record_filled([
                        GapRun(hole.instrument_id, hole.broker, chunk_start, chunk_end,
                               GapState.COMPLETE)
                    ])
                # T3: compute the remainder from ACTUAL coverage, not from whether the chunk
                # came back empty. A venue that returns 100 of 341 minutes has still left 241
                # minutes lost, and reporting the chunk as filled would erase them from the
                # repair queue permanently.
                holes_left = self._uncovered(chunk_start, chunk_end,
                                            [b.interval_start for b in usable])
                remainder.extend((hole.instrument_id, a, b) for a, b in holes_left)

        return BackfillOutcome(
            filled=tuple(filled), skipped=tuple(skipped), terminal=tuple(terminal),
            unresolved=tuple(unresolved), remainder=tuple(remainder),
            broker_requests=requests, bars_written=written,
        )

    @staticmethod
    def _uncovered(start: datetime, end: datetime,
                   have: Sequence[datetime]) -> list[tuple[datetime, datetime]]:
        """The minute-aligned sub-ranges of [start, end) that the venue did NOT supply.

        WHY compute this instead of trusting the response: a venue may answer a 341-minute
        window with 100 minutes, and "we got a response" is not "the window is repaired".
        Reporting such a chunk as filled would permanently remove 241 lost minutes from the
        repair queue, which is the one outcome this whole module exists to prevent.
        """
        present = set(have)
        out: list[tuple[datetime, datetime]] = []
        run_start: datetime | None = None
        cursor = start
        while cursor < end:
            if cursor in present:
                if run_start is not None:
                    out.append((run_start, cursor))
                    run_start = None
            elif run_start is None:
                run_start = cursor
            cursor += _MINUTE
        if run_start is not None:
            out.append((run_start, end))
        return out

    def _usable(self, bars: Sequence[HistoryBar], instrument_id: str,
                start: datetime, end: datetime) -> list[HistoryBar]:
        """Deduplicate, clip to the window, drop corrupt bars, and sort.

        WHY each: duplicates break idempotency, stray bars mean we asked for something we did
        not want, a zero-tick bar is not data (T9), and unsorted writes make a partial repair
        impossible to reason about.
        """
        seen: dict[datetime, HistoryBar] = {}
        for bar in bars:
            if bar.instrument_id != instrument_id:
                continue
            if bar.tick_count <= 0:
                continue  # T9: presence is not correctness
            if not (start <= bar.interval_start < end):
                continue  # the broker returned something we did not ask for
            if bar.low > bar.high:
                continue
            seen[bar.interval_start] = bar
        return [seen[k] for k in sorted(seen)]
