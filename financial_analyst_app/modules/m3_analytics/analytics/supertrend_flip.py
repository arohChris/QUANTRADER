"""WHY: D4 pure compute — fresh Supertrend direction flip (S5 trigger slot).

Spec: docs/specs/m4.7_playbook_tier1.spec.md §Prerequisite analytics #8.
Classic Supertrend (ATR bands + ratchet), pinned hand-verifiable algorithm:
TR/ATR (atr_14 TV convention) -> basicU/basicL -> ratcheted U/L -> dir;
output = flip at the LAST bar only (fresh = dir changed at i).
Isolation Law: compute(candles, params) only — no provider/clock/as_of/timeframe.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "supertrend_flip"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=11,  # default period 10 + 1
    warmup=0,
    defaults={"mult": 3.0, "period": 10.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """+1.0 fresh up-flip, -1.0 fresh down-flip, 0.0 otherwise (incl. 1-bar)."""
    if params is None:
        raise ValueError("supertrend_flip params must be a dict, got None")
    if not candles:
        raise ValueError("supertrend_flip needs at least one candle")
    for bar in candles:
        if not all(math.isfinite(v) for v in (bar.open, bar.high, bar.low, bar.close)):
            return math.nan
    period = _period(params)
    mult = _mult(params)
    n = len(candles)
    if n < 2:
        return 0.0  # no previous bar -> nothing to flip against
    tr = _true_range(candles)
    atr = _atr(tr, period)
    upper: list[float] = []
    lower: list[float] = []
    direction: list[int] = []
    for t, bar in enumerate(candles):
        mid = (bar.high + bar.low) / 2.0
        basic_u = mid + mult * atr[t]
        basic_l = mid - mult * atr[t]
        if t == 0:
            upper.append(basic_u)
            lower.append(basic_l)
            direction.append(1 if bar.close >= mid else -1)
            continue
        prev_u, prev_l, prev_d = upper[-1], lower[-1], direction[-1]
        upper.append(basic_u if (basic_u < prev_u or bar.close > prev_u) else prev_u)
        lower.append(basic_l if (basic_l > prev_l or bar.close < prev_l) else prev_l)
        if bar.close > prev_u:
            direction.append(1)
        elif bar.close < prev_l:
            direction.append(-1)
        else:
            direction.append(prev_d)
    if direction[-1] != direction[-2]:
        return float(direction[-1])  # WHY last bar only: fresh flip = event at i
    return 0.0


def _true_range(candles: list[Candle]) -> list[float]:
    """TR_0 = h-l; TR_t = max(h-l, |h-c_{t-1}|, |l-c_{t-1}|) (atr_14 convention)."""
    tr: list[float] = []
    for t, bar in enumerate(candles):
        if t == 0:
            tr.append(bar.high - bar.low)
        else:
            prev_close = candles[t - 1].close
            tr.append(
                max(
                    bar.high - bar.low,
                    abs(bar.high - prev_close),
                    abs(bar.low - prev_close),
                )
            )
    return tr


def _atr(tr: list[float], period: int) -> list[float]:
    """SMA seed through t=period-1, then Wilder recursion (spec-pinned)."""
    atr: list[float] = []
    for t in range(len(tr)):
        if t < period - 1:
            atr.append(sum(tr[: t + 1]) / (t + 1))
        elif t == period - 1:
            atr.append(sum(tr[:period]) / period)
        else:
            atr.append((atr[-1] * (period - 1) + tr[t]) / period)
    return atr


def _period(params: dict[str, float]) -> int:
    """§3.13 bounds: integral >= 1, finite."""
    raw = params.get("period", 10.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"supertrend_flip period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"supertrend_flip period must be an integral value >= 1, got {raw!r}"
        )
    return int(value)


def _mult(params: dict[str, float]) -> float:
    """§3.13 bounds: finite > 0."""
    raw = params.get("mult", 3.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"supertrend_flip mult must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"supertrend_flip mult must be finite > 0, got {raw!r}")
    return value
