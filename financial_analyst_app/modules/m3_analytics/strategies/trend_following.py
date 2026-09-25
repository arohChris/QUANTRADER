"""WHY: M4.2 D5 `trend_following` family — fast/slow EMA-SMA cross gated by RSI so
whipsaw crosses under weak momentum emit no signal. Slots pin the overrides
(ema_20@10, sma_20@20) so M4.1's single FeatureRequest serves all three analytics.

Pure compute (no I/O, no state): evaluate(features, params) -> signed strength
[-1, +1]; positive = long, negative = short, 0.0 = none (contract:
docs/specs/m4.2_strategy_files.spec.md — PROVISIONAL until M4.1 registry).
"""
from __future__ import annotations

import math

NAME = "trend_following"
FEATURE_SLOTS: dict[str, tuple[str, dict[str, float]]] = {
    "fast": ("ema_20", {"period": 10.0}),
    "slow": ("sma_20", {"period": 20.0}),
    "rsi": ("rsi_14", {}),
}
PARAM_DEFAULTS: dict[str, float] = {"rsi_threshold": 60.0, "divergence_full_pct": 1.0}


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
    rsi_threshold = params.get("rsi_threshold", PARAM_DEFAULTS["rsi_threshold"])
    if not math.isfinite(rsi_threshold) or not 50.0 < rsi_threshold <= 100.0:
        raise ValueError(
            f"params.rsi_threshold: must be in (50, 100], got {rsi_threshold!r}"
        )
    full_pct = params.get("divergence_full_pct", PARAM_DEFAULTS["divergence_full_pct"])
    if not math.isfinite(full_pct) or full_pct <= 0.0:
        raise ValueError(
            f"params.divergence_full_pct: must be > 0, got {full_pct!r}"
        )
    fast = _slot(features, "fast")
    slow = _slot(features, "slow")
    rsi = _slot(features, "rsi")

    if fast > slow and rsi >= rsi_threshold:
        sign = 1.0
    elif fast < slow and rsi <= 100.0 - rsi_threshold:
        sign = -1.0
    else:
        return 0.0
    # House ramp: 0.5 at trigger -> 1.0 at full divergence; min() clamps overshoot.
    progress = abs(fast - slow) / slow / (full_pct / 100.0)
    return sign * (0.5 + 0.5 * min(1.0, progress))
