"""WHY: P17 pure compute — ticks-per-bar mean (D4 one-file-per-analytic).

PROXY_LIMITED semantics live in config/capabilities.yaml (`method:
tick_count_proxy`, warning `tick_count ≠ volume`) + the engine's capability gate;
this file ONLY computes mean(tick_count) over the gated window — one number, no
judgement about proxy quality. Spec: docs/specs/m3.2_analytics.spec.md §tick_rate.
Isolation Law: compute(candles, params) only — no provider/clock/as_of/timeframe.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "tick_rate"

REQUIREMENTS = DataRequirement(
    needs=("tick_count",),
    minimum_history=1,  # mean of >= 1 bar is always defined
    warmup=0,
    defaults={},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """mean(tick_count) = ticks per bar of the resolved timeframe (at 1m: ticks/min).

    Full-precision float; all-zero counts -> 0.0 (valid, not an error).
    """
    if params is None:
        raise ValueError(
            "tick_rate params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "tick_rate needs at least one candle"
        )
    counts = [candle.tick_count for candle in candles]
    # WHY guard: Candle.tick_count is int (always finite) — defensive per spec
    # Edges so a looser future input type still refuses honestly instead of NaN-propagating.
    if not all(math.isfinite(count) for count in counts):
        return math.nan
    return sum(counts) / len(counts)
