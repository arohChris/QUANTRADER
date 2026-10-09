"""WHY: M4.7 S5 playbook card — act ONLY on a fresh Supertrend flip confirmed by
ADX trend-strength (not chop), ramping signal strength from the ADX gate up to
the full-ADX mark via the house ramp.

Pure compute (no I/O, no state): evaluate(features, params) -> signed strength
[-1, +1]; 0.0 = no signal (m4.2 contract — docs/specs/m4.7_playbook_tier1.spec.md).
"""
from __future__ import annotations

import math

NAME = "supertrend_adx_filter"
FEATURE_SLOTS: dict[str, tuple[str, dict[str, float]]] = {
    "flip": ("supertrend_flip", {}),
    "adx": ("adx_14", {}),
}
PARAM_DEFAULTS: dict[str, float] = {"adx_min": 25.0, "adx_full": 50.0}


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
        raise ValueError(f"params.adx_min: must be in [0, 100], got {adx_min!r}")
    adx_full = params.get("adx_full", PARAM_DEFAULTS["adx_full"])
    if not math.isfinite(adx_full) or not 0.0 <= adx_full <= 100.0:
        raise ValueError(f"params.adx_full: must be in [0, 100], got {adx_full!r}")
    if adx_full <= adx_min:
        raise ValueError(
            f"params.adx_full: must be > adx_min ({adx_min!r}), got {adx_full!r}"
        )
    flip = _slot(features, "flip")
    adx = _slot(features, "adx")

    # Gate — strict equals FAIL (boundary adx == adx_min -> 0.0, spec §S5).
    if adx <= adx_min:
        return 0.0
    # Trigger: only a fresh flip (+1 / −1) trades; no-flip (or garbage) -> 0.0.
    if flip not in (1.0, -1.0):
        return 0.0

    progress = (adx - adx_min) / (adx_full - adx_min)
    return flip * (0.5 + 0.5 * min(1.0, progress))
