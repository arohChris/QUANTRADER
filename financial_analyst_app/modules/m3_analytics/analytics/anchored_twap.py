"""WHY: Book Feature Library batch 1 — book card `anchored_twap(bars, i)`:
mean(Close) since the anchor — the volume-free VWAP substitute (the book has
no volume data, hence the whole playbook).

Spec: docs/specs/m3.2_book_feature_library.spec.md §5.10. D2 declared
deviation: Day-1 anchor is the FIRST BAR OF THE REQUEST WINDOW, not the
session open — session anchoring needs M2b TradingCalendar session
boundaries, a data dependency this pure compute must not reach for (Isolation
Law: no clock/calendar parameter exists). TODO(correctness) lives in
BUILD_STATUS Open Questions until the M2b-anchored variant lands as its own
additive-param slice. WHY params shape-checked but not read: there is no
valid parameter to mis-accept, so unknown keys are inert by construction.
"""
from __future__ import annotations

import math

from modules.m3_analytics.engine.analytics_registry import DataRequirement
from shared.contracts.market_data import Candle

NAME = "anchored_twap"

REQUIREMENTS = DataRequirement(
    needs=("price",),
    minimum_history=1,  # single bar is its own window anchor
    warmup=0,
    defaults={},
)


def compute(candles: list[Candle], params: dict[str, float]) -> float:
    """Arithmetic mean of closes over the provided window (window-anchored TWAP)."""
    if params is None:
        raise ValueError(
            "anchored_twap params must be a dict, got None"
        )
    if not candles:
        raise ValueError(
            "anchored_twap needs at least one candle"
        )
    if not all(
        math.isfinite(c.high) and math.isfinite(c.low) and math.isfinite(c.close)
        for c in candles
    ):
        return math.nan
    closes = [c.close for c in candles]
    return sum(closes) / len(closes)
