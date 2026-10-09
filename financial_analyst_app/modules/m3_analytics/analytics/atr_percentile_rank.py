"""WHY: Book Feature Library batch 1 — the book's most-used volume-free
volatility gate (B1 `atr_percentile_rank(bars, i, 250) <= 30 -> skip`,
B3 `<= 40`, D1 `<= 50`) which the book USES but never DEFINES — spec-filled
(§6 D3) as TradingView `ta.percentrank` over the pinned atr_14 ATR series:
`100 * #{v <= cur} / window`, current value included in the window.

WHY TV percentrank semantics: the published 30/40/50 thresholds were
calibrated against the inclusive `<=` integer count — an exclusive or
interpolated percentile silently shifts every gate. D4 trap (constant window
-> 100.0) is TV-compatible; the book pairs these gates with ER/AD gates.

Spec: docs/specs/m3.2_book_feature_library.spec.md §5.2 (+ §6 D3/D4).
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "atr_percentile_rank"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=263,  # window 250 + period 14 - 1: ATR count N-p+1 >= window
    warmup=0,
    defaults={"period": 14.0, "window": 250.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """TV ta.percentrank of the current Wilder ATR over the last `window` ATRs."""
    if params is None:
        raise ValueError(
            "atr_percentile_rank params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "atr_percentile_rank needs at least one candle"
        )
    if not all(
        math.isfinite(c.high) and math.isfinite(c.low) and math.isfinite(c.close)
        for c in candles
    ):
        return math.nan
    period = _period(params)
    window = _window(params)
    if len(candles) < window + period - 1:
        return math.nan  # not enough ATR values to fill the rank window
    atrs = _wilders_atr(candles, period)
    current = atrs[-1]
    tail = atrs[-window:]  # window always exactly `window` long (checked above)
    count = sum(1 for value in tail if value <= current)  # inclusive <= (TV)
    return 100.0 * count / window


def _wilders_atr(candles: list[Candle], period: int) -> list[float]:
    """Pinned atr_14 convention: TV ta.tr(true) ranges, Wilder alpha=1/p,
    seed = SMA of the first p TRs — one valid ATR per bar from bar p-1 on."""
    trs = _true_ranges(candles)
    atr = sum(trs[:period]) / period
    out = [atr]
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
        out.append(atr)
    return out


def _true_ranges(candles: list[Candle]) -> list[float]:
    """TV ta.tr(true): TR_0 = h - l; after that max(h-l, |h-pc|, |l-pc|)."""
    trs = [candles[0].high - candles[0].low]
    for i in range(1, len(candles)):
        c, prev = candles[i], candles[i - 1]
        trs.append(
            max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close))
        )
    return trs


def _period(params: dict[str, float]) -> int:
    """§3.13: integral >= 1 — isfinite BEFORE int() (no int(inf) crash-class)."""
    raw = params.get("period", 14.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"atr_percentile_rank period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"atr_percentile_rank period must be an integral value >= 1, got {raw!r}"
        )
    return int(value)


def _window(params: dict[str, float]) -> int:
    """Rank window: integral >= 2 (a 1-sample rank is always 100 by construction)."""
    raw = params.get("window", 250.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"atr_percentile_rank window must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 2:
        raise ValueError(
            f"atr_percentile_rank window must be an integral value >= 2, got {raw!r}"
        )
    return int(value)
