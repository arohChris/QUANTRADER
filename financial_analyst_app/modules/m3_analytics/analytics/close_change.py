"""WHY: D4 pure compute — signed close-to-close change (S1 "tick" trigger leg:
fresh momentum in the stack direction).

Spec: docs/specs/m4.7_playbook_tier1.spec.md §Prerequisite analytics #5.
Isolation Law: compute(candles, params) only — no provider/clock/as_of/timeframe.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "close_change"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=2,
    warmup=0,
    defaults={},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """close_t - close_{t-1} (signed); 1 candle -> nan honest refusal."""
    if params is None:
        raise ValueError("close_change params must be a dict, got None")
    if not candles:
        raise ValueError("close_change needs at least one candle")
    closes = [candle.close for candle in candles]
    if not all(math.isfinite(close) for close in closes):
        return math.nan
    if len(closes) < 2:
        return math.nan  # WHY nan, not 0: with one bar there IS no change
    return closes[-1] - closes[-2]
