"""Owns the asset-class standard CONFIG: the path, the single process-wide load, accessors.

WHY this layer exists separately from `shared/reference/asset_classes.py`: `shared` owns the
schema and must not know where the application's config lives, and must not do file I/O at
import time (a module that reads a file on import behaves differently depending on the
working directory). So the values live in `config/reference/asset_classes.yaml`, the shape
lives in `shared`, and this module is the only thing that knows both.

This is the ONE place the standard is loaded. The calendar and the asset master both import
it from here, so there is a single answer to "what are this class's hours" and no second copy
that can drift.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import yaml

from shared.reference.asset_classes import (
    AssetClassStandard,
    AssetStandardError,
    parse_standard,
)

__all__ = [
    "AssetStandardsError",
    "class_standard",
    "load_standards",
    "reload_standards",
    "session_defaults",
    "standard_path",
    "standards",
]


class AssetStandardsError(AssetStandardError):
    """The standard config is missing or invalid. Raised at BOOT, never per tick."""


_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "reference"
_STANDARD_FILE = "asset_classes.yaml"

# WHY an override hook: tests need to point at a fixture config without mutating a module
# global, and SIGHUP hot-reload (M1) needs to re-read on demand. Both are legitimate; neither
# is "reach in and patch the dict".
_ENV_OVERRIDE = "QT_TEST_STANDARDS"


def standard_path() -> Path:
    override = os.environ.get(_ENV_OVERRIDE)
    return Path(override) if override else _CONFIG_DIR / _STANDARD_FILE


class _NoDuplicateLoader(yaml.SafeLoader):
    """SafeLoader that REJECTS duplicate keys.

    WHY: PyYAML silently keeps the LAST value for a duplicated key. A class defined twice
    would therefore appear to work while half the file was ignored - the exact "works, and is
    quietly wrong" failure this module exists to prevent.
    """


def _no_duplicates(loader: yaml.Loader, node: yaml.Node) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in mapping:
            raise AssetStandardsError(
                f"line {key_node.start_mark.line + 1}: duplicate key {key!r} - PyYAML would "
                "silently keep only the last one"
            )
        mapping[key] = loader.construct_object(value_node, deep=True)
    return mapping


_NoDuplicateLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates
)


def load_standards(path: Path | None = None) -> dict[str, AssetClassStandard]:
    """Read + validate the standard config. Raises AssetStandardsError on any problem.

    WHY fail-closed on a MISSING file: there is no safe default. Assuming forex hours for an
    unrecognised class would trade an asset at the weekend, or refuse to trade it while it is
    open. A wrong trading week is worse than a stopped process.
    """
    target = Path(path) if path is not None else standard_path()
    if not target.exists():
        raise AssetStandardsError(
            f"asset-class standard config not found: {target}. There is no built-in default "
            "on purpose - a wrong session is worse than a failed boot."
        )
    try:
        raw = yaml.load(target.read_text(encoding="utf-8"), Loader=_NoDuplicateLoader)
    except AssetStandardsError:
        raise
    except yaml.YAMLError as exc:
        raise AssetStandardsError(f"{target.name}: malformed YAML: {exc}") from exc

    if not isinstance(raw, dict) or not raw:
        raise AssetStandardsError(
            f"{target.name}: expected a non-empty mapping of class name -> settings, "
            f"got {type(raw).__name__}"
        )

    out: dict[str, AssetClassStandard] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not name:
            raise AssetStandardsError(f"{target.name}: class name must be a non-empty string, got {name!r}")
        try:
            out[name] = parse_standard(name, entry)
        except AssetStandardError as exc:
            raise AssetStandardsError(f"{target.name}: {exc}") from exc
    return out


_lock = threading.Lock()
_cached: dict[str, AssetClassStandard] | None = None


def standards() -> dict[str, AssetClassStandard]:
    """The process-wide standard, loaded once.

    Thread-safe because reference lookups happen from the ingest path as well as boot, and a
    half-built dict read from two threads would hand one of them a missing class.
    """
    global _cached
    if _cached is None:
        with _lock:
            if _cached is None:
                _cached = load_standards()
    return _cached


def reload_standards(path: Path | None = None) -> dict[str, AssetClassStandard]:
    """Re-read the standard, replacing the cache. Used by tests and by config hot-reload."""
    global _cached
    with _lock:
        _cached = load_standards(path)
    return _cached


def class_standard(asset_class: Any) -> AssetClassStandard:
    """The standard for a class name. Unknown class is a typed error, NEVER a default."""
    table = standards()
    if not isinstance(asset_class, str) or asset_class not in table:
        raise AssetStandardsError(
            f"unknown asset_class {asset_class!r}; known: {sorted(table)}"
        )
    return table[asset_class]


def session_defaults() -> dict[str, dict[str, Any]]:
    """Class -> session shape, for the calendar. One entry per class, copied defensively."""
    return {name: dict(std.session) for name, std in standards().items()}

