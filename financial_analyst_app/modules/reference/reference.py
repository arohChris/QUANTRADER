"""
M2b.1 — ReferenceService facade — broker-native ↔ canonical instrument identity.
WHY: sync pure dict/cache lookups (spec: no await, no I/O in hot path); YAML is the
single source of truth (no hard-coded symbol ifs); typed errors from shared.contracts.errors.

Gate: to_canonical("frxEURUSD", "deriv") == "FX.EURUSD"

M2b.2 (additive, 2026-09-25) — snapshot lineage: current_snapshot_id() feeds
M2.2 normalize.py:96 (MarketData.reference_snapshot_id, bible 02b §6).
Never raises: no try/except at the call site → None = Drop(missing_field) fail-safe.
"""
from __future__ import annotations

import functools
import logging
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from shared.contracts.errors import (
    ReferenceValidationError,
    UnknownBrokerError,
    UnknownInstrumentError,
)

logger = logging.getLogger(__name__)


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


# --- M2b.2 — snapshot lineage (spec: docs/specs/m2b.2_reference_snapshot.spec.md) ---

_SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "config" / "reference" / "snapshot.yaml"
_SNAPSHOT_ID_RE = re.compile(r"REF-\d{4}-\d{2}-\d{2}-\d{3}")


def load_snapshot_id(path: Any) -> str | None:
    """Read + validate one snapshot file → REF-YYYY-MM-DD-NNN, else None. NEVER raises.

    WHY broad except (spec Correct): M2.2 normalize.py:96 calls the facade with no
    try/except — an escaped exception would crash ingestion; None → Drop(missing_field)
    keeps lineage mandatory (no MarketData without a valid snapshot), and the WARNING
    keeps the failure visible (AGENTS §3.9: never silent).
    """
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            logger.warning("M2b.2 snapshot: top-level not a mapping in %s", path)
            return None
        snapshot_id = data.get("snapshot_id")
        if not isinstance(snapshot_id, str):
            logger.warning("M2b.2 snapshot: snapshot_id missing/not a str in %s", path)
            return None
        snapshot_id = snapshot_id.strip()
        if not _SNAPSHOT_ID_RE.fullmatch(snapshot_id):
            logger.warning("M2b.2 snapshot: snapshot_id format invalid in %s", path)
            return None
        effective_from = data.get("effective_from")
        if not isinstance(effective_from, str):
            logger.warning("M2b.2 snapshot: effective_from missing/not a str in %s", path)
            return None
        parsed = datetime.fromisoformat(effective_from)  # raises ValueError → below
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            logger.warning("M2b.2 snapshot: effective_from must be tz-aware in %s", path)
            return None
        return snapshot_id
    except Exception as exc:  # noqa: BLE001 — spec: OSError/Unicode/YAML/ValueError/unexpected → None
        logger.warning("M2b.2 snapshot: cannot load %s: %s", path, exc)
        return None


@functools.lru_cache(maxsize=1)
def current_snapshot_id() -> str | None:
    """Effective snapshot id for lineage stamping — cached per process (I08 determinism).

    Rotation (M2b-I03 immutability): code never writes snapshot.yaml; a new snapshot =
    human edits the file + restart (or current_snapshot_id.cache_clear()).
    """
    return load_snapshot_id(_SNAPSHOT_PATH)
