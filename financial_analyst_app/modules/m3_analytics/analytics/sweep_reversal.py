"""WHY: D4 pure compute — failed-breakout sweep event (D1 trigger slot): wick
beyond the prior window extreme, close back inside, confirmed by bar i.

Spec: docs/specs/m4.7_playbook_tier1.spec.md §Prerequisite analytics #7.
Fail-closed by construction: every candidate window MUST be full (j >= window)
— a short window cannot sweep, no short-window phantoms; min-history guard
returns nan, never a silent 0.
Isolation Law: compute(candles, params) only — no provider/clock/as_of/timeframe.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "sweep_reversal"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=21,  # window 20 + the confirming bar i
    warmup=0,
    defaults={"window": 20.0, "within": 2.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """+1 low sweep + bullish confirm, -1 high sweep + bearish confirm, 0/ nan."""
    if params is None:
        raise ValueError("sweep_reversal params must be a dict, got None")
    if not candles:
        raise ValueError("sweep_reversal needs at least one candle")
    for bar in candles:
        if not all(math.isfinite(v) for v in (bar.open, bar.high, bar.low, bar.close)):
            return math.nan
    window = _window(params)
    within = _within(params)
    i = len(candles) - 1
    if i < window:
        return math.nan  # fewer than `window` prior bars -> honest refusal
    open_i, close_i = candles[i].open, candles[i].close
    if close_i == open_i:
        return 0.0  # doji confirm: no direction to confirm with
    high_sweep = False
    low_sweep = False
    for j in range(max(0, i - within), i + 1):
        if j < window:
            continue  # WHY fail-closed: partial window cannot be a sweep
        prior = candles[j - window : j]
        max_high = max(bar.high for bar in prior)
        min_low = min(bar.low for bar in prior)
        if candles[j].high > max_high and candles[j].close <= max_high:
            high_sweep = True  # wick beyond prior extreme, close back inside
        if candles[j].low < min_low and candles[j].close >= min_low:
            low_sweep = True
    if close_i > open_i:
        return 1.0 if low_sweep else 0.0  # bullish confirm needs a LOW sweep
    return -1.0 if high_sweep else 0.0  # bearish confirm needs a HIGH sweep


def _window(params: dict[str, float]) -> int:
    """§3.13 bounds: integral >= 5, finite."""
    return _integral_param(params, "window", 20.0, minimum=5)


def _within(params: dict[str, float]) -> int:
    """§3.13 bounds: integral >= 1, finite."""
    return _integral_param(params, "within", 2.0, minimum=1)


def _integral_param(params: dict[str, float], key: str, default: float, *, minimum: int) -> int:
    raw = params.get(key, default)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"sweep_reversal {key} must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < minimum:
        raise ValueError(
            f"sweep_reversal {key} must be an integral value >= {minimum}, got {raw!r}"
        )
    return int(value)
