"""WHY: P18 pure compute — Wilder ATR with TradingView ta.tr(true) range (D4).

Spec: docs/specs/m3.2_p18_per_asset.spec.md §5 + REV4 pins (m3.1 §Research pins):
TR_0 = high_0 - low_0 (TV rule — TA-Lib TRANGE yields NO first-bar value; the
pin's "min-bars 15 conditional on TR policy" resolves to 14 here), smoothing =
Wilder alpha = 1/p, seed = SMA of first p TRs. Label: "TradingView ta.tr(true)
compatibility". WHY not TA-Lib TRANGE: consistency with the pinned TR policy and
a first valid value one bar sooner — never a silent mix of the two conventions.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "atr_14"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=14,  # REV4 condition resolved: TV TR gives TR at bar 0 -> 14 TRs
    warmup=0,
    defaults={"period": 14.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """Wilder ATR over TV-style true range — full precision; flat series -> 0.0."""
    if params is None:
        raise ValueError(
            "atr_14 params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "atr_14 needs at least one candle"
        )
    if not all(
        math.isfinite(c.high) and math.isfinite(c.low) and math.isfinite(c.close)
        for c in candles
    ):
        return math.nan
    period = _period(params)
    if len(candles) < period:
        return math.nan
    trs = _true_ranges(candles)
    atr = sum(trs[:period]) / period  # Wilder seed = SMA of first p TRs
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return atr


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
    """§3.13: integral >= 1, finite — isfinite BEFORE int() (no int(inf) crash-class)."""
    raw = params.get("period", 14.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"atr_14 period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"atr_14 period must be an integral value >= 1, got {raw!r}"
        )
    return int(value)
