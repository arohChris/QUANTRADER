"""M2.6 Gap Calendar - broker-authoritative gap classification + a known_gaps ledger.

WHY this exists: a naive gap detector cannot tell LOST DATA from a SHUT MARKET. Measured live
2026-09-27: crypto open, all 25 forex and all 4 commodities shut. Without this module a
25-asset mixed universe records ~17h of "lost data" every weekend, per instrument, forever -
which then blocks calculation permanently and floods the backfill queue.

AUTHORITY (spec 3a): the BROKER is authoritative. The configured calendar is a fallback for
when the broker cannot be verified; the asset-class assumption is the last resort. A broker
ERROR is UNKNOWN, never CLOSED.

    verified?  no -> fall back, and BIAS TOWARD "a gap"
                verified yes -> use the broker's answer

The bias is deliberate and asymmetric. A false gap is self-healing: backfill asks, the broker
returns nothing, the window is recorded permanently-missing with source=learned, and it is
never asked again - one wasted request. A false closure is permanent and silent: real data
loss is never repaired, because the system believes the market was shut. Only one of those
errors announces itself, so "unknown" resolves toward "we may have lost data".

Cost is O(days in window), never O(minutes). Spec: docs/specs/m2.6_gap_calendar.spec.md
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Protocol

from modules.reference import calendar as _cal
from modules.reference.reference import _KNOWN_BROKERS, UnknownInstrumentError, get_instrument

_MINUTE = timedelta(minutes=1)
# WHY a ceiling: a naive build materialised one entry per minute; a ten-year window is ~5.2M
# and does not finish. Runs are emitted as INTERVALS and the calendar is queried per day.
# 3660 rather than 3650 so a genuine ten-calendar-year window (3653 days with leap days) is
# accepted - the ceiling exists to reject absurd input, not to reject "ten years".
_MAX_WINDOW = timedelta(days=3660)
_MAX_REASON = 500
# Ordering reports the WORST state in a mixed window. Fail-closed: a caller must never be
# able to read COMPLETE while a hole exists.
_SEVERITY: dict[str, int] = {"complete": 0, "closed": 1, "filling": 2, "permanently_missing": 3}


class GapState(str, Enum):
    """Four states, not two. `CLOSED` is the one this module exists to add."""

    COMPLETE = "complete"
    FILLING = "filling"
    PERMANENTLY_MISSING = "permanently_missing"
    CLOSED = "closed"


class SessionEvidence(str, Enum):
    """Which source decided a window's tradability. Never silently upgraded."""

    BROKER = "broker"        # authoritative
    CALENDAR = "calendar"    # fallback: broker unverifiable
    DEFAULT = "default"      # last resort: no broker, no usable calendar
    UNKNOWN = "unknown"      # could not determine - NOT the same as closed


class SessionOracle(Protocol):
    """Supplies the venue's own view of a window.

    Returns `(start, end, evidence, is_open)` tuples covering the window. Implementations
    MUST report `SessionEvidence.UNKNOWN` on timeout, rate-limit, error or unreachable host,
    and MUST NOT report `is_open=False` for a window it could not actually ask about.
    """

    def evidence(
        self, instrument_id: str, broker: str, start: datetime, end: datetime
    ) -> Sequence[tuple[datetime, datetime, Any, Any]]: ...


@dataclass(frozen=True, slots=True)
class GapRun:
    """A contiguous half-open [start, end) window sharing one state.

    WHY an interval and not a list of minutes: 341 missing minutes is ONE repair request,
    not 341. It also keeps the output bounded for multi-year windows.
    """

    instrument_id: str
    broker: str
    start: datetime
    end: datetime
    state: GapState
    reason: str = ""
    evidence: SessionEvidence = SessionEvidence.CALENDAR
    # WHY a separate flag: a gap we could not confirm is not the same animal as one the
    # broker reported. It is escalated to a verification pass instead of ordinary backfill,
    # which is what stops an outage turning into a retry storm.
    needs_verification: bool = False

    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    def touches(self, other: GapRun) -> bool:
        return self.start <= other.end and other.start <= self.end


# ------------------------------------------------------------------ validation
def _require_aware(value: object, label: str) -> datetime:
    """Reject naive bounds. WHY: a naive bound has no meaning; whichever machine reads it
    invents a timezone and shifts the window by hours (AGENTS.md 3.8).

    A wrong TYPE is a TypeError and a naive-but-correct type is a ValueError, so a caller can
    tell "you passed nonsense" from "you passed a real datetime without a timezone".
    """
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
    # WHY refuse a future window rather than answering "no gaps": the future has no coverage
    # to report, and an empty answer would read as COMPLETE.
    if start > datetime.now(tz=UTC) + _MINUTE:
        raise ValueError(f"window starts in the future: {start.isoformat()}")
    return start, end


def _require_broker(broker: str) -> str:
    if broker not in _KNOWN_BROKERS:
        raise ValueError(f"unknown broker {broker!r}; known: {sorted(_KNOWN_BROKERS)}")
    return broker


# ------------------------------------------------------------------ interval maths
def _merge_intervals(
    spans: Iterable[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    ordered = sorted(spans, key=lambda p: (p[0], p[1]))
    merged: list[tuple[datetime, datetime]] = []
    for lo, hi in ordered:
        if hi <= lo:
            continue
        if merged and lo <= merged[-1][1]:
            if hi > merged[-1][1]:
                merged[-1] = (merged[-1][0], hi)
            continue
        merged.append((lo, hi))
    return merged


def _complement(
    window: tuple[datetime, datetime], closed: Sequence[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """The parts of `window` that are NOT closed."""
    out: list[tuple[datetime, datetime]] = []
    cursor = window[0]
    for lo, hi in _merge_intervals(closed):
        lo, hi = max(lo, window[0]), min(hi, window[1])
        if hi <= lo:
            continue
        if lo > cursor:
            out.append((cursor, lo))
        cursor = max(cursor, hi)
    if cursor < window[1]:
        out.append((cursor, window[1]))
    return out


def _intersect(
    a: tuple[datetime, datetime], spans: Sequence[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for lo, hi in spans:
        x, y = max(a[0], lo), min(a[1], hi)
        if y > x:
            out.append((x, y))
    return out


# ------------------------------------------------------------------ evidence cascade
def _closed_from_broker(
    instrument_id: str, broker: str, window: tuple[datetime, datetime], oracle: SessionOracle
) -> tuple[list[tuple[datetime, datetime]], bool]:
    """Ask the broker. Returns (broker-confirmed closed spans, broker_was_usable).

    WHY malformed entries are discarded rather than coerced: a broker adapter that returns
    junk must not be able to invent a closure. Garbage downgrades to the fallback, it never
    upgrades to "shut".
    """
    try:
        raw = oracle.evidence(instrument_id, broker, window[0], window[1])
    except Exception:  # noqa: BLE001 - an oracle that raises is simply unverifiable
        return [], False

    closed: list[tuple[datetime, datetime]] = []
    usable = False
    for entry in raw or ():
        try:
            lo, hi, ev, is_open = entry
            lo, hi = _require_aware(lo, "evidence start"), _require_aware(hi, "evidence end")
            ev = SessionEvidence(ev)
        except (TypeError, ValueError):
            continue  # malformed -> ignored, NOT trusted
        if not isinstance(is_open, bool):
            continue
        if ev is SessionEvidence.BROKER:
            usable = True
            if not is_open:
                closed.append((lo, hi))
    return closed, usable


def _closed_from_calendar(
    instrument_id: str, broker: str, window: tuple[datetime, datetime], calendar: Any
) -> list[tuple[datetime, datetime]]:
    source = calendar if calendar is not None else _cal
    try:
        open_spans = source.open_intervals(instrument_id, window[0], window[1], broker=broker)
    except Exception:  # noqa: BLE001 - fall through to the last resort
        return []
    return _complement(window, open_spans)


def _closed_from_default(
    instrument_id: str, window: tuple[datetime, datetime]
) -> list[tuple[datetime, datetime]]:
    """Last resort with NO broker and NO usable calendar.

    WHY this assumes OPEN: assuming 'shut' with no evidence is the one unrecoverable error
    (silent permanent data loss). Assuming 'open' costs one wasted backfill request that then
    self-heals into a learned closure.
    """
    instrument = get_instrument(instrument_id)
    if instrument.get("asset_class") == "crypto":
        return []  # 24/7
    return []  # unknown session -> also assume open, per the asymmetry above


# ------------------------------------------------------------------ public API
def merge_runs(runs: Iterable[GapRun]) -> list[GapRun]:
    """Merge touching/overlapping runs of the SAME instrument+broker+state+evidence.

    WHY merge: adjacent one-minute holes are one repair request, and a `known_gaps` table
    full of one-minute rows is unreadable and slow to scan.
    """
    ordered = sorted(runs, key=lambda r: (r.instrument_id, r.broker, r.state, r.evidence, r.start))
    merged: list[GapRun] = []
    for run in ordered:
        if (
            merged
            and merged[-1].instrument_id == run.instrument_id
            and merged[-1].broker == run.broker
            and merged[-1].state is run.state
            and merged[-1].evidence is run.evidence
            and merged[-1].touches(run)
        ):
            if run.end > merged[-1].end:
                merged[-1] = replace(merged[-1], end=run.end)
            continue
        merged.append(run)
    return merged


def classify_window(
    instrument_id: str,
    broker: str,
    start: datetime,
    end: datetime,
    *,
    missing: Any = None,
    oracle: SessionOracle | None = None,
    calendar: Any = None,
    class_resolver: Any = None,
) -> list[GapRun]:
    """Split [start, end) into CLOSED and FILLING runs, broker-authoritative.

    `missing` supplies the actually-absent spans as (start, end) pairs; omitted, the whole
    window is treated as absent, which is the useful default for a first pass over a window
    known to be empty.

    `class_resolver` is injectable for the same reason the calendar's is (M2b.5): resolving
    the asset class through a module global made a newly added instrument invisible here, and
    a hidden global is untestable without monkeypatching.
    """
    _require_broker(broker)
    resolver = class_resolver or get_instrument
    entry = resolver(instrument_id)
    if not isinstance(entry, dict):
        raise UnknownInstrumentError(
            f"{instrument_id}: class_resolver must return a mapping, got {type(entry).__name__}"
        )
    if not entry.get("asset_class"):
        raise UnknownInstrumentError(
            f"{instrument_id}: no asset_class - the standard cannot be resolved"
        )
    start, end = _validate_window(start, end)
    window = (start, end)

    # WHY a calendar may be built here: the default calendar resolves the asset class through
    # the reference module's global, so an injected resolver would stop at the instrument
    # check and then fail again inside the session lookup. Binding the calendar to the SAME
    # resolver is what makes injection actually mean something.
    effective_calendar = calendar
    if effective_calendar is None and class_resolver is not None:
        from modules.reference.calendar import load_calendar

        effective_calendar = load_calendar(class_resolver=class_resolver)

    if oracle is not None:
        closed, broker_usable = _closed_from_broker(instrument_id, broker, window, oracle)
    else:
        closed, broker_usable = [], False

    # Authority cascade (spec 3a): broker -> configured calendar -> asset-class assumption.
    if broker_usable:
        evidence = SessionEvidence.BROKER
    elif _calendar_usable(instrument_id, broker, window, effective_calendar):
        closed = _closed_from_calendar(instrument_id, broker, window, effective_calendar)
        evidence = SessionEvidence.CALENDAR
    else:
        closed = _closed_from_default(instrument_id, window)
        evidence = SessionEvidence.DEFAULT

    open_spans = _complement(window, closed)
    absent = list(missing(start, end)) if missing is not None else [window]

    runs: list[GapRun] = [
        GapRun(instrument_id, broker, lo, hi, GapState.CLOSED, evidence=evidence)
        for lo, hi in _merge_intervals(closed)
    ]
    for span in absent:
        lo, hi = _require_aware(span[0], "missing start"), _require_aware(span[1], "missing end")
        for a, b in _intersect((lo, hi), open_spans):
            runs.append(
                GapRun(
                    instrument_id, broker, a, b, GapState.FILLING,
                    evidence=evidence,
                    # WHY: anything not confirmed by the broker is a candidate, not a fact.
                    needs_verification=evidence is not SessionEvidence.BROKER,
                )
            )
    return merge_runs(runs)


def _calendar_usable(
    instrument_id: str, broker: str, window: tuple[datetime, datetime], calendar: Any
) -> bool:
    """Can the configured calendar actually answer for this instrument?"""
    source = calendar if calendar is not None else _cal
    try:
        source.open_intervals(instrument_id, window[0], window[1], broker=broker)
    except Exception:  # noqa: BLE001
        return False
    return True


@dataclass(frozen=True, slots=True)
class CoverageVerdict:
    """Can analytics compute over this window? `state` is the WORST state present."""

    instrument_id: str
    broker: str
    start: datetime
    end: datetime
    state: GapState
    runs: tuple[GapRun, ...]

    @property
    def can_compute(self) -> bool:
        """Fail closed: anything short of full coverage blocks computation.

        WHY: a number computed across a hole is the exact failure this system exists to
        prevent, and a caller must not be able to opt into it by ignoring this flag.
        """
        return self.state is GapState.COMPLETE


def coverage_of(
    instrument_id: str,
    broker: str,
    start: datetime,
    end: datetime,
    *,
    present: Any,
    missing: Any,
    oracle: SessionOracle | None = None,
    calendar: Any = None,
    class_resolver: Any = None,
) -> CoverageVerdict:
    """Report the worst state across a window, so a caller cannot compute across a hole."""
    _require_broker(broker)
    start, end = _validate_window(start, end)
    runs = tuple(
        classify_window(instrument_id, broker, start, end,
                        missing=missing, oracle=oracle, calendar=calendar,
                        class_resolver=class_resolver)
    )
    worst = GapState.COMPLETE
    for run in runs:
        if _SEVERITY[run.state.value] > _SEVERITY[worst.value]:
            worst = run.state
    if present(start, end) and worst is GapState.CLOSED:
        worst = GapState.COMPLETE
    return CoverageVerdict(instrument_id, broker, start, end, worst, runs)


class GapLedger:
    """The `known_gaps` table: windows the venue CANNOT supply, so they are never re-asked.

    WHY terminal: if an impossible window were re-enqueued every cycle, the backfill queue
    would retry it forever - a permanent leak of broker calls and a permanent false gap.
    Clearing a record is an explicit human action, never a side effect of scanning.
    """

    def __init__(self) -> None:
        self._permanent: dict[tuple[str, str], list[GapRun]] = {}
        # Windows successfully repaired. WHY this exists (M2.7 backfill): idempotency needs a
        # memory of what was FIXED, not only of what is impossible. Without it a second
        # backfill run re-requests every window it already repaired - not corrupting anything,
        # but wasting a broker call per minute of history, which is the resource the whole
        # design is rationing.
        self._filled: dict[tuple[str, str], list[GapRun]] = {}

    def record_filled(self, runs: Sequence[GapRun]) -> int:
        """Record windows that have been repaired. Returns how many were NEW."""
        added = 0
        for run in runs:
            bucket = self._filled.setdefault((run.instrument_id, run.broker), [])
            if any(existing.touches(run) for existing in bucket):
                continue
            bucket.append(run)
            added += 1
        return added

    def is_filled(self, instrument_id: str, broker: str, start: datetime, end: datetime) -> bool:
        """Has this exact window already been repaired?"""
        probe = GapRun(instrument_id, broker, start, end, GapState.COMPLETE)
        return any(r.touches(probe) for r in self._filled.get((instrument_id, broker), ()))

    def record_permanent(
        self, runs: Sequence[GapRun], *, reason: str, source: str = "config"
    ) -> int:
        """Record unrecoverable windows. Idempotent: returns how many were NEW."""
        added = 0
        for run in runs:
            if run.state is not GapState.PERMANENTLY_MISSING:
                continue
            added += self._add(replace(run, reason=reason[:_MAX_REASON]))
        return added

    def record_learned(self, runs: Sequence[GapRun]) -> int:
        """Learn a closure the broker actually confirmed.

        WHY only broker-confirmed CLOSED runs qualify: a closure inferred from a config we
        could not verify, or worse from an unanswered question, is not evidence. Recording it
        would permanently hide a real gap behind a guess - the one unrecoverable error here.
        """
        added = 0
        for run in runs:
            if run.state is GapState.CLOSED and run.evidence is SessionEvidence.BROKER:
                added += self._add(
                    replace(run, state=GapState.PERMANENTLY_MISSING,
                            reason="broker confirmed no data")
                )
        return added

    def _add(self, stored: GapRun) -> int:
        bucket = self._permanent.setdefault((stored.instrument_id, stored.broker), [])
        if any(existing.touches(stored) for existing in bucket):
            return 0
        bucket.append(stored)
        return 1

    def permanent_runs(self, instrument_id: str, broker: str) -> list[GapRun]:
        """Scoped by broker: a gap recorded for deriv must never match another venue."""
        return list(self._permanent.get((instrument_id, broker), []))

    def enqueueable(self, runs: Sequence[GapRun]) -> list[GapRun]:
        """Filter out anything already known-impossible."""
        return [
            run for run in runs
            if not (run.state is not GapState.PERMANENTLY_MISSING
                    and any(k.touches(run) for k in self.permanent_runs(run.instrument_id, run.broker)))
        ]

    def reopen(self, instrument_id: str, broker: str) -> int:
        """Explicit human action only. Never called by a scan."""
        return len(self._permanent.pop((instrument_id, broker), []))


__all__ = [
    "CoverageVerdict",
    "GapLedger",
    "GapRun",
    "GapState",
    "SessionEvidence",
    "SessionOracle",
    "classify_window",
    "coverage_of",
    "merge_runs",
]
