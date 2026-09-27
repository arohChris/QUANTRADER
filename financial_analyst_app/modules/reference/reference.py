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
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from modules.reference.standards import AssetStandardsError, class_standard, standards
from shared.contracts.errors import (
    ReferenceValidationError,
    UnknownBrokerError,
    UnknownInstrumentError,
)
from shared.reference.asset_classes import AssetStandardError, money_decimal

logger = logging.getLogger(__name__)

# M2b.3 facade re-export (spec "Contract"): a SINGLE entrypoint, the same callable object -
# not a wrapper - so `reference.get_execution_profile is execution_profiles


class AssetConfigError(ReferenceValidationError):
    """An asset file is structurally invalid (M2b.5).

    WHY a distinct type: "the config is wrong" and "you passed me a bad argument" are
    different failures with different owners. A config error must stop the process at BOOT
    (AGENTS.md 3.13) - it is never a per-tick drop.
    """


class OrphanReferenceError(ReferenceValidationError):
    """A rule references an instrument that is not in the master (M2b.5)."""


# Empty broker must raise BOTH ValidationError-style AND UnknownBrokerError
# (approved tests assert each type on to_canonical(..., broker="")); dual base satisfies both.
class _EmptyBrokerError(UnknownBrokerError, ReferenceValidationError):
    """Empty broker edge: typed as both empty-input validation and unknown-broker."""


_KNOWN_BROKERS = frozenset({"deriv", "mt5", "headway"})
_ASSETS_DIR = Path(__file__).resolve().parents[2] / "config" / "assets"
# Scope 2026-09-22: forex | crypto | commodity only — synthetic.yaml removed
_YAML_FILES = ("forex.yaml", "crypto.yaml", "commodities.yaml")


_KNOWN_ASSET_CLASSES = frozenset(standards())

# Money fields materialised as exact Decimal from an authored, QUOTED string (M2b.5).
# `contract_size` is per-instrument by nature - Gold 100, Silver 5000 - so it is required on
# every instrument and is NOT a class default. The rest inherit from the class standard.
_MONEY_FIELDS = ("tick_size", "contract_size", "min_qty", "qty_step")
_REQUIRED_PER_INSTRUMENT = ("contract_size",)

# Sane bounds for a price tick. Below 1e-12 no venue quotes usefully; at or above 1 every
# price would round to a multiple of the tick, which destroys the data instead of guarding it.
# WHY Decimal("...") and not Decimal(1): AGENTS.md 3.1 requires money to be built from `str`.
# FURB157 wants the int form, which is harmless for a whole number but catastrophic for a
# fraction - Decimal(0.1) is 0.1000000000000000055511151231257827, not 0.1. The lint hint is
# refused deliberately, and the same convention is asserted in the money tests.
_MIN_TICK_SIZE = Decimal("0.000000000001")
_MAX_TICK_SIZE = Decimal("1")  # noqa: FURB157

# How a top-level key is classified as an INSTRUMENT versus a sibling config section.
# WHY a pattern and not "has an asset_class": `analytics:` and friends legitimately have no
# asset_class, but so does a MALFORMED instrument. Guessing on the missing field would let a
# typo'd asset_class silently become config and the instrument would simply vanish. The id
# shape is unambiguous - every canonical id is CLASS.SYMBOL - so a key that LOOKS like an
# instrument must validate as one.
_INSTRUMENT_ID = re.compile(r"^[A-Z][A-Z0-9_]*\.[A-Z0-9_]+$")


def _is_instrument_key(key: Any) -> bool:
    return isinstance(key, str) and bool(_INSTRUMENT_ID.match(key))


def _materialise(inst_id: str, entry: dict[str, Any], origin: str) -> dict[str, Any]:
    """Apply the class standard to one instrument entry, validating as we go.

    Order matters: asset_class first, because it decides the defaults for everything else.
    """
    asset_class = entry.get("asset_class")
    try:
        std = class_standard(asset_class)
    except (AssetStandardError, AssetStandardsError) as exc:
        raise AssetConfigError(f"{origin}: {inst_id}: {exc}") from exc

    # WHY reject a per-asset session block outright: session hours are a property of the
    # CLASS. A per-instrument exception is exactly how a second table grows beside the first
    # and drifts. The class standard is the only place session hours may be written.
    if "sessions" in entry:
        raise AssetConfigError(
            f"{origin}: {inst_id}: per-asset 'sessions' is not allowed - session hours are "
            f"inherited from asset_class {asset_class!r}. Remove it, or change the class."
        )

    out = dict(entry)
    out["tick_size"] = std.tick_size
    out["min_qty"] = std.min_qty
    out["qty_step"] = std.qty_step

    for fld in _MONEY_FIELDS:
        if fld not in entry:
            if fld in _REQUIRED_PER_INSTRUMENT:
                raise AssetConfigError(
                    f"{origin}: {inst_id}: {fld} is required and has no safe default - a "
                    "missing contract size is silently wrong notional (AGENTS.md 3.1)"
                )
            continue  # inherited from the class standard above
        if fld != "contract_size" and fld not in std.overridable:
            raise AssetConfigError(
                f"{origin}: {inst_id}: {fld} is not overridable for class {asset_class!r}"
            )
        try:
            out[fld] = money_decimal(entry[fld], field=f"{origin}:{inst_id}:{fld}")
        except AssetStandardError as exc:
            raise AssetConfigError(str(exc)) from exc

    if out["min_qty"] > out["qty_step"]:
        raise AssetConfigError(
            f"{origin}: {inst_id}: min_qty {out['min_qty']} exceeds qty_step "
            f"{out['qty_step']} - no order could ever be placed"
        )

    # WHY bound tick_size (AGENTS.md 3.13: refuse to start on an absurd value). `Decimal` is
    # exact at any exponent, so "1e400" parses fine and would round EVERY price to a multiple
    # of 1e400 - destroying all data while looking valid. Exactness is not the same as sanity.
    if not (_MIN_TICK_SIZE <= out["tick_size"] < _MAX_TICK_SIZE):
        raise AssetConfigError(
            f"{origin}: {inst_id}: tick_size {out['tick_size']} is outside the sane range "
            f"[{_MIN_TICK_SIZE}, {_MAX_TICK_SIZE}) - prices would be rounded to nonsense"
        )
    return out


def load_assets(paths: list[Path] | None = None) -> dict[str, dict[str, Any]]:
    """Load + validate asset files into a materialised master.

    Used by the shipped loader and by tests. Duplicate instruments across files are an
    error naming BOTH files - last-writer-wins would make the effective config depend on
    directory iteration order.
    """
    files = list(paths) if paths is not None else [_ASSETS_DIR / f for f in _YAML_FILES]
    master: dict[str, dict[str, Any]] = {}
    origin_of: dict[str, str] = {}
    for path in files:
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for inst_id, entry in data.items():
            if not isinstance(entry, dict):
                continue
            # Sibling top-level sections (`analytics:`) are config, not instruments, and are
            # recognised by NOT having an instrument-shaped id. A key that DOES look like an
            # instrument must validate as one - otherwise a typo'd asset_class would make the
            # instrument vanish silently instead of failing the boot.
            if not _is_instrument_key(inst_id):
                continue
            if entry.get("asset_class") not in _KNOWN_ASSET_CLASSES:
                raise AssetConfigError(
                    f"{path.name}: {inst_id}: asset_class "
                    f"{entry.get('asset_class')!r} is missing or unknown; "
                    f"known: {sorted(_KNOWN_ASSET_CLASSES)}"
                )
            if inst_id in master:
                raise AssetConfigError(
                    f"duplicate instrument {inst_id} in {origin_of[inst_id]} and {path.name}"
                )
            master[inst_id] = _materialise(inst_id, entry, path.name)
            origin_of[inst_id] = path.name
    return master


def validate_asset_file(path: Path) -> dict[str, dict[str, Any]]:
    """Validate one file and return its materialised instruments. Raises AssetConfigError."""
    return load_assets([path])


def list_orphans(
    *,
    master_files: list[Path],
    calendar_files: list[Path] | None = None,
    mapping_files: list[Path] | None = None,
) -> list[str]:
    """Report rules that point at an instrument the master does not contain.

    WHY this exists: REMOVAL is the dangerous direction. Deleting an instrument from the
    master while a session override or a broker-native mapping still names it leaves a live
    rule for something that no longer exists - and it fails silently, because nothing looks
    the instrument up any more.
    """
    known = set(load_assets(master_files))
    orphans: list[str] = []
    for path in [*(calendar_files or []), *(mapping_files or [])]:
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue
        for inst_id in data:
            # Only instrument-shaped keys are rules about instruments; sibling config
            # sections (analytics:, calendars:) are not and must not be reported.
            if not _is_instrument_key(inst_id) or inst_id in known:
                continue
            orphans.append(f"{path.name}: {inst_id} is not in the instrument master")
    return orphans


def _load_master() -> dict[str, dict[str, Any]]:
    master: dict[str, dict[str, Any]] = {}
    for fname in _YAML_FILES:
        path = _ASSETS_DIR / fname
        if not path.exists():
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for inst_id, entry in data.items():
            # WHY require a recognised asset_class: each assets file also carries SIBLING
            # top-level config sections (today `analytics:`). The loader used to take any
            # dict-valued key, so a profile block became a phantom instrument and
            # exists("analytics") returned True. Fail closed - a real instrument missing its
            # asset_class is a config bug, and silently accepting it would let the calendar
            # resolve a rule for something that is not a tradeable asset.
            if not isinstance(entry, dict):
                continue
            if entry.get("asset_class") not in _KNOWN_ASSET_CLASSES:
                continue
            # M2b.5: the class standard is applied here, so a newly added instrument inherits
            # its session/tick/qty automatically and money fields are exact Decimals.
            master[inst_id] = _materialise(inst_id, entry, fname)
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
    # WHY the explicit fallback rather than `dict.get(native, native)`: the map is typed
    # `dict[str, str]`, so `.get(k, k)` infers `str | None` even though the default is
    # always a `str`. Spelling it out keeps the return type honest - a caller must never have
    # to defend against `None` from an identity lookup that cannot return it.
    resolved = _TO_CANON[broker].get(native)
    return native if resolved is None else resolved


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

# M2b.3 facade re-export (spec "Contract"): a SINGLE entrypoint, the SAME callable object -
# not a wrapper - so `reference.get_execution_profile is`
# execution_profiles.get_execution_profile holds, and there is exactly ONE LRU cache to
# clear on config rotation. Imported LAST because execution_profiles resolves get_instrument
# from this module (lazily, at call time), so importing any earlier would be circular.
from modules.reference.execution_profiles import (  # noqa: F401 - intentional facade re-export
    ExecutionProfile,
    SlippageParams,
    get_execution_profile,
    load_execution_profiles,
)