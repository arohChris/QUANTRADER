"""WHY: D4 pure compute — candle-shape classifier for rejection wicks (S1
"rejection" trigger leg: buyers/sellers defended the level).

Spec: docs/specs/m4.7_playbook_tier1.spec.md §Prerequisite analytics #6.
The playbook says "bullish rejection candle" without spelling out the shape —
pinned by the spec: bullish checked first (both-hold tie -> +1.0, documented).
Isolation Law: compute(candles, params) only — no provider/clock/as_of/timeframe.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "rejection_bar"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=1,
    warmup=0,
    defaults={},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """+1.0 bullish rejection, -1.0 bearish, 0.0 none/doji; nan if non-finite."""
    if params is None:
        raise ValueError("rejection_bar params must be a dict, got None")
    if not candles:
        raise ValueError("rejection_bar needs at least one candle")
    bar = candles[-1]
    if not all(math.isfinite(v) for v in (bar.open, bar.high, bar.low, bar.close)):
        return math.nan
    body = abs(bar.close - bar.open)
    if body == 0:
        return 0.0  # doji: no body -> neither shape
    span = bar.high - bar.low
    lower_wick = min(bar.open, bar.close) - bar.low
    upper_wick = bar.high - max(bar.open, bar.close)
    if lower_wick >= 2.0 * body and bar.close >= bar.low + 0.5 * span:
        return 1.0
    if upper_wick >= 2.0 * body and bar.close <= bar.high - 0.5 * span:
        return -1.0  # WHY after bullish: spec pins bullish-first tie-break
    return 0.0
