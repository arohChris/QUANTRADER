"""WHY: M4.2 D5 `mean_reversion` family — fade statistically extreme z-scores
(z beyond +entry short / -entry long): price snaps back toward the window mean
when the excursion is over-extended rather than trending.

Pure compute: evaluate(features, params) -> signed strength [-1, +1]
(contract: docs/specs/m4.2_strategy_files.spec.md).
"""
from __future__ import annotations

import math

NAME = "mean_reversion"
FEATURE_SLOTS: dict[str, tuple[str, dict[str, float]]] = {"z": ("zscore_20", {})}
PARAM_DEFAULTS: dict[str, float] = {"entry": 2.0}


def _slot(features: dict[str, float], slot: str) -> float:
    if slot not in features:
        raise ValueError(f"features: missing slot {slot!r}")
    value = features[slot]
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"features.{slot}: must be a finite number, got {value!r}")
    return float(value)


def evaluate(features: dict[str, float], params: dict[str, float]) -> float:
    if features is None:
        raise ValueError("features: must be a mapping, got None")
    if params is None:
        raise ValueError("params: must be a mapping, got None")
    entry = params.get("entry", PARAM_DEFAULTS["entry"])
    if not math.isfinite(entry) or entry <= 0.0:
        raise ValueError(
            f"params.entry: must be > 0, got {entry!r}"
        )
    z = _slot(features, "z")

    if abs(z) < entry:
        return 0.0
    # House ramp from the entry threshold; direction fades the extension (-sign(z)).
    progress = (abs(z) - entry) / entry
    return (-1.0 if z > 0.0 else 1.0) * (0.5 + 0.5 * min(1.0, progress))
