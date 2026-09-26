"""WHY: P17 pure compute — Wilder RSI(14) (D4 one-file-per-analytic).

The FIRST indicator file: the formula lives HERE, never in the engine (M3.1
§Contracts / bible §5 Per-Asset pure functions). Spec:
docs/specs/m3.2_analytics.spec.md §rsi_14; pins: m3.1 §Research pins REV4 —
Wilder seed = SMA of first `period` changes (bible pseudocode is Cutler-SMA,
deviation D1/D4 resolved to Wilder); zero branches ARE the spec (no epsilon
guards, deviation D2); flat 0/0 -> 50 (mathematically neutral, REV5/A48 2026-09-26,
supersedes the prior flat->100 rule) is the PROJECT canonical rule, TradingView
compatibility to be confirmed by golden test, never claimed as doc-verified.
Isolation Law: compute(candles, params) only — no provider/clock/as_of/timeframe.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "rsi_14"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=15,  # REV4 pin: 14 changes need 15 closes (mathematical minimum)
    warmup=0,  # the minimum itself IS the first-valid-bar gate
    defaults={"period": 14.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """Wilder RSI — full precision; NaN = honest refusal (engine emits invalid:<f>).

    WHY sync def, no state: pure-calc budget (§4.1 analytics <20ms), deterministic
    per request. `params` arrives pre-merged by the engine (defaults > profile >
    overrides); unknown keys are forward-compat and ignored.
    """
    if params is None:
        raise ValueError(
            "rsi_14 params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "rsi_14 needs at least one candle"
        )
    closes = [candle.close for candle in candles]
    if not all(math.isfinite(close) for close in closes):
        return math.nan  # bad bar -> NaN sentinel, never a number from junk
    period = _period(params)
    if len(closes) <= period:
        # Seed needs `period` changes = period+1 closes; a dynamic override larger
        # than the static minimum reaches here -> nan, never a truncated number.
        return math.nan
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0.0, change) for change in changes]
    losses = [-change if change < 0.0 else 0.0 for change in changes]
    # WHY Wilder seed = SMA of first p changes (REV4 pin): TA-Lib/TV RMA semantics.
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    # WHY branch order + no epsilon (REV5/A48): flat RS = 0/0 is undefined, so the
    # branch IS the specification - BOTH zero checked FIRST -> neutral 50; gains-only
    # -> 100; losses-only -> 0. A 1e-17 float remnant stays > 0 and is correctly
    # NOT treated as zero (never an epsilon substitution).
    if avg_gain == 0.0 and avg_loss == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 100.0
    if avg_gain == 0.0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _period(params: dict[str, float]) -> int:
    """§3.13: integral >= 2, finite — fail loud on hostile overrides (isfinite
    checked BEFORE int() so inf/nan can never hit the int() crash-class)."""
    raw = params.get("period", 14.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"rsi_14 period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 2:
        raise ValueError(
            f"rsi_14 period must be an integral value >= 2, got {raw!r}"
        )
    return int(value)
