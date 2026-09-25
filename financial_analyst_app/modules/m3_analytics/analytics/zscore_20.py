"""WHY: P18 pure compute — z-score of last close vs its window (D4 per-analytic).

Spec: docs/specs/m3.2_p18_per_asset.spec.md §3. sigma == 0 -> 0.0 is the PROJECT
canonical branch (flat window: last == mean, 0/0 has no dispersion meaning —
baseline zero, never nan). Same no-epsilon law as rsi_14 (branch, never 1e-9).
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "zscore_20"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=20,  # pinned mathematical minimum: window mean/sigma need p closes
    warmup=0,
    defaults={"period": 20.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """(last - mean) / sigma over the last `period` closes — full precision."""
    if params is None:
        raise ValueError(
            "zscore_20 params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "zscore_20 needs at least one candle"
        )
    closes = [candle.close for candle in candles]
    if not all(math.isfinite(close) for close in closes):
        return math.nan
    period = _period(params)
    if len(closes) < period:
        return math.nan
    window = closes[-period:]
    mean = sum(window) / period
    var = sum((x - mean) ** 2 for x in window) / period  # ddof=0 (REV4 pin)
    sigma = math.sqrt(var)
    if sigma == 0.0:
        return 0.0  # canonical spec branch: no dispersion -> baseline zero
    return (window[-1] - mean) / sigma


def _period(params: dict[str, float]) -> int:
    """§3.13: integral >= 2 (a dispersion stat needs >= 2 samples), finite."""
    raw = params.get("period", 20.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"zscore_20 period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 2:
        raise ValueError(
            f"zscore_20 period must be an integral value >= 2, got {raw!r}"
        )
    return int(value)
