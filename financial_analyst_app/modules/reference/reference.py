"""
M2b.1 — ReferenceService facade — broker-native ↔ canonical instrument identity.
WHY: sync pure dict/cache lookups (spec: no await, no I/O in hot path); YAML is the
single source of truth (no hard-coded symbol ifs); typed errors from shared.contracts.errors.

Gate: to_canonical("frxEURUSD", "deriv") == "FX.EURUSD"
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from shared.contracts.errors import (
    ReferenceValidationError,
    UnknownBrokerError,
    UnknownInstrumentError,
)


# Empty broker must raise BOTH ValidationError-style AND UnknownBrokerError
# (approved tests assert each type on to_canonical(..., broker="")); dual base satisfies both.
class _EmptyBrokerError(UnknownBrokerError, ReferenceValidationError):
    """Empty broker edge: typed as both empty-input validation and unknown-broker."""


_KNOWN_BROKERS = frozenset({"deriv", "mt5", "headway"})
_ASSETS_DIR = Path(__file__).resolve().parents[2] / "config" / "assets"
# Scope 2026-09-22: forex | crypto | commodity only — synthetic.yaml removed
_YAML_FILES = ("forex.yaml", "crypto.yaml", "commodities.yaml")


def _load_master() -> dict[str, dict[str, Any]]:
    master: dict[str, dict[str, Any]] = {}
    for fname in _YAML_FILES:
        path = _ASSETS_DIR / fname
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for inst_id, entry in data.items():
            if isinstance(entry, dict):
                master[inst_id] = entry
    return master


def _build_maps(
    master: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, str]], dict[tuple[str, str], str]]:
    # to_canonical: {broker: {native: instrument_id}}; to_native: {(instrument_id, broker): native}
    to_canon: dict[str, dict[str, str]] = {b: {} for b in _KNOWN_BROKERS}
    to_nat: dict[tuple[str, str], str] = {}
    for inst_id, entry in master.items():
        bn = entry.get("broker_native")
        if bn is None:
            continue
        if isinstance(bn, str):
            pairs = ((broker, bn) for broker in entry.get("brokers", []))
        elif isinstance(bn, dict):
            pairs = bn.items()
        else:
            continue
        for broker, native in pairs:
            if broker in to_canon:
                to_canon[broker][native] = inst_id
                to_nat[(inst_id, broker)] = native
    return to_canon, to_nat


_MASTER = _load_master()
_TO_CANON, _TO_NATIVE = _build_maps(_MASTER)


def _require_non_empty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or value == "":
        raise ReferenceValidationError(f"{label} must be a non-empty str, got {value!r}")
    return value


def _require_broker(broker: Any) -> str:
    if not isinstance(broker, str):
        raise ReferenceValidationError(f"broker must be a str, got {broker!r}")
    if broker == "":
        raise _EmptyBrokerError(broker)
    if broker not in _KNOWN_BROKERS:
        raise UnknownBrokerError(broker)
    return broker


def to_canonical(native: Any, broker: Any) -> str:
    """Broker-native symbol → canonical instrument_id; unknown native returns unchanged
    (unresolved native — M2.2 detects/quarantines; not a resolved canonical ID)."""
    _require_non_empty_str(native, "native")
    _require_broker(broker)
    return _TO_CANON[broker].get(native, native)


def to_native(instrument_id: Any, broker: Any) -> str:
    """Canonical instrument_id → broker-native symbol for an explicitly mapped pair."""
    _require_non_empty_str(instrument_id, "instrument_id")
    _require_broker(broker)
    if instrument_id not in _MASTER:
        raise UnknownInstrumentError(instrument_id)
    try:
        return _TO_NATIVE[(instrument_id, broker)]
    except KeyError:
        raise ReferenceValidationError(
            f"no broker_native mapping for ({instrument_id!r}, {broker!r})"
        ) from None


def exists(instrument_id: Any, broker: str | None = None) -> bool:
    """True if instrument is in master; with broker, only if listed for that broker."""
    if not isinstance(instrument_id, str) or instrument_id == "":
        return False
    entry = _MASTER.get(instrument_id)
    if entry is None:
        return False
    if broker is None:
        return True
    if not isinstance(broker, str) or broker == "" or broker not in _KNOWN_BROKERS:
        return False
    return broker in entry.get("brokers", [])


def get_instrument(instrument_id: Any) -> dict[str, Any]:
    """Instrument master facts (deep copy — callers cannot corrupt shared state)."""
    _require_non_empty_str(instrument_id, "instrument_id")
    entry = _MASTER.get(instrument_id)
    if entry is None:
        raise UnknownInstrumentError(instrument_id)
    return deepcopy(entry)
