"""WHY: D4 pure compute — where in its range the last bar closed (A1 pullback
quality slot: close near low = touch of dynamic support).

Spec: docs/specs/m4.7_playbook_tier1.spec.md §Prerequisite analytics #4.
Canonical flat-range branch (donchian_20 style): high == low -> 0.5, never
divide-by-zero.
Isolation Law: compute(candles, params) only — no provider/clock/as_of/timeframe.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "close_position"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=1,
    warmup=0,
    defaults={},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """(close - low) / (high - low) of the last bar, 0..1 (1 = close at high)."""
    if params is None:
        raise ValueError("close_position params must be a dict, got None")
    if not candles:
        raise ValueError("close_position needs at least one candle")
    bar = candles[-1]
    if not all(math.isfinite(v) for v in (bar.high, bar.low, bar.close)):
        return math.nan
    if bar.high == bar.low:
        return 0.5  # WHY canonical half: exact tie has no direction — same
        # canonical-branch style as donchian_20 (no divide-by-zero, no magic 0/1)
    return (bar.close - bar.low) / (bar.high - bar.low)
