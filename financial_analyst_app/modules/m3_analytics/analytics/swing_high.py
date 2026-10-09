"""WHY: Book Feature Library batch 1 — book card `swing_high(bars, i)`: fractal
pivot high, a STRICT local maximum vs `k` bars on each side (k=2 book default).
Book structure tools (A1/D2/S5 resistance, D1/D3 structure) read this level.

Spec: docs/specs/m3.2_book_feature_library.spec.md §5.5. WHY confirmation lag
(j + k <= i): a pivot counts only once k bars have printed after it — scanning
newest-first among confirmable j is the no-look-ahead rule, and returning the
nearest one gives the level the strategy is actually standing on. WHY strict
`>`: a tie is not local structure — equal highs never mark a swing.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "swing_high"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=5,  # earliest pivot j=k confirms at i=2k -> 2k+1 bars
    warmup=0,
    defaults={"lookback": 2.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """Most recent confirmed strict pivot high (k bars each side), else NaN."""
    if params is None:
        raise ValueError(
            "swing_high params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "swing_high needs at least one candle"
        )
    if not all(
        math.isfinite(c.high) and math.isfinite(c.low) and math.isfinite(c.close)
        for c in candles
    ):
        return math.nan
    k = _lookback(params)
    if len(candles) < 2 * k + 1:
        return math.nan  # no pivot can be confirmed yet
    highs = [c.high for c in candles]
    i = len(highs) - 1
    for j in range(i - k, k - 1, -1):  # newest-first: nearest confirmable pivot
        center = highs[j]
        if all(
            center > highs[m] for m in range(j - k, j + k + 1) if m != j
        ):
            return center
    return math.nan  # no confirmed strict pivot (flat/tied highs land here)


def _lookback(params: dict[str, float]) -> int:
    """§3.13: integral >= 1 (book's k, bars each side) — isfinite BEFORE int()."""
    raw = params.get("lookback", 2.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"swing_high lookback must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"swing_high lookback must be an integral value >= 1, got {raw!r}"
        )
    return int(value)
