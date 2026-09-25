"""WHY: M4.2 D5 `regime_based` family — regime classifier expressed as a signed
signal for family selection: +1.0 = trend regime (deploy trend families),
-1.0 = chop regime (deploy mean-reversion families), 0.0 = mixed/neutral.
Choppiness + Hurst must AGREE — one alone never flips the regime (categorical,
so this file deliberately returns exact 1.0/-1.0, no house ramp — m4.2 spec).

Pure compute: evaluate(features, params) -> signed strength [-1, +1]
(contract: docs/specs/m4.2_strategy_files.spec.md).
"""
from __future__ import annotations

import math

NAME = "regime_based"
FEATURE_SLOTS: dict[str, tuple[str, dict[str, float]]] = {
    "chop": ("choppiness", {}),
    "hurst": ("hurst", {}),
}
PARAM_DEFAULTS: dict[str, float] = {
    "chop_trend_max": 38.0,
    "chop_chop_min": 61.0,
    "hurst_trend_min": 0.5,
    "hurst_chop_max": 0.5,
}


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
    chop_trend_max = params.get("chop_trend_max", PARAM_DEFAULTS["chop_trend_max"])
    chop_chop_min = params.get("chop_chop_min", PARAM_DEFAULTS["chop_chop_min"])
    for key, value in (("chop_trend_max", chop_trend_max), ("chop_chop_min", chop_chop_min)):
        if not math.isfinite(value) or not 0.0 <= value <= 100.0:
            raise ValueError(
                f"params.{key}: must be in [0, 100], got {value!r}"
            )
    if chop_trend_max >= chop_chop_min:
        raise ValueError(
            f"params.chop_trend_max: must be < chop_chop_min, "
            f"got {chop_trend_max!r} >= {chop_chop_min!r}"
        )
    hurst_trend_min = params.get("hurst_trend_min", PARAM_DEFAULTS["hurst_trend_min"])
    hurst_chop_max = params.get("hurst_chop_max", PARAM_DEFAULTS["hurst_chop_max"])
    for key, value in (("hurst_trend_min", hurst_trend_min), ("hurst_chop_max", hurst_chop_max)):
        if not math.isfinite(value):
            raise ValueError(
                f"params.{key}: must be finite, got {value!r}"
            )
    if hurst_trend_min < hurst_chop_max:
        raise ValueError(
            f"params.hurst_trend_min: must be >= hurst_chop_max (zones must not "
            f"overlap), got {hurst_trend_min!r} < {hurst_chop_max!r}"
        )
    chop = _slot(features, "chop")
    hurst = _slot(features, "hurst")

    if chop < chop_trend_max and hurst > hurst_trend_min:
        return 1.0  # trend regime: low chop + persistent (H > 0.5)
    if chop > chop_chop_min and hurst < hurst_chop_max:
        return -1.0  # chop regime: high chop + anti-persistent (H < 0.5)
    return 0.0
