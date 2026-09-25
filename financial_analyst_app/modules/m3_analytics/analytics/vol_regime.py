"""WHY: P18 pure compute — current vs historical volatility ratio (regime).

Spec: docs/specs/m3.2_p18_per_asset.spec.md §11. Two DISJOINT windows (last w
vs the w before it): ratio = sigma_now / sigma_hist. WHY disjoint (not rolling
mean of sigmas): the value depends only on its own 2w bars — deterministic for
a given window, no hidden dependence on how much extra history the planner
happened to serve. Canonical: sigma_hist == 0 -> 1.0 (a flat history cannot
normalize anything — baseline neutral, documented, tested; even when the NOW
window is moving, reporting +inf or a fake spike would be noise, not signal).
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "vol_regime"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=40,  # pinned mathematical minimum: two disjoint w=20 windows
    warmup=0,
    defaults={"window": 20.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """sigma(last w) / sigma(previous w), population sigma — full precision, unclamped."""
    if params is None:
        raise ValueError(
            "vol_regime params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "vol_regime needs at least one candle"
        )
    closes = [candle.close for candle in candles]
    if not all(math.isfinite(close) for close in closes):
        return math.nan
    window = _window(params)
    if len(closes) < 2 * window:
        return math.nan
    sigma_now = _pop_std(closes[-window:])
    sigma_hist = _pop_std(closes[-2 * window : -window])
    if sigma_hist == 0.0:
        return 1.0  # canonical: flat history cannot normalize — baseline neutral
    return sigma_now / sigma_hist


def _pop_std(values: list[float]) -> float:
    """Population sigma: sqrt(mean((x - mean)^2)), ddof=0 (REV4 pin)."""
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(var)


def _window(params: dict[str, float]) -> int:
    """§3.13: integral >= 1, finite — isfinite BEFORE int() (no int(inf) crash-class)."""
    raw = params.get("window", 20.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"vol_regime window must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"vol_regime window must be an integral value >= 1, got {raw!r}"
        )
    return int(value)
