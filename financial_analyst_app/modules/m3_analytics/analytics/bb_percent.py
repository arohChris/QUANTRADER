"""WHY: P18 pure compute — Bollinger %B (D4 one-file-per-analytic).

Spec: docs/specs/m3.2_p18_per_asset.spec.md §8. sigma = population ddof=0 (REV4
pin — same definition as std_20/zscore_20/vol_regime). %B is UNCLAMPED: a value
> 1 or < 0 (price outside the bands) IS the signal — clamping would destroy it.
Canonical zero-width branch: sigma == 0 or k == 0 -> 0.5 (price at midline).
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "bb_percent"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=20,  # pinned mathematical minimum: p-window mean/sigma
    warmup=0,
    defaults={"period": 20.0, "stddev": 2.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """%B = (last - lower) / (upper - lower), bands = mean +/- k*sigma — unclamped."""
    if params is None:
        raise ValueError(
            "bb_percent params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "bb_percent needs at least one candle"
        )
    closes = [candle.close for candle in candles]
    if not all(math.isfinite(close) for close in closes):
        return math.nan
    period = _period(params)
    k = _stddev(params)
    if len(closes) < period:
        return math.nan
    window = closes[-period:]
    mean = sum(window) / period
    var = sum((x - mean) ** 2 for x in window) / period  # ddof=0 (REV4 pin)
    sigma = math.sqrt(var)
    upper, lower = mean + k * sigma, mean - k * sigma
    width = upper - lower
    if width == 0.0:
        return 0.5  # canonical: zero-width band -> price AT the midline
    return (window[-1] - lower) / width


def _period(params: dict[str, float]) -> int:
    """§3.13: integral >= 1, finite — isfinite BEFORE int() (no int(inf) crash-class)."""
    raw = params.get("period", 20.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"bb_percent period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"bb_percent period must be an integral value >= 1, got {raw!r}"
        )
    return int(value)


def _stddev(params: dict[str, float]) -> float:
    """Band width multiplier: finite and >= 0 (0 -> canonical 0.5 branch, tested)."""
    raw = params.get("stddev", 2.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"bb_percent stddev must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(
            f"bb_percent stddev must be a finite value >= 0, got {raw!r}"
        )
    return value
