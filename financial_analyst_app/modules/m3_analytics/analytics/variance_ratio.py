"""WHY: Book Feature Library batch 1 — book card `variance_ratio(bars, i, q)`
(aliased here as `period` for profile-merge param uniformity) = Var(q-bar
returns) / (q * Var(1-bar returns)); the book's regime gate: >1 trending,
<1 mean-reverting.

Spec: docs/specs/m3.2_book_feature_library.spec.md §5.4. Correct: Lo &
MacKinlay (1988) construction — LOG returns (repo `returns.py` convention),
OVERLAPPING q-bar returns, both variances population ddof=0 around their own
means. D6: the plain ratio of variances (the book's gate form), not the
paper's bias-adjusted hypothesis-test statistic — this is a regime heuristic.
D7: Var(r1)==0 -> 1.0 (null random walk; a `<1` mean-reversion gate must fail
safe rather than divide by zero).
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "variance_ratio"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=7,  # default q=5: need N-q >= 2 overlapping q-bar returns
    warmup=0,
    defaults={"period": 5.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """VR(q) = pop_var(log q-bar returns) / (q * pop_var(log 1-bar returns))."""
    if params is None:
        raise ValueError(
            "variance_ratio params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "variance_ratio needs at least one candle"
        )
    if not all(
        math.isfinite(c.high) and math.isfinite(c.low) and math.isfinite(c.close)
        for c in candles
    ):
        return math.nan
    if any(c.close <= 0.0 for c in candles):
        return math.nan  # log domain (same rule as returns.py): <=0 price is junk
    q = _period(params)
    if len(candles) < q + 2:
        return math.nan  # need >= 2 of each return series
    closes = [c.close for c in candles]
    r1 = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    rq = [math.log(closes[i] / closes[i - q]) for i in range(q, len(closes))]
    var_1 = _population_variance(r1)
    if var_1 == 0.0:
        return 1.0  # D7: no 1-bar dispersion — null random-walk value, gate-safe
    return _population_variance(rq) / (q * var_1)


def _population_variance(values: list[float]) -> float:
    """ddof=0 around the series' own mean (REV4 pin; Lo-MacKinlay construction)."""
    mean = sum(values) / len(values)
    return sum((x - mean) ** 2 for x in values) / len(values)


def _period(params: dict[str, float]) -> int:
    """§3.13: integral >= 2 (q=1 would reduce VR to 1 by construction) — isfinite first."""
    raw = params.get("period", 5.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"variance_ratio period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 2:
        raise ValueError(
            f"variance_ratio period must be an integral value >= 2, got {raw!r}"
        )
    return int(value)
