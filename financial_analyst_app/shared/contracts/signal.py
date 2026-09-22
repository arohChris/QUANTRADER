"""
M1.1 — Signal — Output of M4.2 families, input to M4.3/M4.4 validation
INSTITUTIONAL HEDGE FUND GRADE — frozen, time-bounded, explainable
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from .base import BaseContract, Explainability


class Signal(BaseContract):
    """
    WHY strength 0.0-1.0 not qty? M4 proposes intent+conviction. M5 decides qty.
        Separation of concerns: strategy cannot size beyond risk.
    WHY valid_until? Alpha decays. M5 rejects if now > valid_until (stale).
    WHY shadow? Shadow strategy runs in paper but signal.shadow=True → M4.3 drops before M5.
    WHY analytics_refs? Links Signal to AnalyticsResult ids for M7 lineage audit.
    """

    signal_id: UUID = Field(description="Unique signal id — also used to derive client_order_id via uuid5")
    strategy_id: str = Field(description="e.g., trend_following::ema_trend")
    version: str = Field(description="Strategy version — e.g., 2.1")
    symbol: str
    broker: str
    direction: Literal["long", "short", "exit", "hedge"]
    strength: float = Field(ge=0.0, le=1.0, description="Conviction 0-1, NOT qty")
    timeframe: str = Field(description="1m, 5m, 1h — strategy's timeframe")
    valid_until: datetime = Field(description="Freshness window — Risk rejects if expired")
    analytics_refs: list[UUID] = Field(default_factory=list)
    explainability: Explainability | None = None
    shadow: bool = Field(default=False, description="If true, not forwarded to M5")
    # Per-tenant
    user_id: str | None = None

Signal.model_rebuild()
