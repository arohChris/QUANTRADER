"""WHY: M4.7 D1 playbook card — fade the FAILED breakout: sweep_reversal event
(+1 bull trap / −1 bear trap) only when the volatility regime is extreme
(ATR percentile rank above the median), so ordinary mid-regime whipsaw sweeps
never get faded.

Pure compute (no I/O, no state): evaluate(features, params) -> signed strength
[-1, +1]; 0.0 = no signal (m4.2 contract — docs/specs/m4.7_playbook_tier1.spec.md).
"""
from __future__ import annotations

import math

NAME = "failed_breakout_sweep_reversal"
FEATURE_SLOTS: dict[str, tuple[str, dict[str, float]]] = {
    "sweep": ("sweep_reversal", {}),
    "atr_rk": ("atr_percentile_rank", {}),
}
PARAM_DEFAULTS: dict[str, float] = {"atr_rk_min": 50.0}


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
    sweep = _slot(features, "sweep")
    atr_rk = _slot(features, "atr_rk")

    # Gate — strict equals FAIL (boundary 50 -> 0.0, spec §D1).
    if atr_rk <= atr_rk_min:
        return 0.0
    # Trigger: only a real sweep event (+1 / −1) fades; 0 (or garbage) -> 0.0
    # fail-closed, never a partial strength from a non-event.
    if sweep not in (1.0, -1.0):
        return 0.0

    progress = (atr_rk - atr_rk_min) / (100.0 - atr_rk_min)
    return sweep * (0.5 + 0.5 * min(1.0, progress))
