"""WHY: M4.7 B1 playbook card — trade the N-bar Donchian breakout ONLY when the
bar is statistically stretched (ATR percentile rank) and directional
(efficiency ratio), so drifting low-vol ranges never fire. The card's calendar
filter (tagged_twice_this_week) and HTF-structure filter are declared omissions
in spec §B1 (no calendar/HTF data yet), not silent drops.

Pure compute (no I/O, no state): evaluate(features, params) -> signed strength
[-1, +1]; 0.0 = no signal (m4.2 contract — docs/specs/m4.7_playbook_tier1.spec.md).
"""
from __future__ import annotations

import math

NAME = "n_bar_donchian_breakout"
FEATURE_SLOTS: dict[str, tuple[str, dict[str, float]]] = {
    "atr_rk": ("atr_percentile_rank", {}),
    "chan": ("donchian_20", {}),
    "er": ("efficiency_ratio", {}),
}
PARAM_DEFAULTS: dict[str, float] = {"atr_rk_min": 30.0, "er_min": 0.25}


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
    atr_rk_min = params.get("atr_rk_min", PARAM_DEFAULTS["atr_rk_min"])
    if not math.isfinite(atr_rk_min) or not 0.0 <= atr_rk_min <= 100.0:
        raise ValueError(f"params.atr_rk_min: must be in [0, 100], got {atr_rk_min!r}")
    er_min = params.get("er_min", PARAM_DEFAULTS["er_min"])
    if not math.isfinite(er_min) or not 0.0 <= er_min < 1.0:
        raise ValueError(f"params.er_min: must be in [0, 1), got {er_min!r}")
    atr_rk = _slot(features, "atr_rk")
    chan = _slot(features, "chan")
    er = _slot(features, "er")

    # Gate — strict equals FAIL (spec §B1): range must be stretched AND trending.
    if atr_rk <= atr_rk_min or er <= er_min:
        return 0.0

    # Trigger: donchian_20 >100 = close above the prior 20-bar high (the card's
    # `C > max(H,20)`); <0 = below the prior low. Inside the channel -> 0.0.
    if chan > 100.0:
        sign, progress = 1.0, (chan - 100.0) / 100.0
    elif chan < 0.0:
        sign, progress = -1.0, (0.0 - chan) / 100.0
    else:
        return 0.0
    return sign * (0.5 + 0.5 * min(1.0, progress))
