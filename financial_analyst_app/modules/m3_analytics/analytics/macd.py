"""WHY: P18 pure compute — MACD line (fast EMA - slow EMA, D4 per-analytic).

Spec: docs/specs/m3.2_p18_per_asset.spec.md §7. EMAs use the Pine first-value
seed (m3.1 golden-locked REV4 pin — reused, not re-litigated). minimum = slow
(26 default) is the CHART-TRUE gate (EMA_slow through one full period; follows
the §8b EMA exemplar precedent EMA20: minimum = period) — the first-value seed
could compute earlier, we refuse until the slow period is real, never a number
dominated by the seed. fast < slow enforced (a flipped pair is a config error,
fail loud at compute per §3.13).
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "macd"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=26,  # chart-true gate: EMA_slow(26) through one full period
    warmup=0,
    defaults={"fast": 12.0, "slow": 26.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """MACD line = EMA_fast - EMA_slow (last values) — full precision, either sign."""
    if params is None:
        raise ValueError(
            "macd params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "macd needs at least one candle"
        )
    closes = [candle.close for candle in candles]
    if not all(math.isfinite(close) for close in closes):
        return math.nan
    fast = _period(params, "fast", 12.0)
    slow = _period(params, "slow", 26.0)
    if fast >= slow:
        raise ValueError(
            f"macd fast must be < slow, got fast={fast} slow={slow}"
        )
    if len(closes) < slow:
        return math.nan  # EMA_slow not yet through one full period (chart-true)
    return _ema(closes, fast) - _ema(closes, slow)


def _ema(closes: list[float], period: int) -> float:
    """Pine first-value seed (m3.1 pin): EMA_0 = close_0, alpha = 2/(p+1)."""
    alpha = 2.0 / (period + 1.0)
    ema = closes[0]
    for close in closes[1:]:
        ema = close * alpha + ema * (1.0 - alpha)
    return ema


def _period(params: dict[str, float], key: str, default: float) -> int:
    """§3.13: integral >= 1, finite — isfinite BEFORE int() (no int(inf) crash-class)."""
    raw = params.get(key, default)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"macd {key} must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"macd {key} must be an integral value >= 1, got {raw!r}"
        )
    return int(value)
