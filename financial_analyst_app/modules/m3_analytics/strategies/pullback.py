"""WHY: M4.2 D5 `pullback` family — buy the dip inside a proven ADX trend: strong
trend (adx >= adx_min) plus price pulled back >= pullback sigmas below the mean.
**LONG-only Day-1** (documented gap, m4.2 spec): ADX measures trend STRENGTH not
direction, so a short-side pullback rule would have no direction input — flagged
until a directional feature lands (M4.1+).

Pure compute: evaluate(features, params) -> signed strength [-1, +1]
(contract: docs/specs/m4.2_strategy_files.spec.md).
"""
from __future__ import annotations

import math

NAME = "pullback"
FEATURE_SLOTS: dict[str, tuple[str, dict[str, float]]] = {
    "trend": ("adx_14", {}),
    "entry": ("zscore_20", {}),
}
PARAM_DEFAULTS: dict[str, float] = {"adx_min": 25.0, "pullback": 1.0}


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
    adx_min = params.get("adx_min", PARAM_DEFAULTS["adx_min"])
    if not math.isfinite(adx_min) or not 0.0 <= adx_min <= 100.0:
        raise ValueError(
            f"params.adx_min: must be in [0, 100], got {adx_min!r}"
        )
    pullback = params.get("pullback", PARAM_DEFAULTS["pullback"])
    if not math.isfinite(pullback) or pullback <= 0.0:
        raise ValueError(
            f"params.pullback: must be > 0, got {pullback!r}"
        )
    trend = _slot(features, "trend")
    entry = _slot(features, "entry")

    if trend < adx_min:
        return 0.0  # no trend to pull back within
    if entry >= pullback:
        return 0.0  # day-1 long-only: no short-side rule (see module WHY)
    if entry <= -pullback:
        progress = (abs(entry) - pullback) / pullback
        return 0.5 + 0.5 * min(1.0, progress)  # house ramp from the pullback depth
    return 0.0
