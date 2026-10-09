"""M2b.4 — TradingCalendar. When is each instrument tradable, in UTC, with no DST ambiguity.

WHY this exists: gap detection, backfill and "can I trade now" all need ONE answer to "was
this market open at T". Measured live 2026-09-27T04:40Z against Deriv's own
`exchange_is_open`: BTC open, all forex and all metals shut. Without a calendar, a 25-asset
mixed universe records ~17h of "lost data" every weekend, per instrument, forever.

TWO REAL BUGS FOUND AND FIXED HERE (both would have caused permanent, silent data loss):

  1. `SessionRule.window()` added a 5-day span AND wrapped the minute offset with `% 1440`.
     Metals (open Sun 22:00, close Fri 21:00) have a -60 minute offset; `% 1440` turned it
     into +1380, inflating a 119-hour week to 143. The spurious 24h landed on SATURDAY, so
     metals appeared to trade all Saturday - a closure misread as tradable, and therefore a
     permanent false data gap against a venue that never had those bars.
  2. `open_intervals()` originally probed the weekly window at midday, which returns the
     window CONTAINING midday. For Sunday that is LAST week's window, already closed on
     Friday - so Sunday 21:00-24:00 was reported shut, hiding 3h of real trading. Two
     candidate anchors (this week and last) are now checked.

Session hours come from the asset-class standard in config (M2b.5), NOT from a private table
here: a second copy is exactly how two answers drift apart.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from modules.reference.reference import UnknownInstrumentError, get_instrument

# WHY bounded: `next_open` must be a total function. A misconfigured rule that never opens
# would otherwise walk forever, and a calendar that hangs is worse than one that refuses.
_MAX_LOOKAHEAD_DAYS = 7
_MAX_INSTANT_YEAR = 9998

# Minimum/maximum weekday and minute-of-day, for config validation.
_MAX_WEEKDAY = 6
_MAX_MINUTE = 1439


class CalendarConfigError(ValueError):
    """Config is structurally invalid. Raised at LOAD time, never at query time.

    WHY load-time: a structurally broken config must stop the process at startup, not surface
    as a wrong trading answer three weeks later.
    """


def _require_aware(at: Any, label: str = "at") -> datetime:
    """Reject naive datetimes (AGENTS.md 3.8).

    WHY: a naive bound has no meaning. Whichever machine reads it invents a timezone and
    shifts the window by hours, which for a trading session is the whole answer.
    """
    if not isinstance(at, datetime):
        raise CalendarConfigError(f"{label} must be a datetime, got {type(at).__name__}")
    if at.tzinfo is None or at.utcoffset() is None:
        raise CalendarConfigError(f"{label} must be tz-aware (UTC); naive datetimes are rejected")
    at = at.astimezone(UTC)
    # WHY an upper bound: `at + timedelta(days=...)` overflows near datetime.max, and an
    # overflow here is an OverflowError from deep inside arithmetic rather than the clear
    # config error the caller needs. Refuse the absurd instant instead of hanging on it.
    if at.year > _MAX_INSTANT_YEAR:
        raise CalendarConfigError(
            f"{label} year {at.year} exceeds the supported maximum {_MAX_INSTANT_YEAR}"
        )
    return at


def _parse_hhmm(raw: Any, label: str) -> int:
    """'HH:MM' -> minutes from midnight UTC. Strict: rejects 25:00 and junk alike."""
    if not isinstance(raw, str) or ":" not in raw:
        raise CalendarConfigError(f"{label} must be 'HH:MM', got {raw!r}")
    hh, _, mm = raw.partition(":")
    if not (hh.isdigit() and mm.isdigit()):
        raise CalendarConfigError(f"{label} must be 'HH:MM', got {raw!r}")
    hours, minutes = int(hh), int(mm)
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise CalendarConfigError(f"{label} out of range: {raw!r}")
    return hours * 60 + minutes


def _parse_holiday(raw: Any) -> datetime:
    """A holiday is a CALENDAR DATE, not an instant.

    WHY a date: callers ask "was this market shut on 25 Dec?", and comparing full instants made
    a holiday match only at exactly midnight - so a declared holiday silently did nothing.
    """
    if isinstance(raw, datetime):
        return _require_aware(raw, "holiday")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw).replace(tzinfo=UTC)
        except ValueError as exc:
            raise CalendarConfigError(f"holiday {raw!r} is not an ISO date") from exc
    raise CalendarConfigError(f"holiday must be a date string or datetime, got {raw!r}")


def _require_known_broker(broker: Any) -> str:
    from modules.reference.reference import _KNOWN_BROKERS

    try:
        return _require_broker(broker)
    except Exception as exc:
        raise ValueError(f"unknown broker {broker!r}; known: {sorted(_KNOWN_BROKERS)}") from exc


def _require_broker(broker: Any) -> str:
    from modules.reference.reference import _require_broker as _rb

    return _rb(broker)


def _merge_intervals(
    spans: Iterable[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Sort and coalesce touching/overlapping [start, end) pairs.

    WHY: consecutive trading days produce adjacent spans (Mon ends 00:00, Tue starts 00:00).
    Left unmerged the caller sees a phantom closure at every midnight and would classify a
    continuously-open week as a week of gaps.
    """
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


@dataclass(frozen=True, slots=True)
class SessionRule:
    """One instrument's effective-dated trading rule. Frozen: callers cannot corrupt shared
    reference state, which the bible requires of every reference fact.

    TWO DISTINCT WINDOW KINDS - conflating them was a real bug:
      * `daily_windows` non-empty -> the market is open EVERY day during those UTC windows
        (e.g. 12:00-13:00), and shut otherwise.
      * `daily_windows` empty     -> a single WEEKLY window from open_weekday:open_minute
        to close_weekday:close_minute (e.g. Sun 21:00 -> Fri 21:00), shut outside it.
    """

    instrument_id: str
    asset_class: str
    always_open: bool
    open_weekday: int
    open_minute: int
    close_weekday: int
    close_minute: int
    effective_from: datetime | None
    daily_windows: tuple[tuple[int, int], ...] = ()
    #: ISO `YYYY-MM-DD` dates on which this class is shut ALL DAY, inherited from the class
    #: standard (`asset_classes.yaml: holidays`, derived from the venue's `trading_times`
    #: events rather than hand-kept). Per-INSTRUMENT holidays still arrive through the
    #: constructor and are checked separately; this set is the class-wide half.
    holidays: frozenset[str] = frozenset()
    #: ISO date -> minute-of-day at which trading shuts early on that date ONLY. A RESTRICTION
    #: and never an extension: it can only close the day earlier, never open it later. Metals
    #: close at 16:30 on 2026-11-26 / 2027-01-18 / 2027-02-15 while normally running to 21:00.
    early_closes: dict[str, int] = None  # type: ignore[assignment]
    #: weekday (0=Mon) -> ((open_minute, close_minute), ...). Present only when the venue's
    #: week is NOT one repeating span - e.g. metals, which runs two sessions Mon-Thu and ONE
    #: on Friday. A day absent from this map is SHUT, so the map is exhaustive by
    #: construction: forgetting Saturday means Saturday is closed, not open.
    weekday_windows: dict[int, tuple[tuple[int, int], ...]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # A frozen dataclass still needs real dicts for defaulted mutable fields; doing it
        # here keeps the defaults immutable at class level and avoids a shared-dict bug where
        # one rule's holidays or sessions would leak into another's.
        if self.early_closes is None:
            object.__setattr__(self, "early_closes", {})
        if self.weekday_windows is None:
            object.__setattr__(self, "weekday_windows", {})

    def sessions_on(self, at: datetime) -> tuple[tuple[int, int], ...] | None:
        """The windows for `at`'s weekday, or None when no per-weekday schedule is in force.

        None (not an empty tuple) means "this rule does not use a per-weekday schedule", so
        the caller falls back to the weekly window. An EMPTY tuple would mean "shut all day",
        which is a different fact and must not be conflated.
        """
        if not self.weekday_windows:
            return None
        return self.weekday_windows.get(at.weekday(), ())

    def in_weekday_window(self, at: datetime) -> bool:
        """Minute-of-day membership in that weekday's windows. Half-open, so the exact close
        minute is already shut. A day with no entry is shut all day."""
        minute = at.hour * 60 + at.minute
        return any(
            start <= minute < end for start, end in (self.weekday_windows.get(at.weekday(), ()))
        )

    def closed_early_at(self, at: datetime) -> int | None:
        """The early close minute for `at`'s date, or None if it is an ordinary day."""
        return self.early_closes.get(at.date().isoformat())

    def is_holiday(self, at: datetime) -> bool:
        return at.date().isoformat() in self.holidays

    def window(self, at: datetime) -> tuple[datetime, datetime]:
        """The concrete [open, close) WEEKLY window containing `at`, in UTC.

        WHY anchor backwards from `at` to the most recent open weekday: a weekly window is
        REPEATING, so the only well-defined window for a given instant is the one containing
        it. An earlier revision anchored FORWARD from the top of `at`'s day, which silently
        shifted every boundary by a week.
        """
        midnight = at.replace(hour=0, minute=0, second=0, microsecond=0)
        days_back = (at.weekday() - self.open_weekday) % 7
        opens = midnight - timedelta(days=days_back) + timedelta(minutes=self.open_minute)
        if opens > at:
            opens -= timedelta(days=7)
        span_days = (self.close_weekday - self.open_weekday) % 7 or 7
        # WHY the offset is SIGNED and not `% 1440`: it is a correction to the day span, and
        # wrapping a negative offset into a positive one adds a spurious 24 hours. For metals
        # (open Sun 22:00, close Fri 21:00) the offset is -60 min; wrapping it to 1380 turned a
        # 119-hour week into 143, and the extra day landed on SATURDAY - so metals appeared to
        # trade all Saturday. A closure misread as tradable is a permanent false data gap.
        closes = opens + timedelta(days=span_days, minutes=self.close_minute - self.open_minute)
        return opens, closes

    def in_daily_window(self, at: datetime) -> bool:
        """Minute-of-day membership in an explicit daily window. Half-open, so the exact
        close minute is already shut."""
        minute = at.hour * 60 + at.minute
        return any(start <= minute < end for start, end in self.daily_windows)


class TradingCalendar:
    """Immutable calendar. Build with `load_calendar`, query with the module functions."""

    def __init__(
        self,
        *,
        overrides: Mapping[str, Mapping[str, Any]] | None = None,
        holidays: Mapping[str, Iterable[Any]] | None = None,
        sessions: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
        class_resolver: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        # WHY injectable (found by the M2b.5 add/remove proof, 2026-09-27): the class used to
        # be resolved through the module-global `get_instrument`, so an instrument that had
        # just been added to the master was invisible to the calendar - the add succeeded and
        # the calendar then raised UnknownInstrumentError. A hidden global is also untestable
        # without monkeypatching, and this project forbids incidental test setups.
        self._class_resolver: Callable[[str], Mapping[str, Any]] = class_resolver or get_instrument
        # WHY per-instrument override stored verbatim and resolved at QUERY time: a
        # historical lookup must use the rule in force AT THAT TIME, not today's rule.
        self._overrides = {k: dict(v) for k, v in (overrides or {}).items()}
        self._holidays: dict[str, set[datetime]] = {
            k: {_parse_holiday(d) for d in v} for k, v in (holidays or {}).items()
        }
        # WHY precomputed: `is_open` runs on every gap/coverage query, and rebuilding this
        # date set per call allocated a new set each time. `_holidays` is never mutated after
        # __init__, so deriving the dates once is safe and removes the per-call allocation.
        self._holiday_dates: dict[str, frozenset[Any]] = {
            k: frozenset(d.date() for d in v) for k, v in self._holidays.items()
        }
        self._sessions = self._validate_sessions(sessions or {})
        self._rules: dict[tuple[str, datetime | None], SessionRule] = {}

    @staticmethod
    def _validate_sessions(sessions: Mapping[str, Iterable[Mapping[str, Any]]]) -> dict:
        """Validate at LOAD time, never at query time.

        WHY load-time: a structurally broken config must stop the process at startup, not
        surface as a wrong trading answer three weeks later.
        """
        out: dict[str, dict] = {}
        for instrument, windows in sessions.items():
            parsed = []
            for w in windows:
                opens = _parse_hhmm(w.get("open"), f"{instrument} session open")
                closes = _parse_hhmm(w.get("close"), f"{instrument} session close")
                # WHY reject rather than swap: a reversed or zero-length window is ambiguous
                # (0h or 24h?) and silently guessing is how a market trades while shut.
                if opens == closes:
                    raise CalendarConfigError(
                        f"{instrument}: session open == close ({w.get('open')}) - ambiguous"
                    )
                if closes < opens:
                    raise CalendarConfigError(
                        f"{instrument}: session close {w.get('close')} precedes open {w.get('open')}"
                    )
                parsed.append({"open": opens, "close": closes})
            out[instrument] = {"windows": parsed}
        return out

    # -- rule resolution ------------------------------------------------
    def _class_of(self, instrument_id: str) -> str:
        # Let UnknownInstrumentError propagate deliberately: spec invariant 3 says an unknown
        # instrument is a typed error, never a silent "assume closed/open".
        resolved = self._class_resolver(instrument_id)
        if not isinstance(resolved, Mapping):
            raise UnknownInstrumentError(
                f"{instrument_id}: class_resolver must return a mapping, got "
                f"{type(resolved).__name__}"
            )
        return str(resolved.get("asset_class") or "")

    def rule_for(self, instrument_id: str, at: datetime) -> SessionRule:
        at = _require_aware(at)
        asset_class = self._class_of(instrument_id)
        override = self._overrides.get(instrument_id, {})

        # Effective dating: pick the LATEST override whose effective_from is <= at. A
        # historical lookup therefore uses the historical rule.
        effective_from: datetime | None = None
        applicable = True
        if "effective_from" in override:
            effective_from = _require_aware(override["effective_from"], "effective_from")
            applicable = effective_from <= at

        default = dict(_class_defaults().get(asset_class) or {})
        if not default:
            raise UnknownInstrumentError(
                f"no calendar rule for asset_class {asset_class!r} ({instrument_id})"
            )
        merged = {**default, **{k: v for k, v in override.items() if k != "effective_from"}}

        # An explicit window list (from config or tests) beats the weekday formula.
        windows = self._sessions.get(instrument_id, {}).get("windows") or []
        if windows:
            return SessionRule(
                instrument_id=instrument_id,
                asset_class=asset_class,
                always_open=False,
                open_weekday=6,
                open_minute=windows[0]["open"],
                close_weekday=6,
                close_minute=windows[-1]["close"],
                effective_from=effective_from,
                daily_windows=tuple((w["open"], w["close"]) for w in windows),
            )

        return SessionRule(
            instrument_id=instrument_id,
            asset_class=asset_class,
            always_open=bool(merged.get("always_open", False)) and applicable,
            open_weekday=int(merged.get("open_weekday", 6)),
            open_minute=int(merged.get("open_minute", 0)),
            close_weekday=int(merged.get("close_weekday", 4)),
            close_minute=int(merged.get("close_minute", 0)),
            effective_from=effective_from,
            # Class-wide dated exceptions, INHERITED from the class standard. A per-instrument
            # override may add to them but never shrink them: a class-level closure is a fact
            # about the venue's schedule, and one instrument cannot be "open" on Christmas.
            holidays=frozenset(merged.get("holidays") or ()),
            early_closes={
                k: int(v) for k, v in (merged.get("early_closes") or {}).items()
            },
            weekday_windows={
                int(day): tuple((int(a), int(b)) for a, b in wins)
                for day, wins in (merged.get("weekday_windows") or {}).items()
            },
        )

    def is_open(self, instrument_id: str, at: datetime, broker: str = "deriv") -> bool:
        """Is this instrument tradable at `at`? Total function: always a real bool.

        WHY the broker is validated rather than ignored: silently answering for the wrong
        venue's hours is worse than refusing. Every venue here can have different sessions.
        """
        at = _require_aware(at)
        _require_known_broker(broker)
        rule = self.rule_for(instrument_id, at)

        # A holiday is a CLOSURE and beats any session window. Two sources, both checked:
        # a per-INSTRUMENT set passed to the constructor, and the class-wide set inherited
        # from the class standard.
        if at.date() in self._holiday_dates.get(instrument_id, frozenset()):
            return False
        if rule.is_holiday(at):
            return False

        # A dated EARLY CLOSE is a restriction and never an extension: it can only shut the
        # day sooner, never open it later. Checked BEFORE the always_open shortcut, because a
        # venue maintenance closure applies to a 24/7 class too - crypto simply publishes none.
        early = rule.closed_early_at(at)
        if early is not None and (at.hour * 60 + at.minute) >= early:
            return False

        if rule.always_open:
            return True

        # A per-WEEKDAY schedule supersedes both the weekly window and daily_windows: it is
        # the only one of the three that can express "two sessions Mon-Thu, one on Friday".
        # Checked before daily_windows so a class that declares it is unambiguous.
        if rule.weekday_windows:
            return rule.in_weekday_window(at)

        # An explicit daily window is a DIFFERENT concept from the weekly window: it is
        # membership in a minute-of-day range, not containment in a spanning week.
        if rule.daily_windows:
            return rule.in_daily_window(at)

        opens, closes = rule.window(at)
        # Half-open interval: open boundary inclusive, close boundary EXCLUSIVE, so the exact
        # instant of close is already shut.
        return opens <= at < closes

    def next_open(self, instrument_id: str, at: datetime, broker: str = "deriv") -> datetime:
        """The earliest instant STRICTLY AFTER `at` at which trading is open.

        WHY strictly after and on a 1-minute grid: returning `at` itself would make a
        caller's retry loop non-terminating, and an unbounded walk on a misconfigured rule is a
        hang. WHY a grid and not a boundary jump: the honest answer to "when can I trade next"
        is the first tradable minute, which is also the only definition that behaves
        identically for an always-open instrument and a windowed one.
        """
        at = _require_aware(at)
        _require_known_broker(broker)
        deadline = at + timedelta(days=_MAX_LOOKAHEAD_DAYS)
        cursor = at
        step = timedelta(minutes=1)
        while cursor < deadline:
            cursor += step
            if self.is_open(instrument_id, cursor, broker=broker):
                return cursor
        raise ValueError(
            f"{instrument_id}: no open boundary within {_MAX_LOOKAHEAD_DAYS} days of {at.isoformat()}"
        )

    def session_for(self, instrument_id: str, at: datetime, broker: str = "deriv") -> SessionRule:
        return self.rule_for(instrument_id, _require_aware(at))

    def open_intervals(
        self, instrument_id: str, start: datetime, end: datetime, broker: str = "deriv"
    ) -> list[tuple[datetime, datetime]]:
        """The OPEN sub-intervals of [start, end), as sorted non-overlapping pairs.

        WHY this exists: the first gap-detection build asked "is this minute open?" once per
        minute. A ten-year window is ~5.2M calls, which does not finish. Callers that need to
        know WHICH PARTS of a window are tradable must ask for intervals, so the cost is
        O(days in window) instead of O(minutes in window).

        WHY it lives here and not in the gap module: this is session arithmetic. Deriving it
        from `SessionRule` in one place means a second copy cannot drift out of step with
        `is_open` - and a drifting second copy would silently mislabel market closure as data
        loss, which is the exact failure this whole slice exists to prevent.
        """
        start = _require_aware(start, "start")
        end = _require_aware(end, "end")
        _require_known_broker(broker)
        if end <= start:
            return []

        spans: list[tuple[datetime, datetime]] = []
        day = start.replace(hour=0, minute=0, second=0, microsecond=0)
        holiday_dates = self._holiday_dates.get(instrument_id, frozenset())

        while day < end:
            next_day = day + timedelta(days=1)
            lo, hi = max(day, start), min(next_day, end)
            # WHY the rule is resolved FIRST and the exceptions read off IT, rather than only
            # the per-instrument holiday set: the class standard now carries venue-derived
            # holidays and dated early closes, and `is_open` honours all three sources. If
            # this function consulted only `self._holiday_dates` the two would DISAGREE - and
            # the docstring above says a drifting second copy "would silently mislabel market
            # closure as data loss, which is the exact failure this whole slice exists to
            # prevent". Two agreeing implementations beat one correct and one stale.
            rule = self.rule_for(instrument_id, day)
            is_holiday = (
                day.date() in holiday_dates or rule.is_holiday(day)
            )
            early = rule.closed_early_at(day)
            if early is not None:
                # A restriction, never an extension: clip the DAY's span to the early close.
                hi = min(hi, day + timedelta(minutes=early))
            if lo < hi and not is_holiday:
                if rule.always_open:
                    spans.append((lo, hi))
                elif rule.weekday_windows:
                    # Same source of truth as `is_open`, read off the SAME rule - the two
                    # must not drift, or closure gets mislabelled as data loss.
                    for open_min, close_min in rule.weekday_windows.get(day.weekday(), ()):
                        a = max(day + timedelta(minutes=open_min), lo)
                        b = min(day + timedelta(minutes=close_min), hi)
                        if a < b:
                            spans.append((a, b))
                elif rule.daily_windows:
                    for open_min, close_min in rule.daily_windows:
                        a = max(day + timedelta(minutes=open_min), lo)
                        b = min(day + timedelta(minutes=close_min), hi)
                        if a < b:
                            spans.append((a, b))
                else:
                    # Weekly window. TWO candidate anchors are required, not one.
                    # WHY: a weekly session can BEGIN mid-week (forex opens Sunday 21:00).
                    # A single probe at midday returns the window CONTAINING midday - which
                    # for Sunday midday is LAST week's window, already closed on Friday. Using
                    # only that silently reported Sunday 21:00-24:00 as shut, i.e. it hid
                    # three hours of real trading. Checking this week's and last week's anchor
                    # covers both "session covers this day" and "session starts on this day".
                    days_back = (day.weekday() - rule.open_weekday) % 7
                    anchor = day - timedelta(days=days_back) + timedelta(minutes=rule.open_minute)
                    for opens in (anchor, anchor - timedelta(days=7)):
                        closes = rule.window(opens + timedelta(hours=12))[1]
                        a, b = max(opens, lo), min(closes, hi)
                        if a < b:
                            spans.append((a, b))
            day = next_day

        return _merge_intervals(spans)

    def open_minutes(self, instrument_id: str, start: datetime, end: datetime,
                     broker: str = "deriv") -> int:
        """Count of tradable whole minutes in [start, end). Clipped to the window, so a
        partial window is never over-counted."""
        total = 0
        for a, b in self.open_intervals(instrument_id, start, end, broker=broker):
            lo, hi = max(a, start), min(b, end)
            if hi > lo:
                total += int((hi - lo).total_seconds() // 60)
        return total


_DEFAULT: TradingCalendar | None = None


def _default() -> TradingCalendar:
    """Process-wide calendar, built once. Shared reference data, never per-tenant -
    the bible requires reference lookups be tenant-agnostic."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = TradingCalendar()
    return _DEFAULT


def is_open(instrument_id: str, at: datetime, broker: str = "deriv") -> bool:
    """Module-level facade, matching the bible's `reference.is_open(...)` shape."""
    return _default().is_open(instrument_id, at, broker=broker)


def next_open(instrument_id: str, at: datetime, broker: str = "deriv") -> datetime:
    return _default().next_open(instrument_id, at, broker=broker)


def session_for(instrument_id: str, at: datetime, broker: str = "deriv") -> SessionRule:
    return _default().session_for(instrument_id, at, broker=broker)


def open_intervals(
    instrument_id: str, start: datetime, end: datetime, broker: str = "deriv"
) -> list[tuple[datetime, datetime]]:
    """Module-level facade for the O(days) interval query. See TradingCalendar.open_intervals."""
    return _default().open_intervals(instrument_id, start, end, broker=broker)


def open_minutes(instrument_id: str, start: datetime, end: datetime, broker: str = "deriv") -> int:
    """Module-level facade for the tradable-minute count. Added for API symmetry: every other
    calendar query has a module-level entrypoint, and a verify script reaching for the class
    directly is a sign the facade is incomplete."""
    return _default().open_minutes(instrument_id, start, end, broker=broker)


def load_calendar(**kwargs: Any) -> TradingCalendar:
    """Build an explicit calendar (tests, or config-loaded production instance)."""
    return TradingCalendar(**kwargs)


def broker_agrees(
    instrument_id: str, at: datetime, *, calendar_open: bool, broker_open: bool
) -> str | None:
    """Cross-check the static calendar against the venue's live `exchange_is_open`.

    WHY this exists rather than trusting either side: the calendar is a configured fact that
    can rot, and the broker flag is authoritative only for the present. Disagreement is
    REPORTED, never silently resolved - an override here would hide a config bug until it
    caused a missed trade.
    """
    if calendar_open == broker_open:
        return None
    verb = "open" if broker_open else "shut"
    return (
        f"calendar/broker disagree for {instrument_id} at {at.isoformat()}: "
        f"calendar says {'open' if calendar_open else 'shut'}, broker says {verb}"
    )


def _class_defaults() -> dict[str, dict[str, Any]]:
    """Class -> session shape, read from the asset-class standard CONFIG (M2b.5).

    WHY resolved lazily on every rule lookup rather than bound at import: binding it at import
    made a newly added instrument or class invisible to the calendar for the life of the
    process. Reading through a function also lets config hot-reload (M1 SIGHUP) change hours
    without a restart.
    """
    from modules.reference.standards import session_defaults

    return session_defaults()


__all__ = [
    "CalendarConfigError",
    "SessionRule",
    "TradingCalendar",
    "broker_agrees",
    "is_open",
    "load_calendar",
    "next_open",
    "open_intervals",
    "session_for",
]
