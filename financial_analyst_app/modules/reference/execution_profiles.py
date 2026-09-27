"""M2b.3 — Execution cost FACTS, effective-dated, per (instrument_id, broker).

WHY this exists: AGENTS.md 3.4 requires a backtest to include realistic costs, and a backtest
without costs is rejected. Costs are therefore not an optional embellishment - they are a
fact the system must know, stored once and served to the backtest and execution layers.

This module stores DESCRIPTIVE FACTS ONLY. It computes nothing: no notional, no cost, no
expectancy. Applying a rate to a trade is M8's job (spec "Out of scope"). Keeping the
computation out is what stops a fact table from quietly becoming a pricing engine.

MONEY LAW (spec REV3, 2026-09-27, human-approved): every money value is a QUOTED string in
config and becomes a `Decimal` built from that string. An unquoted YAML number is REJECTED.
By the time YAML has parsed `0.0000001` it is already a binary float, and `Decimal(str(f))`
recovers the author's literal only because CPython's float repr is shortest-round-trip - a
property of the runtime, not a guarantee of the format. `M2b.5` enforces the same rule in
`shared/reference/asset_classes.py`; before REV3 the system carried two opposite money laws.

UNITS AND SIGNS, stated once (AGENTS.md 3.1 requires this exactly once):
  * `commission_rate` — fraction of notional, NOTIONAL currency. `Decimal("0.0001")` = 1 bp.
  * `typical_spread`  — price units, QUOTE currency per base (not a fraction).
  * `financing_apy`   — annualised rate as a fraction, SIGNED: negative = cost of carry.
  * `slippage.value`  — basis points of notional, NOTIONAL currency.

CONTRACT: no `shared/contracts` change and no version bump. Models are frozen and in-module
(precedent: M2b.2 `load_snapshot_id`, M4.3 in-module `SignalRejection`). Typed errors are
REUSED from `shared.contracts.errors` - adding a new error class would be a frozen-contract
change, which is banned in this slice.

Consumers (future, per spec 5): M8 backtest cost model, M6 order-intent planner.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from shared.contracts.errors import ReferenceValidationError

__all__ = [
    "ExecutionProfile",
    "SlippageParams",
    "get_execution_profile",
    "load_execution_profiles",
]


def _get_instrument(instrument_id: str) -> dict[str, Any]:
    """Resolve an instrument from the master, importing LAZILY.

    WHY lazy: `reference.py` re-exports this module's facade at its own bottom, so a
    module-level `from ... import get_instrument` here would be a circular import. Deferring
    the import to call time breaks the cycle without a second code path, and the cost is a
    dict lookup in `sys.modules` on a cached facade call.
    """
    from modules.reference.reference import get_instrument

    return get_instrument(instrument_id)


#: Module-level path attribute, so config rotation and tests can redirect it. Mirrors the
#: `_SNAPSHOT_PATH` precedent in reference.py - a seam, not a global singleton.
_PROFILES_PATH = Path(__file__).resolve().parents[2] / "config" / "execution" / "profiles.yaml"

# Sane hard bounds (AGENTS.md 3.13: refuse, never clamp). 10% commission is already absurd;
# anything above it is a typo, and clamping would silently invent a fee.
#
# WHY `Decimal("...")` and not `Decimal(...)`: AGENTS.md 3.1 requires money built from `str`.
# FURB157 wants the numeric form, which is harmless for a whole number but catastrophic for a
# fraction - Decimal(0.1) is 0.1000000000000000055511151231257827, not 0.1. The lint hint is
# refused deliberately.
_COMMISSION_MAX = Decimal("0.10")
_SPREAD_MAX = Decimal("1000000")  # noqa: FURB157
_FINANCING_MIN, _FINANCING_MAX = Decimal("-1"), Decimal("1")  # noqa: FURB157
_SLIPPAGE_BPS_MAX = Decimal("1000")  # noqa: FURB157

# Non-finite literals parse as Decimal but poison every comparison (NaN raises
# InvalidOperation on ordering, so bounds checking would not even be reliable).
_NON_FINITE = frozenset({"nan", "snan", "inf", "infinity", "-inf", "-infinity"})


def _money(raw: Any, field: str) -> Decimal:
    """Parse an AUTHORED money literal. Unquoted numbers are rejected, not coerced.

    WHY reject rather than `Decimal(str(raw))`: that expression only recovers the author's
    intent because of a float-representation accident. See the module docstring.

    WHY every raise below is `ValueError` and not `TypeError` (ruff TRY004 is suppressed):
    these run inside Pydantic field validators, and Pydantic v2 converts `ValueError` into a
    `ValidationError` - which the loader then converts into the typed
    `ReferenceValidationError`. A `TypeError` would PROPAGATE uncaught and break that
    contract, so the "wrong exception type" lint is wrong here.
    """
    if isinstance(raw, (bool, int, float)):
        raise ValueError(  # noqa: TRY004
            f"{field}: must be a QUOTED string, got {type(raw).__name__} {raw!r}. "
            f'Quote it (e.g. "{raw!r}") so it is never parsed as a float - AGENTS.md 3.1.'
        )
    if not isinstance(raw, str):
        raise ValueError(  # noqa: TRY004
            f"{field}: must be a quoted string, got {type(raw).__name__}"
        )
    if raw == "":
        raise ValueError(f"{field}: must not be empty")
    if raw.lower() in _NON_FINITE:
        raise ValueError(f"{field}: {raw!r} is not a finite decimal")
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{field}: {raw!r} is not a valid decimal") from exc
    if not value.is_finite():
        raise ValueError(f"{field}: {raw!r} overflows to a non-finite Decimal")
    return value


def _utc(value: Any, field: str) -> dt.datetime:
    """Require a tz-aware datetime and normalise to UTC (AGENTS.md 3.8).

    `ValueError` not `TypeError` for the same Pydantic reason documented in `_money`.
    """
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{field}: {value!r} is not an ISO datetime") from exc
    else:
        raise ValueError(  # noqa: TRY004
            f"{field}: must be an ISO datetime string, got {type(value).__name__}"
        )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field}: must be tz-aware (UTC); naive datetimes are rejected")
    return parsed.astimezone(dt.UTC)


class SlippageParams(BaseModel):
    """Slippage as a descriptive fact. Frozen, extra forbidden."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: Literal["fixed_bps", "none"]
    value: Decimal

    @field_validator("value", mode="before")
    @classmethod
    def _check_value(cls, raw: Any) -> Decimal:
        return _money(raw, "slippage.value")

    @field_validator("value")
    @classmethod
    def _check_range(cls, v: Decimal) -> Decimal:
        if v < 0 or v > _SLIPPAGE_BPS_MAX:
            raise ValueError(f"slippage.value {v} outside [0, {_SLIPPAGE_BPS_MAX}] bps")
        return v

    def model_post_init(self, _context: Any) -> None:
        # WHY a cross-field rule and not a field validator: the constraint is BETWEEN model and
        # value, so neither field can check it alone. 'none' means literally no modelled
        # slippage, so any non-zero value would be a contradiction in the fact itself.
        if self.model == "none" and self.value != Decimal(0):
            raise ValueError(f"slippage model 'none' requires value 0, got {self.value}")
        if self.model == "fixed_bps" and self.value <= 0:
            raise ValueError(f"slippage model 'fixed_bps' requires value > 0, got {self.value}")


class ExecutionProfile(BaseModel):
    """One instrument+broker's cost facts, in force from `effective_from` onward."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_id: str
    broker: str
    effective_from: dt.datetime
    commission_rate: Decimal
    typical_spread: Decimal
    financing_apy: Decimal
    slippage: SlippageParams

    @field_validator("effective_from", mode="before")
    @classmethod
    def _check_effective_from(cls, raw: Any) -> dt.datetime:
        return _utc(raw, "effective_from")

    @field_validator("commission_rate", "typical_spread", "financing_apy", mode="before")
    @classmethod
    def _check_money(cls, raw: Any, info: Any) -> Decimal:
        return _money(raw, info.field_name)

    @field_validator("commission_rate")
    @classmethod
    def _check_commission(cls, v: Decimal) -> Decimal:
        if v < 0 or v > _COMMISSION_MAX:
            raise ValueError(f"commission_rate {v} outside [0, {_COMMISSION_MAX}]")
        return v

    @field_validator("typical_spread")
    @classmethod
    def _check_spread(cls, v: Decimal) -> Decimal:
        if v < 0 or v > _SPREAD_MAX:
            raise ValueError(f"typical_spread {v} outside [0, {_SPREAD_MAX}]")
        return v

    @field_validator("financing_apy")
    @classmethod
    def _check_financing(cls, v: Decimal) -> Decimal:
        if v < _FINANCING_MIN or v > _FINANCING_MAX:
            raise ValueError(f"financing_apy {v} outside [{_FINANCING_MIN}, {_FINANCING_MAX}]")
        return v


def load_execution_profiles(path: Path) -> dict[tuple[str, str], list[ExecutionProfile]]:
    """Load + validate cost facts. Fail-closed on ANY invalid content.

    WHY fail-closed: a missing or partial costs file must never yield an implicit zero-cost
    profile. Zero commission and zero spread are the most dangerous values in this system -
    they make a losing strategy look profitable, which is exactly how a backtest lies.
    """
    if not path.exists():
        raise ReferenceValidationError(
            f"execution profiles not found: {path}. Costs are mandatory; a backtest with no "
            "cost facts is rejected (AGENTS.md 3.4)."
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ReferenceValidationError(f"{path.name}: malformed YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ReferenceValidationError(
            f"{path.name}: expected a mapping with a 'profiles' key, got {type(raw).__name__}"
        )
    rows = raw.get("profiles")
    if rows is None:
        raise ReferenceValidationError(f"{path.name}: missing required 'profiles' key")
    if not isinstance(rows, list) or not rows:
        raise ReferenceValidationError(
            f"{path.name}: 'profiles' must be a non-empty list, got {type(rows).__name__}"
        )

    out: dict[tuple[str, str], list[ExecutionProfile]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ReferenceValidationError(f"{path.name}: profiles[{index}] must be a mapping")
        try:
            prof = ExecutionProfile(**row)
        except ValidationError as exc:
            raise ReferenceValidationError(
                f"{path.name}: profiles[{index}] invalid: {exc.errors()}"
            ) from exc

        # Config drift vs the identity source of truth: a row naming an instrument the master
        # does not know is a typo or a stale file, and would otherwise serve costs for a
        # tradeable thing that does not exist.
        try:
            instrument = _get_instrument(prof.instrument_id)
        except Exception as exc:  # re-raised as the typed reference error
            raise ReferenceValidationError(
                f"{path.name}: profiles[{index}] instrument_id {prof.instrument_id!r} is not in "
                f"the instrument master: {exc}"
            ) from exc

        brokers = instrument.get("brokers") or []
        if prof.broker not in brokers:
            raise ReferenceValidationError(
                f"{path.name}: profiles[{index}] broker {prof.broker!r} is not listed for "
                f"{prof.instrument_id} (master lists {sorted(brokers)})"
            )

        key = (prof.instrument_id, prof.broker)
        bucket = out.setdefault(key, [])
        # Ambiguous overlap must never resolve last-wins: two rows claiming the same instant
        # mean the config does not know which cost applied.
        if any(b.effective_from == prof.effective_from for b in bucket):
            raise ReferenceValidationError(
                f"{path.name}: duplicate effective_from {prof.effective_from.isoformat()} for "
                f"{key} - ambiguous, never last-wins"
            )
        bucket.append(prof)

    for bucket in out.values():
        bucket.sort(key=lambda p: p.effective_from)
    return out


@lru_cache(maxsize=1)
def _cached_profiles() -> dict[tuple[str, str], list[ExecutionProfile]]:
    return load_execution_profiles(_PROFILES_PATH)


@lru_cache(maxsize=256)
def get_execution_profile(
    instrument_id: str, broker: str, effective_at: dt.datetime | None = None
) -> ExecutionProfile:
    """The cost facts in force for (instrument, broker) at `effective_at`.

    Effective-dated precedence: the latest row whose `effective_from` is <= the instant wins.
    `effective_at=None` means now, in UTC.

    Config rotation: edit the file, then call `get_execution_profile.cache_clear()` (and
    `_cached_profiles.cache_clear()`). Caching is deliberate - this is read on the hot path,
    and re-reading a config file per lookup would be I/O in a lookup. The seam is explicit
    rather than a background refresh, so a reader can never silently observe a half-applied
    edit.
    """
    # Identity is checked BEFORE any lookup, so a caller passing junk gets the typed identity
    # error rather than a confusing "no profile" message.
    instrument = _get_instrument(instrument_id)

    # WHY the broker is validated against the KNOWN set first, before the instrument's own
    # broker list: "this is not a venue we support" and "this venue has no cost facts for
    # this instrument" are different problems with different fixes, and collapsing them would
    # tell an operator to edit a config file when the real answer is a typo in a broker name.
    from modules.reference.reference import _require_broker

    _require_broker(broker)

    if effective_at is None:
        effective_at = dt.datetime.now(dt.UTC)
    elif effective_at.tzinfo is None or effective_at.utcoffset() is None:
        raise ReferenceValidationError(
            f"effective_at must be tz-aware (UTC); got naive {effective_at!r}"
        )
    else:
        effective_at = effective_at.astimezone(dt.UTC)

    if broker not in (instrument.get("brokers") or []):
        # An unlisted broker is not a typo, it is a real venue that simply has no cost facts.
        # Raised as a validation error naming the pair so the operator knows what to add.
        raise ReferenceValidationError(
            f"no execution profile for ({instrument_id}, {broker}): broker is not listed for "
            f"this instrument in the master"
        )

    profiles = _cached_profiles().get((instrument_id, broker))
    if not profiles:
        raise ReferenceValidationError(f"no execution profile for ({instrument_id}, {broker})")

    applicable = [p for p in profiles if p.effective_from <= effective_at]
    if not applicable:
        # Never silently use a profile that was not yet in effect - that would apply today's
        # costs to a historical backtest and quietly rewrite its result.
        raise ReferenceValidationError(
            f"no execution profile for ({instrument_id}, {broker}) effective at "
            f"{effective_at.isoformat()}; earliest is "
            f"{profiles[0].effective_from.isoformat()}"
        )
    return max(applicable, key=lambda p: p.effective_from)


# Capture the real lru_cache clear BEFORE overwriting it - otherwise the replacement calls
# itself and recurses to death. (Found the hard way: RecursionError on the first facade call.)
_inner_pair_cache_clear = get_execution_profile.cache_clear  # type: ignore[attr-defined]


def _clear_all_caches() -> None:
    """Clear BOTH the per-pair cache and the parsed-file cache.

    WHY this exists: config rotation must not be able to leave half the state stale. An
    earlier revision had two independent `lru_cache`s and exposed only the outer one, so
    `get_execution_profile.cache_clear()` appeared to work while the parsed file was still
    served from the inner cache - a rotation that silently did nothing. One clear now clears
    both, because a seam that half-works is worse than no seam.
    """
    _inner_pair_cache_clear()
    _cached_profiles.cache_clear()


# Expose a single, complete clear on the public callable (spec Edge 16 rotation seam).
get_execution_profile.cache_clear = _clear_all_caches  # type: ignore[attr-defined]
