"""WHY: D4 pure compute — last completed bar's close (A1 gate slot).

Spec: docs/specs/m4.7_playbook_tier1.spec.md §Prerequisite analytics #2.
Isolation Law: compute(candles, params) only — no provider/clock/as_of/timeframe.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "last_close"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=1,
    warmup=0,
    defaults={},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """Last bar's close; non-finite anywhere -> nan honest refusal."""
    if params is None:
        raise ValueError("last_close params must be a dict, got None")
    if not candles:
        raise ValueError("last_close needs at least one candle")
    closes = [candle.close for candle in candles]
    if not all(math.isfinite(close) for close in closes):
        return math.nan
    return closes[-1]
