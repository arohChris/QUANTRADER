"""WHY: D4 pure compute — last completed bar's low (A1 pullback-touch slot).

Spec: docs/specs/m4.7_playbook_tier1.spec.md §Prerequisite analytics #3.
Isolation Law: compute(candles, params) only — no provider/clock/as_of/timeframe.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "bar_low"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=1,
    warmup=0,
    defaults={},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """Last bar's low; non-finite anywhere -> nan honest refusal."""
    if params is None:
        raise ValueError("bar_low params must be a dict, got None")
    if not candles:
        raise ValueError("bar_low needs at least one candle")
    lows = [candle.low for candle in candles]
    if not all(math.isfinite(low) for low in lows):
        return math.nan
    return lows[-1]
