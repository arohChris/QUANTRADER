"""WHY: Book Feature Library batch 1 — book card `ols_r2(bars, i, n)` under the
full unabbreviated name per naming law. R-squared of an OLS regression of Close
on bar index over the last n bars; A1 gates `ols_r2(bars, i, 50) > 0.6`
("is this move a straight line?").

Spec: docs/specs/m3.2_book_feature_library.spec.md §5.3. Formula: textbook
simple-regression R² = Sxy²/(Sxx·Syy) (equals Pearson r²). WHY flat -> 0.0
(D5): Syy==0 leaves R² undefined — 0.0 is the gate-safe falsy (prices going
nowhere carry no trend, so `>0.6` must fail) rather than a NaN that dodges
the gate silently.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "ols_r_squared"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=50,  # default p=50 regression points
    warmup=0,
    defaults={"period": 50.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """R² of OLS(close ~ bar index) over the last p bars; flat -> 0.0."""
    if params is None:
        raise ValueError(
            "ols_r_squared params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "ols_r_squared needs at least one candle"
        )
    if not all(
        math.isfinite(c.high) and math.isfinite(c.low) and math.isfinite(c.close)
        for c in candles
    ):
        return math.nan
    period = _period(params)
    if len(candles) < period:
        return math.nan
    closes = [c.close for c in candles][-period:]
    mean_x = (period - 1) / 2.0
    mean_y = sum(closes) / period
    sxy = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(closes))
    sxx = sum((i - mean_x) ** 2 for i in range(period))
    syy = sum((y - mean_y) ** 2 for y in closes)
    if syy == 0.0:
        return 0.0  # D5: flat series — R² undefined, gate-safe falsy
    if sxx == 0.0:
        return 0.0  # defensive: impossible for distinct x with p >= 3
    r_squared = (sxy * sxy) / (sxx * syy)
    return max(0.0, min(1.0, r_squared))  # clamp float noise only, math in [0,1]


def _period(params: dict[str, float]) -> int:
    """§3.13: integral >= 3 (2 points leave 0 regression df) — isfinite first."""
    raw = params.get("period", 50.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"ols_r_squared period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 3:
        raise ValueError(
            f"ols_r_squared period must be an integral value >= 3, got {raw!r}"
        )
    return int(value)
