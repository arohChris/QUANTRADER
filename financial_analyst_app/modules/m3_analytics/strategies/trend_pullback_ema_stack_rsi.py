"""WHY: M4.7 S1 playbook card — join pullbacks INSIDE a confirmed EMA stack
(8/21/55) timed by the RSI pullback band (or near-EMA proximity) plus a fresh
tick direction or rejection bar, so entries ride the stack instead of fighting
it. Progress = stack stretch toward the full-stack mark (house ramp).

Pure compute (no I/O, no state): evaluate(features, params) -> signed strength
[-1, +1]; 0.0 = no signal (m4.2 contract — docs/specs/m4.7_playbook_tier1.spec.md).
"""
from __future__ import annotations

import math

NAME = "trend_pullback_ema_stack_rsi"
FEATURE_SLOTS: dict[str, tuple[str, dict[str, float]]] = {
    "adx": ("adx_14", {}),
    "e8": ("ema_8", {}),
    "e21": ("ema_21", {}),
    "e55": ("ema_55", {}),
    "rsi": ("rsi_14", {}),
    "atr": ("atr_14", {}),
    "close": ("last_close", {}),
    "chg": ("close_change", {}),
    "rej": ("rejection_bar", {}),
}
PARAM_DEFAULTS: dict[str, float] = {
    "adx_min": 25.0,
    "rsi_lo": 40.0,
    "rsi_hi": 55.0,
    "short_offset": 5.0,
    "near_atr": 0.3,
    "stack_full_pct": 0.5,
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
    rsi_lo = params.get("rsi_lo", PARAM_DEFAULTS["rsi_lo"])
    if not math.isfinite(rsi_lo) or not 0.0 <= rsi_lo <= 100.0:
        raise ValueError(f"params.rsi_lo: must be in [0, 100], got {rsi_lo!r}")
    rsi_hi = params.get("rsi_hi", PARAM_DEFAULTS["rsi_hi"])
    if not math.isfinite(rsi_hi) or not 0.0 <= rsi_hi <= 100.0:
        raise ValueError(f"params.rsi_hi: must be in [0, 100], got {rsi_hi!r}")
    if rsi_hi <= rsi_lo:
        raise ValueError(f"params.rsi_hi: must be > rsi_lo ({rsi_lo!r}), got {rsi_hi!r}")
    short_offset = params.get("short_offset", PARAM_DEFAULTS["short_offset"])
    if not math.isfinite(short_offset) or short_offset < 0.0:
        raise ValueError(f"params.short_offset: must be >= 0, got {short_offset!r}")
    if rsi_hi + short_offset > 100.0:
        raise ValueError(
            f"params.short_offset: rsi_hi + short_offset must be <= 100, "
            f"got {rsi_hi!r} + {short_offset!r}"
        )
    near_atr = params.get("near_atr", PARAM_DEFAULTS["near_atr"])
    if not math.isfinite(near_atr) or near_atr <= 0.0:
        raise ValueError(f"params.near_atr: must be > 0, got {near_atr!r}")
    stack_full_pct = params.get("stack_full_pct", PARAM_DEFAULTS["stack_full_pct"])
    if not math.isfinite(stack_full_pct) or stack_full_pct <= 0.0:
        raise ValueError(f"params.stack_full_pct: must be > 0, got {stack_full_pct!r}")
    adx = _slot(features, "adx")
    e8 = _slot(features, "e8")
    e21 = _slot(features, "e21")
    e55 = _slot(features, "e55")
    rsi = _slot(features, "rsi")
    atr = _slot(features, "atr")
    close = _slot(features, "close")
    chg = _slot(features, "chg")
    rej = _slot(features, "rej")

    # Gate — trend confirmed + strictly ordered stack; neither stack -> 0.0.
    if adx <= adx_min:
        return 0.0
    if e8 > e21 > e55:
        sign = 1.0
    elif e8 < e21 < e55:
        sign = -1.0
    else:
        return 0.0

    # Trigger — BOTH legs must hold (spec §S1): (pullback band OR near-EMA)
    # AND (fresh tick direction OR rejection bar in trade direction).
    if sign > 0.0:
        leg1 = rsi_lo <= rsi <= rsi_hi or close <= e21 + near_atr * atr
        leg2 = chg > 0.0 or rej == 1.0
    else:
        leg1 = (rsi_lo + short_offset) <= rsi <= (rsi_hi + short_offset) or (
            close >= e21 - near_atr * atr
        )
        leg2 = chg < 0.0 or rej == -1.0
    if not (leg1 and leg2):
        return 0.0

    # Degenerate price (e55 == 0) would divide below — typed error, never inf
    # or a crash (spec §S1 edge).
    if e55 == 0.0:
        raise ValueError("features.e55: must be non-zero for stack progress")
    progress = abs(e8 - e55) / abs(e55) / (stack_full_pct / 100.0)
    return sign * (0.5 + 0.5 * min(1.0, progress))
