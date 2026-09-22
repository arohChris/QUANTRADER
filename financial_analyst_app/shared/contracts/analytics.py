"""
M1.1 — AnalyticsResult — Output of M3 Analytics
INSTITUTIONAL HEDGE FUND GRADE — pure compute, deterministic, frozen
"""
from __future__ import annotations

from pydantic import Field

from .base import BaseContract
from .market_data import CapabilityStatus


class AnalyticsResult(BaseContract):
    """
    WHY frozen? M4 must see exactly what M3 computed — no mutation.
    WHY features dict? 17 indicators → features["rsi_14"]=68.2, ["atr_14"]=0.0012
        M4 reads features["rsi_14"] without knowing RSI formula.
    WHY regime? M4 regime_filter skips mean_reversion when choppiness<38 (trending)
    WHY capability_status? M3.0 stamps PROXY_LIMITED so UI can warn "tick_count ≠ volume"
    """

    symbol: str
    broker: str
    timeframe: str = Field(description="1m, 5m, 1h, 1d")
    features: dict[str, float] = Field(default_factory=dict)
    regime: str | None = Field(default=None, description="trending | choppy | high_vol")
    capability_status: CapabilityStatus = Field(default=CapabilityStatus.FULLY_SUPPORTED)
    capability_warning: str | None = Field(default=None, description="e.g., 'tick_count ≠ volume'")
    # Link back to MarketData watermark for lineage
    source_watermark: int | None = None

AnalyticsResult.model_rebuild()
