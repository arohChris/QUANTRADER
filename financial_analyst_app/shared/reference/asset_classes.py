"""Asset-class standard: SCHEMA and VALIDATION. The data itself lives in config.

WHY this module holds no data (human directive 2026-09-27): *"everything that can be
configured should be made configurable, especially things that have the potential to be
expanded."* A hardcoded table in Python makes adding an asset class a code change and a
deploy, which is the wrong shape for the part of the system whose purpose is to grow. So this
module owns the SHAPE; `config/reference/asset_classes.yaml` owns the VALUES.

WHY no import-time file I/O here: `shared` must not know the application's config path, and a
module that reads a file on import is a module whose behaviour depends on the working
directory. `modules/reference/standards.py` owns the path and the single process-wide load.

INHERITED vs PER-INSTRUMENT - the distinction that keeps this honest:

  * session hours  -> INHERITED, never per-instrument. One venue, one session per class.
    Allowing a per-instrument exception is how two tables drift apart again.
  * contract_size  -> PER-INSTRUMENT and always required. Gold is 100oz, Silver 5000oz. A
    real difference, not an inconsistency; "standardise everything" would be wrong.
  * tick_size      -> class default, overridable per instrument (crypto pairs differ).
  * min_qty/step   -> class defaults, overridable (venue lot rules differ).

MONEY TYPING: money values are authored as QUOTED strings and become `Decimal` built from that
string. `AGENTS.md` 3.1 forbids `float` in the money path. By the time YAML has parsed `0.01`
it is already a float, and `Decimal(str(0.01))` only recovers the author's literal because
Python's float repr happens to be shortest-round-trip - a coincidence, not a guarantee. So
unquoted numbers are REJECTED rather than converted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = [
    "ALLOWED_KEYS",
    "MAX_MINUTE",
    "MAX_WEEKDAY",
    "REQUIRED_KEYS",
    "AssetClassStandard",
    "AssetStandardError",
    "money_decimal",
    "parse_standard",
]


class AssetStandardError(ValueError):
    """The asset-class standard, or a value read against it, is invalid.

    WHY this is a hard failure rather than a warning: in configuration the dangerous outcome
    is not "it does not work", it is "it works and is quietly wrong" - an instrument given
    the wrong trading week is traded at the weekend, or refused while it is open.
    """


#: 0 = Monday .. 6 = Sunday, matching `datetime.weekday()`.
MAX_WEEKDAY = 6
#: Minutes from UTC midnight; a session cannot start or end outside one day.
MAX_MINUTE = 1439

#: An ISO calendar date. Used to reject a typo'd holiday/early-close key at BOOT, because a
#: silently-ignored date is a closure the calendar thinks is a trading day.
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

#: Keys a class entry may carry. Anything else is a TYPO and is rejected - a silently ignored
#: `open_minte:` would leave a class with no open time and no error.
ALLOWED_KEYS = frozenset({
    "always_open", "open_weekday", "open_minute", "close_weekday", "close_minute",
    "holidays", "early_closes", "weekday_windows",
    "price_dp", "tick_size", "min_qty", "qty_step",
})

#: Money keys every class must define, so a class can never be half-specified.
REQUIRED_KEYS = frozenset({"tick_size", "min_qty", "qty_step"})

# Exactness is not sanity: `Decimal("1e400")` is finite and exact, and would round every
# price to a multiple of 1e400 - destroying the data while looking perfectly valid.
MIN_TICK_SIZE = Decimal("0.000000000001")
MAX_TICK_SIZE = Decimal("1")  # noqa: FURB157 - money from str per AGENTS.md 3.1

# Values `Decimal` would accept but that are meaningless as money. `Decimal("nan")` parses
# successfully and poisons every downstream comparison.
_POISON = frozenset({"nan", "snan", "inf", "infinity", "-inf", "-infinity"})


def _holiday_dates(raw: Any, name: str) -> frozenset[str]:
    """Validate a list of ISO closure dates -> frozenset of `YYYY-MM-DD` strings.

    WHY a string and not a `date`: the calendar compares against `at.date().isoformat()`, and
    keeping the wire form means a config value round-trips visibly instead of being
    reformatted on load.

    Every failure is a TYPED boot error, never a silently-ignored entry. A holiday list that
    half-parses is worse than no holiday list, because it looks configured.
    """
    if raw is None:
        return frozenset()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple, set, frozenset)):
        raise AssetStandardError(
            f"class {name!r}: holidays must be a list of 'YYYY-MM-DD' strings, got "
            f"{type(raw).__name__}"
        )
    out: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not ISO_DATE.fullmatch(item.strip()):
            raise AssetStandardError(
                f"class {name!r}: holiday {item!r} is not an ISO 'YYYY-MM-DD' date"
            )
        try:
            date.fromisoformat(item.strip())
        except ValueError as exc:
            raise AssetStandardError(
                f"class {name!r}: holiday {item!r} is not a real calendar date ({exc})"
            ) from exc
        out.add(item.strip())
    return frozenset(out)


def _early_closes(raw: Any, name: str) -> dict[str, int]:
    """Validate a {ISO date: close minute-of-day} map -> plain dict.

    A dated early close (the venue closes metals at 16:30 on certain dates) is NOT the same
    fact as a weekly close, and cannot be expressed by `close_weekday`/`close_minute` because
    those repeat every week. The value is minutes from UTC midnight, matching
    `open_minute`/`close_minute`, so one convention governs every time in this file.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AssetStandardError(
            f"class {name!r}: early_closes must be a mapping 'YYYY-MM-DD' -> minute-of-day, "
            f"got {type(raw).__name__}"
        )
    out: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not ISO_DATE.fullmatch(key.strip()):
            raise AssetStandardError(
                f"class {name!r}: early_closes key {key!r} is not an ISO 'YYYY-MM-DD' date"
            )
        try:
            date.fromisoformat(key.strip())
        except ValueError as exc:
            raise AssetStandardError(
                f"class {name!r}: early_closes key {key!r} is not a real calendar date ({exc})"
            ) from exc
        if isinstance(value, bool) or not isinstance(value, int):
            raise AssetStandardError(
                f"class {name!r}: early_closes[{key!r}] must be an int minute-of-day, got "
                f"{type(value).__name__}"
            )
        if not 0 <= value <= MAX_MINUTE:
            raise AssetStandardError(
                f"class {name!r}: early_closes[{key!r}] = {value} is outside [0, {MAX_MINUTE}]"
            )
        out[key.strip()] = value
    return out


def _weekday_windows(raw: Any, name: str) -> dict[int, tuple[tuple[int, int], ...]]:
    """Validate a PER-WEEKDAY schedule -> {weekday: ((open, close), ...)}.

    WHY THIS EXISTS (venue-measured 2026-09-30)
    -------------------------------------------
    A single weekly window and a plain `daily_windows` list both fail on the same real
    schedule. Metals at this venue runs **Mon-Thu 00:00-21:00 AND 22:00-24:00** (two
    sessions with a one-hour break) but **Friday 00:00-20:55** (ONE session - the venue
    publishes a single close for Friday, so the evening session does not happen).

    * one weekly window cannot express two sessions, and
    * `daily_windows` cannot express a per-day difference, and worse, per calendar.py's own
      docstring a non-empty `daily_windows` means "open EVERY day" with no weekday filter.

    Modelling the Mon-Thu sessions alone would have been the half-fix that makes the audit
    WORSE: the calendar would believe metals trade through 21:00-22:00 (reporting the break
    as lost data) and would also believe the Friday evening session exists (reporting its
    absence as lost data). So the model has to carry a per-weekday schedule, or the metals
    numbers stay wrong in a new way.

    This is ADDITIVE and OPTIONAL. A class without it keeps the existing weekly-window path
    untouched, which is deliberate: forex's window is single-session and already covered by
    goldens, so there is no reason to move it onto a new code path.

    Validation is strict and happens at BOOT: a schedule that half-parses is worse than none,
    because it looks configured while quietly mislabelling closure as data loss.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AssetStandardError(
            f"class {name!r}: weekday_windows must be a mapping weekday -> windows, got "
            f"{type(raw).__name__}"
        )
    out: dict[int, tuple[tuple[int, int], ...]] = {}
    for key, windows in raw.items():
        if isinstance(key, bool):
            raise AssetStandardError(
                f"class {name!r}: weekday_windows key {key!r} must be an int weekday 0..6"
            )
        try:
            weekday = int(key)
        except (TypeError, ValueError) as exc:
            raise AssetStandardError(
                f"class {name!r}: weekday_windows key {key!r} must be an int weekday 0..6"
            ) from exc
        if not 0 <= weekday <= 6:
            raise AssetStandardError(
                f"class {name!r}: weekday_windows key {weekday} outside 0..6 "
                "(0=Mon .. 6=Sun)"
            )
        if not isinstance(windows, (list, tuple)) or not windows:
            raise AssetStandardError(
                f"class {name!r}: weekday_windows[{weekday}] must be a non-empty list of "
                f"windows, got {type(windows).__name__}"
            )
        parsed: list[tuple[int, int]] = []
        for window in windows:
            if isinstance(window, dict):
                o: Any = window.get("open")
                c: Any = window.get("close")
            elif isinstance(window, (list, tuple)) and len(window) == 2:
                o, c = window[0], window[1]
            else:
                raise AssetStandardError(
                    f"class {name!r}: weekday_windows[{weekday}] entry {window!r} must be "
                    "a [open, close] pair or an {open, close} mapping"
                )
            for label, value in (("open", o), ("close", c)):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise AssetStandardError(
                        f"class {name!r}: weekday_windows[{weekday}].{label} must be an int "
                        f"minute-of-day, got {type(value).__name__}"
                    )
                if not 0 <= value <= MAX_MINUTE + 1:
                    raise AssetStandardError(
                        f"class {name!r}: weekday_windows[{weekday}].{label} = {value} is "
                        f"outside [0, {MAX_MINUTE + 1}]"
                    )
            if o >= c:
                raise AssetStandardError(
                    f"class {name!r}: weekday_windows[{weekday}] has an empty or reversed "
                    f"session {o}->{c}; a zero-length session can never be open and would "
                    "silently mark a whole day as data loss"
                )
            parsed.append((o, c))
        parsed.sort()
        # Overlapping or ADJACENT windows are merged; a genuine gap between sessions is
        # preserved. Merging is what stops two spellings of the same session becoming two
        # spans that a caller might double-count.
        merged: list[list[int]] = []
        for o, c in parsed:
            if merged and o <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], c)
            else:
                merged.append([o, c])
        out[weekday] = tuple((a, b) for a, b in merged)
    return out


@dataclass(frozen=True, slots=True)
class AssetClassStandard:
    """Everything the class decides. Frozen: reference data must not be mutable at runtime."""

    name: str
    #: Consumed by modules/reference/calendar.py. `always_open` wins outright; otherwise the
    #: weekday/minute fields describe a 7-day repeating window.
    session: dict[str, Any]
    price_dp: int
    tick_size: Decimal
    min_qty: Decimal
    qty_step: Decimal
    #: Fields an individual instrument MAY override. `contract_size` is deliberately absent -
    #: it is per-instrument by nature, not a class default with an escape hatch.
    overridable: frozenset[str] = frozenset({"tick_size", "min_qty", "qty_step"})


def money_decimal(raw: Any, *, field: str = "value") -> Decimal:
    """Parse an AUTHORED money literal into an exact, finite, positive `Decimal`.

    Rejects, naming the field:
      * `float` / `int` - a YAML number is already lossy by the time we see it (AGENTS.md 3.1)
      * `""`, `" 0.01 "`, `"0.01USD"`, `"-0"`, `"0.0.1"` - malformed or ambiguous author intent
      * `"nan"`, `"Infinity"` - parse fine, poison every comparison
    """
    # bool is an int subclass, so it must be caught by the same branch deliberately.
    if isinstance(raw, (bool, int, float)):
        raise AssetStandardError(
            f"{field}: must be a QUOTED string, got {type(raw).__name__} {raw!r}. "
            f'Quote it (e.g. "{raw}") so it is never parsed as a float - AGENTS.md 3.1.'
        )
    if not isinstance(raw, str):
        raise AssetStandardError(f"{field}: must be a quoted string, got {type(raw).__name__}")
    if raw == "":
        raise AssetStandardError(f"{field}: must not be empty")
    # WHY refuse surrounding whitespace rather than strip it: " 0.01 " is ambiguous authoring,
    # and silently accepting it hides a typo in a file nobody reads by eye.
    if raw != raw.strip():
        raise AssetStandardError(f"{field}: {raw!r} has surrounding whitespace")
    if raw.lower() in _POISON:
        raise AssetStandardError(f"{field}: {raw!r} is not a finite decimal")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise AssetStandardError(f"{field}: {raw!r} is not a valid decimal") from exc
    if not value.is_finite():
        raise AssetStandardError(f"{field}: {raw!r} overflows to a non-finite Decimal")
    if value <= 0:
        raise AssetStandardError(f"{field}: must be > 0, got {raw!r}")
    return value


def _bounded_int(raw: Any, *, field: str, low: int, high: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise AssetStandardError(f"{field}: must be an integer, got {raw!r}")
    if not low <= raw <= high:
        raise AssetStandardError(f"{field}: {raw} is outside [{low}, {high}]")
    return raw


def parse_standard(name: str, data: Any) -> AssetClassStandard:
    """Validate ONE class entry from config into a frozen standard. Fails loudly.

    Every rejection names the class and the key, because the person fixing it is reading a
    boot log, not the code.
    """
    if not isinstance(data, dict):
        raise AssetStandardError(f"class {name!r}: entry must be a mapping, got {type(data).__name__}")

    unknown = sorted(set(data) - ALLOWED_KEYS)
    if unknown:
        raise AssetStandardError(
            f"class {name!r}: unknown key(s) {unknown}; allowed: {sorted(ALLOWED_KEYS)}. "
            "A typo here would silently disable the rule it was meant to set."
        )
    missing = sorted(REQUIRED_KEYS - set(data))
    if missing:
        raise AssetStandardError(f"class {name!r}: missing required key(s) {missing}")

    always_open = data.get("always_open", False)
    if not isinstance(always_open, bool):
        raise AssetStandardError(f"class {name!r}: always_open must be true/false, got {always_open!r}")
    weekday_keys = {"open_weekday", "open_minute", "close_weekday", "close_minute"}
    present = weekday_keys & set(data)
    if always_open and present:
        # Two contradictory answers and no safe way to choose. Refuse rather than pick.
        raise AssetStandardError(
            f"class {name!r}: always_open contradicts {sorted(present)} - a 24/7 market has "
            "no open/close minutes. Remove one of the two."
        )
    if not always_open:
        absent = sorted(weekday_keys - set(data))
        if absent:
            raise AssetStandardError(
                f"class {name!r}: a windowed class needs all of {sorted(weekday_keys)}; "
                f"missing {absent}"
            )

    session: dict[str, Any] = {"always_open": always_open}
    if not always_open:
        session["open_weekday"] = _bounded_int(
            data["open_weekday"], field=f"class {name!r}: open_weekday", low=0, high=MAX_WEEKDAY)
        session["close_weekday"] = _bounded_int(
            data["close_weekday"], field=f"class {name!r}: close_weekday", low=0, high=MAX_WEEKDAY)
        session["open_minute"] = _bounded_int(
            data["open_minute"], field=f"class {name!r}: open_minute", low=0, high=MAX_MINUTE)
        session["close_minute"] = _bounded_int(
            data["close_minute"], field=f"class {name!r}: close_minute", low=0, high=MAX_MINUTE)
    # Dated exceptions, added 2026-09-30. The weekly window alone cannot express a closure
    # that happens on specific calendar dates, and the venue publishes those dates itself via
    # `trading_times` -> `events`. Without them a 250-day load spanning Christmas and New Year
    # reports every closed minute as LOST DATA, which is the same class of error as the old
    # "Sun 21:00" window: the calendar disagreeing with the venue about when trading happens.
    # Derived by scripts/derive_venue_calendar_exceptions.py, never hand-maintained.
    session["holidays"] = _holiday_dates(data.get("holidays", ()), name)
    session["early_closes"] = _early_closes(data.get("early_closes", {}) or {}, name)
    # A per-weekday schedule, when the venue's week is not one repeating span. Absent for
    # crypto (24/7) and for any single-session class, which keeps the tested weekly-window
    # path in use for them.
    session["weekday_windows"] = _weekday_windows(data.get("weekday_windows"), name)

    price_dp = _bounded_int(data.get("price_dp", 0), field=f"class {name!r}: price_dp",
                            low=0, high=18)
    tick = money_decimal(data["tick_size"], field=f"class {name!r}: tick_size")
    min_qty = money_decimal(data["min_qty"], field=f"class {name!r}: min_qty")
    qty_step = money_decimal(data["qty_step"], field=f"class {name!r}: qty_step")

    if not MIN_TICK_SIZE <= tick < MAX_TICK_SIZE:
        raise AssetStandardError(
            f"class {name!r}: tick_size {tick} is outside [{MIN_TICK_SIZE}, {MAX_TICK_SIZE}) - "
            "prices would be rounded to nonsense"
        )
    if min_qty > qty_step:
        raise AssetStandardError(
            f"class {name!r}: min_qty {min_qty} exceeds qty_step {qty_step} - no order could "
            "ever be placed"
        )

    return AssetClassStandard(
        name=name, session=session, price_dp=price_dp,
        tick_size=tick, min_qty=min_qty, qty_step=qty_step,
    )
