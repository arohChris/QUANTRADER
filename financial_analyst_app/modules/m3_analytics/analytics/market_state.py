"""WHY: Book Feature Library batch 1 — book card `market_state(bars, i)` ->
'bull' (HH/HL) | 'bear' (LH/LL) | 'chop', from the tracked pivot sequence
(5.5/5.6 strict rules). Book gates A1/A2/A3/D2/S1 branch on structure state.

Spec: docs/specs/m3.2_book_feature_library.spec.md §5.7. D1 declared deviation:
encoded as +1.0/-1.0/0.0 because the frozen `AnalyticsResult.features` is
`dict[str, float]` — M4 maps to labels. WHY not a contract bump for strings:
a display label is not worth a major contract migration (§5). WHY NaN below
2 pivots of either kind: a silent 'chop' would BLOCK structure entries with
no explanation — `invalid:` warns instead (§3.3 no silent confidence).
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "market_state"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=8,  # 3k+2 for k=2: second same-type pivot confirms at i=3k+1
    warmup=0,
    defaults={"lookback": 2.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """+1.0 bull (HH/HL), -1.0 bear (LH/LL), 0.0 chop; NaN without structure."""
    if params is None:
        raise ValueError(
            "market_state params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "market_state needs at least one candle"
        )
    if not all(
        math.isfinite(c.high) and math.isfinite(c.low) and math.isfinite(c.close)
        for c in candles
    ):
        return math.nan
    k = _lookback(params)
    if len(candles) < 3 * k + 2:
        return math.nan  # second same-type pivot cannot confirm any sooner
    i = len(candles) - 1
    pivot_highs = _pivot_levels([c.high for c in candles], k, i, strict_high=True)
    pivot_lows = _pivot_levels([c.low for c in candles], k, i, strict_high=False)
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return math.nan  # not enough structure — engine flags invalid:
    higher_high = pivot_highs[-1] > pivot_highs[-2]
    lower_high = pivot_highs[-1] < pivot_highs[-2]
    higher_low = pivot_lows[-1] > pivot_lows[-2]
    lower_low = pivot_lows[-1] < pivot_lows[-2]
    if higher_high and higher_low:
        return 1.0  # HH/HL bull
    if lower_high and lower_low:
        return -1.0  # LH/LL bear
    return 0.0  # mixed or equal (strict rules already drop ties)


def _pivot_levels(
    values: list[float], k: int, i: int, *, strict_high: bool
) -> list[float]:
    """Confirmed pivot levels in bar order — only j with j + k <= i (no look-ahead)."""
    out: list[float] = []
    for j in range(k, i - k + 1):
        center = values[j]
        if strict_high:
            if all(center > values[m] for m in range(j - k, j + k + 1) if m != j):
                out.append(center)
        else:
            if all(center < values[m] for m in range(j - k, j + k + 1) if m != j):
                out.append(center)
    return out


def _lookback(params: dict[str, float]) -> int:
    """§3.13: integral >= 1 (book's k, bars each side) — isfinite BEFORE int()."""
    raw = params.get("lookback", 2.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"market_state lookback must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"market_state lookback must be an integral value >= 1, got {raw!r}"
        )
    return int(value)
