"""WHY: Book Feature Library batch 1 — book card `bbwp(bars, i, lookback)`
under the full unabbreviated name per naming law. Percentile rank of the
current Bollinger Bandwidth over the lookback window; the book's contraction
gates: A3 `bbwp < 25`, E1 `< 20` (squeeze before a breakout).

Spec: docs/specs/m3.2_book_feature_library.spec.md §5.9. TradingView
ta.percentrank semantics over the bandwidth SERIES: `100 * #{v <= cur} /
lookback`, current bandwidth included; ANY na in the window -> na (Pine rule —
a mid==0 bar poisons the rank honestly, never dropped as a sample). D4 trap:
constant bandwidths -> 100.0 (inclusive <=), which is why the book pairs this
gate with trend gates. WHY self-contained bandwidth math (not a call into
bollinger_bandwidth): §4 — no imports beyond math + contract types, each file
is independently discoverable and replaceable.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "bollinger_bandwidth_percentile"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=119,  # lookback 100 + period 20 - 1: bandwidth count N-p+1 >= lookback
    warmup=0,
    defaults={"period": 20.0, "stddev": 2.0, "lookback": 100.0},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """TV percentrank of current BandWidth over the last `lookback` bandwidths."""
    if params is None:
        raise ValueError(
            "bollinger_bandwidth_percentile params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "bollinger_bandwidth_percentile needs at least one candle"
        )
    if not all(
        math.isfinite(c.high) and math.isfinite(c.low) and math.isfinite(c.close)
        for c in candles
    ):
        return math.nan
    period = _period(params)
    k = _stddev(params)
    lookback = _lookback(params)
    if len(candles) < lookback + period - 1:
        return math.nan  # not enough bandwidth values to fill the rank window
    closes = [c.close for c in candles]
    bandwidths = [
        _bandwidth(closes[s : s + period], k)
        for s in range(len(closes) - period + 1)
    ]
    tail = bandwidths[-lookback:]  # always exactly `lookback` long (checked above)
    if any(math.isnan(value) for value in tail):
        return math.nan  # Pine na rule: na anywhere in window -> na result
    current = tail[-1]
    count = sum(1 for value in tail if value <= current)  # inclusive <= (TV)
    return 100.0 * count / lookback


def _bandwidth(window: list[float], k: float) -> float:
    """(upper-lower)/mid for one window; mid == 0 -> nan (D8, same as 5.8)."""
    period = len(window)
    mid = sum(window) / period
    if mid == 0.0:
        return math.nan
    var = sum((x - mid) ** 2 for x in window) / period  # ddof=0 (REV4 pin)
    return 2.0 * k * math.sqrt(var) / mid


def _period(params: dict[str, float]) -> int:
    """§3.13: integral >= 1 — isfinite BEFORE int() (no int(inf) crash-class)."""
    raw = params.get("period", 20.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"bollinger_bandwidth_percentile period must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 1:
        raise ValueError(
            f"bollinger_bandwidth_percentile period must be an integral value >= 1, got {raw!r}"
        )
    return int(value)


def _stddev(params: dict[str, float]) -> float:
    """Band width multiplier: finite and >= 0 (0 -> all bandwidths 0 -> 100.0)."""
    raw = params.get("stddev", 2.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"bollinger_bandwidth_percentile stddev must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError(
            f"bollinger_bandwidth_percentile stddev must be a finite value >= 0, got {raw!r}"
        )
    return value


def _lookback(params: dict[str, float]) -> int:
    """Rank window: integral >= 2 (a 1-sample rank is always 100 by construction)."""
    raw = params.get("lookback", 100.0)
    if not isinstance(raw, (int, float)):
        raise ValueError(  # noqa: TRY004 — spec-frozen ValueError contract
            f"bollinger_bandwidth_percentile lookback must be a number, got {raw!r}"
        )
    value = float(raw)
    if not math.isfinite(value) or value != int(value) or int(value) < 2:
        raise ValueError(
            f"bollinger_bandwidth_percentile lookback must be an integral value >= 2, got {raw!r}"
        )
    return int(value)
