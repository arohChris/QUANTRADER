"""WHY: P18 pure compute — Wilder ADX (trend strength, D4 one-file-per-analytic).

Spec: docs/specs/m3.2_p18_per_asset.spec.md §6 + REV4 pins: Wilder smoothing on
+DM/-DM/TR, DX from the DIs, ADX = Wilder smooth of DX; minimum_history = 2p = 28
(REV4 derived pin). Canonical zero branches, no epsilon: sTR == 0 -> DI = 0/0
branch -> 0.0; (+DI + -DI) == 0 -> DX = 0.0. Flat series -> 0.0 exactly.
Direction-blind by design: ADX measures STRENGTH, not direction (DI gap is |·| —
strategies read direction from other features).
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "adx_14"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=28,  # REV4 pin: first ADX needs p DX = 2p bars (2*14)
    warmup=0,
    defaults={"period": 14.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """Wilder ADX — full precision; flat series -> 0.0 (all zero branches)."""
    if params is None:
        raise ValueError(
            "adx_14 params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "adx_14 needs at least one candle"
        )
    if not all(
        math.isfinite(c.high) and math.isfinite(c.low) and math.isfinite(c.close)
        for c in candles
    ):
        return math.nan
    period = _period(params)
    if len(candles) < 2 * period:
        return math.nan  # 2p bars: p bars to seed DM/TR, p DX to seed ADX
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    trs: list[float] = []
    for i in range(1, len(candles)):
        c, prev = candles[i], candles[i - 1]
        up = c.high - prev.high
        down = prev.low - c.low
        plus_dm.append(up if (up > down and up > 0.0) else 0.0)
        minus_dm.append(down if (down > up and down > 0.0) else 0.0)
        trs.append(max(c.high - c.low, abs(c.high - prev.close), abs(c.low - prev.close)))
    s_plus, s_minus, s_tr = _wilder(plus_dm, period), _wilder(minus_dm, period), _wilder(trs, period)
    dx: list[float] = []
    for dp, dm, tr in zip(s_plus, s_minus, s_tr, strict=True):
        if tr == 0.0:
            plus_di, minus_di = 0.0, 0.0  # canonical: no range at all -> no direction
        else:
            plus_di, minus_di = 100.0 * dp / tr, 100.0 * dm / tr
        denom = plus_di + minus_di
        dx.append(0.0 if denom == 0.0 else 100.0 * abs(plus_di - minus_di) / denom)
    return _wilder(dx, period)[-1]


def _wilder(values: list[float], period: int) -> list[float]:
    """Wilder smoothing: seed = SMA of first p, then a = (a*(p-1) + v)/p (alpha 1/p)."""
    seed = sum(values[:period]) / period
    out = [seed]
    current = seed
    for value in values[period:]:
        current = (current * (period - 1) + value) / period
        out.append(current)
    return out


def _period(params: dict[str, float]) -> int:
    """§3.13: integral >= 1, finite — isfinite BEFORE int() (no int(inf) crash-class)."""
    raw = params.get("period", 14.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"adx_14 period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"adx_14 period must be an integral value >= 1, got {raw!r}"
        )
    return int(value)
