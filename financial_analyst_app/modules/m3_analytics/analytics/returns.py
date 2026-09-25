"""WHY: P18 pure compute — last log return (D4 one-file-per-analytic).

Spec: docs/specs/m3.2_p18_per_asset.spec.md §4. Log returns make moves additive
and sign-symmetric (up +0.1% and down -0.1% are mirror images) — the base for
hurst/vol math later. <= 0 close -> nan (log domain), never a crash-class.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "returns"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=2,  # pinned mathematical minimum: one return needs two closes
    warmup=0,
    defaults={},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """ln(close_n / close_{n-1}) — full precision; no params by design."""
    if params is None:
        raise ValueError(
            "returns params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "returns needs at least one candle"
        )
    closes = [candle.close for candle in candles]
    if not all(math.isfinite(close) for close in closes):
        return math.nan
    if len(closes) < 2:
        return math.nan  # no pair -> no return (minimum_history=2)
    prev, last = closes[-2], closes[-1]
    if prev <= 0.0 or last <= 0.0:
        return math.nan  # log domain: nonpositive prices are not a number here
    return math.log(last / prev)
