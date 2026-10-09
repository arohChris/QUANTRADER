"""WHY: Book Feature Library batch 1 — book card `bollinger_bandwidth(bars, i,
n, k)` = (upper - lower) / mid from bollinger_bands() = SMA(n) +/- k*stdev(n).
The official Bollinger BandWidth, equivalently 2*k*sigma/mid.

Spec: docs/specs/m3.2_book_feature_library.spec.md §5.8. Population sigma
(divide by n) — Bollinger's own specification, matching the REV4 ddof=0 pin
(same definition as bb_percent/std_20/zscore_20). D8: mid == 0 -> NaN — a
zero-centered mean is not a price level, the ratio is undefined (distinct
from %B's canonical 0.5 branch, which has a natural zero-width value).
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "bollinger_bandwidth"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=20,  # pinned mathematical minimum: p-window mean/sigma
    warmup=0,
    defaults={"period": 20.0, "stddev": 2.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """(upper-lower)/mid = 2*k*sigma/mid over the last p closes; mid 0 -> NaN."""
    if params is None:
        raise ValueError(
            "bollinger_bandwidth params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "bollinger_bandwidth needs at least one candle"
        )
    if not all(
        math.isfinite(c.high) and math.isfinite(c.low) and math.isfinite(c.close)
        for c in candles
    ):
        return math.nan
    period = _period(params)
    k = _stddev(params)
    if len(candles) < period:
        return math.nan
    window = [c.close for c in candles][-period:]
    mid = sum(window) / period
    if mid == 0.0:
        return math.nan  # D8: division guard — zero mid is not a price level
    var = sum((x - mid) ** 2 for x in window) / period  # ddof=0 (REV4 pin)
    return 2.0 * k * math.sqrt(var) / mid  # == (upper-lower)/mid by construction


def _period(params: dict[str, float]) -> int:
    """§3.13: integral >= 1 — isfinite BEFORE int() (no int(inf) crash-class)."""
    raw = params.get("period", 20.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"bollinger_bandwidth period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"bollinger_bandwidth period must be an integral value >= 1, got {raw!r}"
        )
    return int(value)


def _stddev(params: dict[str, float]) -> float:
    """Band width multiplier: finite and >= 0 (0 -> numerator 0 -> exactly 0.0)."""
    raw = params.get("stddev", 2.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"bollinger_bandwidth stddev must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(
            f"bollinger_bandwidth stddev must be a finite value >= 0, got {raw!r}"
        )
    return value
