"""WHY: P17 pure compute — EMA with first-value seed (D4 one-file-per-analytic).

Spec: docs/specs/m3.2_analytics.spec.md §ema_20. Seed pin (m3.1 §Research pins
REV4): first source value — a pinned IMPLEMENTATION decision locked by the golden
test (ema [2,4,6,8] p=3 -> 6.25), not a claimed doc-verified TV statement.
Isolation Law: compute(candles, params) only — no provider/clock/as_of/timeframe.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "ema_20"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=20,  # §8b EMA exemplar: minimum_history == period (EMA200: 200)
    warmup=5,  # scaled from that exemplar's 25% ratio (EMA200 warmup 50 -> EMA20 5)
    defaults={"period": 20.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """EMA — seed = first close (first-value pin), then alpha-recursion, full precision.

    NaN = honest refusal (engine drops the feature + warns invalid:<f>).
    """
    if params is None:
        raise ValueError(
            "ema_20 params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "ema_20 needs at least one candle"
        )
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
    """§3.13-style bounds: integral >= 1 (EMA(1) = last close is legal), finite."""
    raw = params.get("period", 20.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"ema_20 period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"ema_20 period must be an integral value >= 1, got {raw!r}"
        )
    return int(value)
