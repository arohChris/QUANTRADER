"""WHY: Book Feature Library batch 1 — book card `swing_low(bars, i)`: fractal
pivot LOW, the exact mirror of swing_high — strict local minimum vs `k` bars
each side (k=2 book default); book structure tools read it as support.

Spec: docs/specs/m3.2_book_feature_library.spec.md §5.6 (§5.5 rules mirrored).
WHY separate file, not a direction flag: one-file-per-analytic D4 layout —
each NAME is its own capability/discovery entry, and a bool param would make
the two discovery names collapse into one.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "swing_low"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=5,  # earliest pivot j=k confirms at i=2k -> 2k+1 bars
    warmup=0,
    defaults={"lookback": 2.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """Most recent confirmed strict pivot low (k bars each side), else NaN."""
    if params is None:
        raise ValueError(
            "swing_low params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "swing_low needs at least one candle"
        )
    if not all(
        math.isfinite(c.high) and math.isfinite(c.low) and math.isfinite(c.close)
        for c in candles
    ):
        return math.nan
    k = _lookback(params)
    if len(candles) < 2 * k + 1:
        return math.nan  # no pivot can be confirmed yet
    lows = [c.low for c in candles]
    i = len(lows) - 1
    for j in range(i - k, k - 1, -1):  # newest-first: nearest confirmable pivot
        center = lows[j]
        if all(
            center < lows[m] for m in range(j - k, j + k + 1) if m != j
        ):
            return center
    return math.nan  # no confirmed strict pivot (flat/tied lows land here)


def _lookback(params: dict[str, float]) -> int:
    """§3.13: integral >= 1 (book's k, bars each side) — isfinite BEFORE int()."""
    raw = params.get("lookback", 2.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"swing_low lookback must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"swing_low lookback must be an integral value >= 1, got {raw!r}"
        )
    return int(value)
