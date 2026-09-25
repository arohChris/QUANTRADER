"""WHY: P18 pure compute — Hurst exponent via R/S on log returns (regime).

Spec: docs/specs/m3.2_p18_per_asset.spec.md §12. H = ln(R/S)/ln(w): H near 1 =
persistent/trending (cumulative deviation outruns its own noise), near 0 =
mean-reverting, 0.5 = no measurable memory. Canonical: S == 0 -> 0.5 (constant
returns have no measurable memory — single branch: centered steps all zero makes
R == 0 too, so one branch covers both, never log(0)/0-division). NO CLAMP: a
pathological series outside [0,1] reports honestly — faking a bound would hide
the math. window bound >= 2 because ln(1) = 0 would divide by zero.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "hurst"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=51,  # pinned mathematical minimum: w returns need w+1 closes
    warmup=0,
    defaults={"window": 50.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """R/S Hurst on the last `window` log returns — full precision, no clamp."""
    if params is None:
        raise ValueError(
            "hurst params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "hurst needs at least one candle"
        )
    closes = [candle.close for candle in candles]
    if not all(math.isfinite(close) for close in closes):
        return math.nan
    window = _window(params)
    if len(closes) < window + 1:
        return math.nan
    tail = closes[-(window + 1) :]
    if any(close <= 0.0 for close in tail):
        return math.nan  # log domain: nonpositive prices have no log return
    returns = [math.log(tail[i] / tail[i - 1]) for i in range(1, len(tail))]
    mean = sum(returns) / window
    cumulative = [0.0]
    for r in returns:
        cumulative.append(cumulative[-1] + (r - mean))
    r_range = max(cumulative) - min(cumulative)
    var = sum((r - mean) ** 2 for r in returns) / window  # ddof=0 (REV4 pin)
    s = math.sqrt(var)
    if s == 0.0:
        return 0.5  # canonical: constant returns -> R == 0 too -> no memory, neutral
    return math.log(r_range / s) / math.log(window)


def _window(params: dict[str, float]) -> int:
    """§3.13: integral >= 2 (ln(1) = 0 denominator), finite, isfinite BEFORE int."""
    raw = params.get("window", 50.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"hurst window must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 2:
        raise ValueError(
            f"hurst window must be an integral value >= 2, got {raw!r}"
        )
    return int(value)
