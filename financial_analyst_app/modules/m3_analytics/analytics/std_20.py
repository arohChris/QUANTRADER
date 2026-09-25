"""WHY: P18 pure compute — population standard deviation (D4 one-file-per-analytic).

Spec: docs/specs/m3.2_p18_per_asset.spec.md §2. ddof=0 (REV4 pin — same sigma as
bb_percent/zscore/vol_regime/hurst, one definition of "spread" across M3.2).
Flat window -> exactly 0.0 (not an error: zero dispersion IS the answer).
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "std_20"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=20,  # pinned mathematical minimum: p closes for a p-window sigma
    warmup=0,
    defaults={"period": 20.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """Population sigma (ddof=0) of the last `period` closes — full precision."""
    if params is None:
        raise ValueError(
            "std_20 params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "std_20 needs at least one candle"
        )
    closes = [candle.close for candle in candles]
    if not all(math.isfinite(close) for close in closes):
        return math.nan
    period = _period(params)
    if len(closes) < period:
        return math.nan
    return _pop_std(closes[-period:])


def _pop_std(values: list[float]) -> float:
    """Population sigma: sqrt(mean((x - mean)^2)), ddof=0 (REV4 pin)."""
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(var)


def _period(params: dict[str, float]) -> int:
    """§3.13: integral >= 1, finite — isfinite BEFORE int() (no int(inf) crash-class)."""
    raw = params.get("period", 20.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"std_20 period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"std_20 period must be an integral value >= 1, got {raw!r}"
        )
    return int(value)
