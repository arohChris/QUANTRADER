"""WHY: M4.7 A1 playbook card — join pullbacks INTO the trend's dynamic support
(EMA20) only when trend context is real (state/ADX/ER/R-squared), so
counter-trend noise never fills at the moving average. The reversal legs
(cpos >= cpos_min AND low > swing-low) apply to BOTH directions literally from
the card pseudo — mirrored short shape is the spec's recorded
TODO(correctness) question, not a silent change (spec §A1).

Pure compute (no I/O, no state): evaluate(features, params) -> signed strength
[-1, +1]; 0.0 = no signal (m4.2 contract — docs/specs/m4.7_playbook_tier1.spec.md).
"""
from __future__ import annotations

import math

NAME = "trend_pullback_to_dynamic_support"
FEATURE_SLOTS: dict[str, tuple[str, dict[str, float]]] = {
    "state": ("market_state", {}),
    "adx": ("adx_14", {}),
    "er": ("efficiency_ratio", {}),
    "r2": ("ols_r_squared", {}),
    "atr": ("atr_14", {}),
    "ema": ("ema_20", {}),
    "rsi": ("rsi_14", {}),
    "swlow": ("swing_low", {}),
    "close": ("last_close", {}),
    "low": ("bar_low", {}),
    "cpos": ("close_position", {}),
}
PARAM_DEFAULTS: dict[str, float] = {
    "adx_min": 25.0,
    "er_min": 0.3,
    "r2_min": 0.6,
    "touch_atr": 0.25,
    "cpos_min": 0.4,
    "rsi_min": 40.0,
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
    adx_min = params.get("adx_min", PARAM_DEFAULTS["adx_min"])
    if not math.isfinite(adx_min) or not 0.0 <= adx_min <= 100.0:
        raise ValueError(f"params.adx_min: must be in [0, 100], got {adx_min!r}")
    er_min = params.get("er_min", PARAM_DEFAULTS["er_min"])
    if not math.isfinite(er_min) or not 0.0 <= er_min < 1.0:
        raise ValueError(f"params.er_min: must be in [0, 1), got {er_min!r}")
    r2_min = params.get("r2_min", PARAM_DEFAULTS["r2_min"])
    if not math.isfinite(r2_min) or not 0.0 <= r2_min < 1.0:
        raise ValueError(f"params.r2_min: must be in [0, 1), got {r2_min!r}")
    touch_atr = params.get("touch_atr", PARAM_DEFAULTS["touch_atr"])
    if not math.isfinite(touch_atr) or touch_atr <= 0.0:
        raise ValueError(f"params.touch_atr: must be > 0, got {touch_atr!r}")
    cpos_min = params.get("cpos_min", PARAM_DEFAULTS["cpos_min"])
    if not math.isfinite(cpos_min) or not 0.0 <= cpos_min <= 1.0:
        raise ValueError(f"params.cpos_min: must be in [0, 1], got {cpos_min!r}")
    rsi_min = params.get("rsi_min", PARAM_DEFAULTS["rsi_min"])
    if not math.isfinite(rsi_min) or not 0.0 <= rsi_min <= 100.0:
        raise ValueError(f"params.rsi_min: must be in [0, 100], got {rsi_min!r}")
    state = _slot(features, "state")
    adx = _slot(features, "adx")
    er = _slot(features, "er")
    r2 = _slot(features, "r2")
    atr = _slot(features, "atr")
    ema = _slot(features, "ema")
    rsi = _slot(features, "rsi")
    swlow = _slot(features, "swlow")
    close = _slot(features, "close")
    low = _slot(features, "low")
    cpos = _slot(features, "cpos")

    # Gate — strict equals FAIL everywhere (spec §A1); flat state 0 -> 0.0.
    if state not in (1.0, -1.0):
        return 0.0
    if adx <= adx_min or er <= er_min or r2 <= r2_min:
        return 0.0

    # Trigger — ALL-AND from the card pseudo
    # `if not (touched and reversal and rsi(...)>40): return None`.
    dist = abs(close - ema)
    touched = dist <= touch_atr * atr
    reversal = cpos >= cpos_min and low > swlow
    if not (touched and reversal and rsi > rsi_min):
        return 0.0

    # Pullback quality: exact touch -> full progress 1.0 (explicit so atr == 0
    # with close == ema never hits 0/0); touched + dist>0 implies denom > 0.
    progress = 1.0 if dist == 0.0 else 1.0 - min(1.0, dist / (touch_atr * atr))
    return state * (0.5 + 0.5 * min(1.0, progress))
