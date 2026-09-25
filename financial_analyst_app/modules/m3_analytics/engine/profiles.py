"""WHY: bible §4 profiles — one `analytics:` param section per asset-class YAML
(`config/assets/{forex,crypto,commodities}.yaml`), loaded ONCE at startup so a typo
fails loud at config load instead of silently using wrong indicator params per request.

Amendment M3.1-c: asset_class vocabulary is `commodity` (M2b.1) but the file is
`commodities.yaml` — mapped here, never by renaming the frozen vocabulary.
Precedence is the engine's job: overrides > profile > REQUIREMENTS.defaults.
"""
from __future__ import annotations

from numbers import Real
from pathlib import Path
from typing import Any

import yaml

# WHY stem→class map: bible writes `load_yaml(f".../{asset_class}.yaml")["analytics"]`
# but commodity≠commodities (M3.1-c) — resolve the filename once, here.
_CLASS_BY_STEM = {"forex": "forex", "crypto": "crypto", "commodities": "commodity"}
# Fail-loud startup (spec §Edges): all Tier-1 asset classes must have a profile file.
_REQUIRED_CLASSES = ("forex", "crypto", "commodity")


def _validate_analytics(raw: Any, where: str) -> dict[str, dict[str, float]]:
    """Shape: {feature: {param: number}} — anything else is a config bug (ValueError)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        # WHY ValueError not TypeError: profile load failures freeze ValueError
        # (spec edge test_profile_missing_at_load_fails_loud — pytest.raises(ValueError)).
        raise ValueError(f"{where}: analytics section must be a mapping")  # noqa: TRY004
    validated: dict[str, dict[str, float]] = {}
    for feature, params in raw.items():
        if not isinstance(feature, str) or not feature:
            raise ValueError(
                f"{where}: analytics feature names must be non-empty strings"
            )
        if not isinstance(params, dict):
            raise ValueError(  # noqa: TRY004 — same ValueError contract
                f"{where}: analytics.{feature} must be a mapping of params"
            )
        for key, value in params.items():
            # bool is an int subclass — a `true` param would coerce to 1.0 silently.
            if not isinstance(key, str) or not isinstance(value, Real) or isinstance(value, bool):
                raise ValueError(  # noqa: TRY004 — same ValueError contract
                    f"{where}: analytics.{feature}.{key} must be a number"
                )
        validated[feature] = {str(k): float(v) for k, v in params.items()}
    return validated


class Profiles:
    """Immutable per-asset-class param lookup — sync reads, no I/O per request."""

    def __init__(self, by_class: dict[str, dict[str, dict[str, float]]]) -> None:
        self._by_class = by_class

    @classmethod
    def from_dir(cls, directory: str | Path) -> Profiles:
        root = Path(directory)
        files = sorted(root.glob("*.yaml"))
        if not files:
            # Spec edge: profile missing → error at config LOAD, not per request.
            raise ValueError(f"no asset profile YAMLs found in {root}")
        by_class: dict[str, dict[str, dict[str, float]]] = {}
        for path in files:
            asset_class = _CLASS_BY_STEM.get(path.stem)
            if asset_class is None:
                raise ValueError(f"unknown asset profile file {path.name!r}")
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ValueError(  # noqa: TRY004 — same ValueError contract
                    f"{path.name}: top-level must be a mapping"
                )
            by_class[asset_class] = _validate_analytics(data.get("analytics"), path.name)
        missing = [cls_name for cls_name in _REQUIRED_CLASSES if cls_name not in by_class]
        if missing:
            raise ValueError(f"profiles missing for asset_class: {missing}")
        return cls(by_class)

    def get(self, asset_class: str) -> dict[str, dict[str, float]]:
        try:
            return self._by_class[asset_class]
        except KeyError:
            raise ValueError(f"no profile for asset_class {asset_class!r}") from None
