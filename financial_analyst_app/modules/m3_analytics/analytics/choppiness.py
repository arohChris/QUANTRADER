"""WHY: P18 pure compute — Choppiness index (range vs path, regime detector).

Spec: docs/specs/m3.2_p18_per_asset.spec.md §10. chop = 100*log10(sumTR/range)/
log10(p): high (near 100) = the path churned WITHOUT travelling (ranging),
low (near 0) = the path travelled efficiently (trending). TV-style TR inside
the window (same pinned TR policy as atr_14). Canonical: flat window -> 100.0
(no travel at all = pure chop); period bound >= 2 because log10(1) = 0 would
divide by zero — rejected at the param, never a ZeroDivisionError mid-request.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "choppiness"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=14,  # pinned mathematical minimum: a p-bar window
    warmup=0,
    defaults={"period": 14.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """100*log10(sum(TR)/range)/log10(period) over the last p bars — full precision."""
    if params is None:
        raise ValueError(
            "choppiness params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "choppiness needs at least one candle"
        )
    if not all(
        math.isfinite(c.high) and math.isfinite(c.low) and math.isfinite(c.close)
        for c in candles
    ):
        return math.nan
    period = _period(params)
    if len(candles) < period:
        return math.nan
    window = candles[-period:]
    tr_sum = 0.0
    for i, c in enumerate(window):
        if i == 0:
            tr_sum += c.high - c.low  # TV rule inside the window (pinned TR policy)
        else:
            prev = window[i - 1]
            tr_sum += max(
                c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close)
            )
    channel_range = max(c.high for c in window) - min(c.low for c in window)
    if channel_range == 0.0:
        return 100.0  # canonical: flat channel = pure chop (0/0 -> 100)
    if tr_sum == 0.0:
        return 0.0  # guarded impossible under TV TR (spec): no path, some range
    return 100.0 * math.log10(tr_sum / channel_range) / math.log10(period)


def _period(params: dict[str, float]) -> int:
    """§3.13: integral >= 2 (log10(1) = 0 denominator), finite, isfinite BEFORE int."""
    raw = params.get("period", 14.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"choppiness period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 2:
        raise ValueError(
            f"choppiness period must be an integral value >= 2, got {raw!r}"
        )
    return int(value)
