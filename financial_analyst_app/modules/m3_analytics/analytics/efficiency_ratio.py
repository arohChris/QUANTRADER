"""WHY: Book Feature Library batch 1 — Kaufman Efficiency Ratio (book card
`efficiency_ratio(bars, i, n)`; book alias: ER). The book's trending filters
gate on ER >0.25/>0.3/>0.4 (B1/B3/A1), so path==0 MUST map to 0.0, not NaN —
a flat market must FAIL a trend gate, not dodge it (spec D5 safer-assumption).

Spec: docs/specs/m3.2_book_feature_library.spec.md §5.1. Formula: Kaufman,
*New Trading Systems and Methods* — |net move| / sum|step| over the last n
bars, 0..1 form (the book's gates compare the unitless 0..1 value, not x100).
WHY not a precomputed rolling helper: Isolation Law — one file, one pure
compute, params carried per request (profile merge).
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "efficiency_ratio"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=21,  # default p=20: net spans closes[-1-p] -> p+1 closes
    warmup=0,
    defaults={"period": 20.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """Kaufman ER = |c[-1]-c[-1-p]| / (sum of last p |deltas|); flat -> 0.0."""
    if params is None:
        raise ValueError(
            "efficiency_ratio params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "efficiency_ratio needs at least one candle"
        )
    if not all(
        math.isfinite(c.high) and math.isfinite(c.low) and math.isfinite(c.close)
        for c in candles
    ):
        return math.nan
    period = _period(params)
    if len(candles) < period + 1:
        return math.nan
    closes = [c.close for c in candles]
    net = abs(closes[-1] - closes[-1 - period])
    path = sum(
        abs(closes[t] - closes[t - 1])
        for t in range(len(closes) - period, len(closes))
    )
    if path == 0.0:
        return 0.0  # D5: no movement = no efficiency — trend gates must fail
    return net / path  # triangle inequality keeps the result in [0,1]


def _period(params: dict[str, float]) -> int:
    """§3.13: integral >= 2 — isfinite BEFORE int() (no int(inf) crash-class)."""
    raw = params.get("period", 20.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"efficiency_ratio period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 2:
        raise ValueError(
            f"efficiency_ratio period must be an integral value >= 2, got {raw!r}"
        )
    return int(value)
