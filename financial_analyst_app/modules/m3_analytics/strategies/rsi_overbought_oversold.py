"""WHY: M4.2 D5 `rsi_overbought_oversold` family — fade RSI extremes (A27 id,
capabilities entry predates this file; manifest was already true). Classic
counter-trend: overbought -> short, oversold -> long, nothing in between.

Pure compute: evaluate(features, params) -> signed strength [-1, +1]
(contract: docs/specs/m4.2_strategy_files.spec.md).
"""
from __future__ import annotations

import math

NAME = "rsi_overbought_oversold"
FEATURE_SLOTS: dict[str, tuple[str, dict[str, float]]] = {"rsi": ("rsi_14", {})}
PARAM_DEFAULTS: dict[str, float] = {"overbought": 70.0, "oversold": 30.0}


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
    overbought = params.get("overbought", PARAM_DEFAULTS["overbought"])
    if not math.isfinite(overbought) or not 0.0 < overbought < 100.0:
        raise ValueError(
            f"params.overbought: must be in (0, 100), got {overbought!r}"
        )
    oversold = params.get("oversold", PARAM_DEFAULTS["oversold"])
    if not math.isfinite(oversold) or not 0.0 < oversold < overbought:
        raise ValueError(
            f"params.oversold: must satisfy 0 < oversold < overbought, "
            f"got oversold={oversold!r} overbought={overbought!r}"
        )
    rsi = _slot(features, "rsi")

    if rsi >= overbought:
        progress = (rsi - overbought) / (100.0 - overbought)
        return -(0.5 + 0.5 * min(1.0, progress))  # house ramp: 0.5 floor at trigger
    if rsi <= oversold:
        progress = (oversold - rsi) / oversold
        return 0.5 + 0.5 * min(1.0, progress)
    return 0.0
