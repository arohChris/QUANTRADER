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

from dataclasses import dataclass
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

#: Keys a class entry may carry. Anything else is a TYPO and is rejected - a silently ignored
#: `open_minte:` would leave a class with no open time and no error.
ALLOWED_KEYS = frozenset({
    "always_open", "open_weekday", "open_minute", "close_weekday", "close_minute",
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
