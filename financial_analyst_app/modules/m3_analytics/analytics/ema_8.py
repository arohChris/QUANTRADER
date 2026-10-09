"""WHY: D4 pure compute — EMA(8), distinct name for the m4.2 all-distinct-slot
rule (S1 needs ema_8/ema_21/ema_55 simultaneously; ema_20@override x3 is
illegal — registry rejects shared analytics within one file).

Spec: docs/specs/m4.7_playbook_tier1.spec.md §Prerequisite analytics #1.
Algorithm + first-value seed identical to ema_20.py (m3.1 REV4 pin).
Isolation Law: compute(candles, params) only — no provider/clock/as_of/timeframe.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "ema_8"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=8,  # §8b exemplar: minimum_history == period
    warmup=2,  # 25% ratio (m3.1 exemplar: EMA200 warmup 50 -> EMA8 warmup 2)
    defaults={"period": 8.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """EMA — seed = first close (REV4 pin), alpha-recursion, full precision."""
    if params is None:
        raise ValueError("ema_8 params must be a dict, got None")
    if not candles:
        raise ValueError("ema_8 needs at least one candle")
    closes = [candle.close for candle in candles]
    if not all(math.isfinite(close) for close in closes):
        return math.nan  # bad bar -> NaN sentinel, never raises, never a number
    period = _period(params)
    alpha = 2.0 / (period + 1.0)
    ema = closes[0]  # WHY first-value seed: REV4 pin — golden test locks it
    for close in closes[1:]:
        ema = alpha * close + (1.0 - alpha) * ema
    return ema


def _period(params: dict[str, float]) -> int:
    """§3.13-style bounds: integral >= 1 (EMA(1) = last close), finite."""
    raw = params.get("period", 8.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"ema_8 period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(f"ema_8 period must be an integral value >= 1, got {raw!r}")
    return int(value)
