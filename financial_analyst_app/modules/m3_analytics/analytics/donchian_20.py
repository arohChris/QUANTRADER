"""WHY: P18 pure compute — Donchian channel position vs PRIOR window (breakout).

Spec: docs/specs/m3.2_p18_per_asset.spec.md §9. Current bar EXCLUDED from the
channel — a breakout signal must compare the last close against what the market
did BEFORE it (including the current bar would cap every value at 100 and make
breakouts unrepresentable). Values > 100 / < 0 are legal BY DESIGN (that IS the
breakout). Collapsed prior window (range 0) is a canonical three-way branch.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "donchian_20"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=21,  # pinned mathematical minimum: p prior bars + current bar
    warmup=0,
    defaults={"period": 20.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """100*(last - prior_low)/(prior_high - prior_low) on the PRIOR p-bar channel."""
    if params is None:
        raise ValueError(
            "donchian_20 params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "donchian_20 needs at least one candle"
        )
    if not all(
        math.isfinite(c.high) and math.isfinite(c.low) and math.isfinite(c.close)
        for c in candles
    ):
        return math.nan
    period = _period(params)
    if len(candles) <= period:
        return math.nan  # need p prior bars AFTER excluding the current one
    prior = candles[-(period + 1) : -1]  # current bar excluded (breakout semantics)
    prior_high = max(c.high for c in prior)
    prior_low = min(c.low for c in prior)
    last = candles[-1].close
    channel_range = prior_high - prior_low
    if channel_range > 0.0:
        return 100.0 * (last - prior_low) / channel_range  # unclamped on purpose
    if last > prior_high:
        return 100.0  # canonical: collapsed window, close broke up
    if last < prior_low:
        return 0.0  # canonical: collapsed window, close broke down
    return 50.0  # canonical: collapsed window, close still inside


def _period(params: dict[str, float]) -> int:
    """§3.13: integral >= 1, finite — isfinite BEFORE int() (no int(inf) crash-class)."""
    raw = params.get("period", 20.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"donchian_20 period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"donchian_20 period must be an integral value >= 1, got {raw!r}"
        )
    return int(value)
