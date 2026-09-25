"""WHY: P18 pure compute — simple moving average (D4 one-file-per-analytic).

Spec: docs/specs/m3.2_p18_per_asset.spec.md §1. Contract conformance identical
to P17 (m3.2_analytics.spec.md §Contract): Isolation Law, sync pure, no rounding,
nan = honest refusal. Whole-window finite scan (rsi_14 precedent) — a junk bar
anywhere in the served window refuses the value, never feeds partial math.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "sma_20"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=20,  # pinned mathematical minimum: a p=20 window needs 20 closes
    warmup=0,
    defaults={"period": 20.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """Mean of the last `period` closes — full precision, no rounding."""
    if params is None:
        raise ValueError(
            "sma_20 params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "sma_20 needs at least one candle"
        )
    closes = [candle.close for candle in candles]
    if not all(math.isfinite(close) for close in closes):
        return math.nan
    period = _period(params)
    if len(closes) < period:
        return math.nan
    window = closes[-period:]
    return sum(window) / period


def _period(params: dict[str, float]) -> int:
    """§3.13: integral >= 1, finite — isfinite BEFORE int() (no int(inf) crash-class)."""
    raw = params.get("period", 20.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"sma_20 period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"sma_20 period must be an integral value >= 1, got {raw!r}"
        )
    return int(value)
