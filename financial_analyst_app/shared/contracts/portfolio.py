"""
M1.1 — PortfolioState / Position — Owned by M5 Portfolio (per-tenant)
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .base import BaseContract


class Position(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    symbol: str
    qty: float = Field(description="Lots — positive long, negative short, 0 flat")
    avg_entry: float = Field(description="Weighted avg entry price")
    unrealized_pnl: float | None = None
    realized_pnl: float | None = None
    last_trade_plan_id: str | None = Field(default=None, description="For audit lineage")


class PortfolioState(BaseContract):
    """
    WHY per-tenant? user 42/deriv positions != user 42/mt5 != user 99/deriv.
    M5 is ONLY writer. M3 reads this (read-only) for exposure/VaR. M7 compares vs broker.
    """

    user_id: str | None = None
    broker: str | None = None
    positions: dict[str, Position] = Field(default_factory=dict)
    cash: dict[str, float] = Field(default_factory=dict, description="{USD: 1000, EUR: 500}")
    equity: float = 0.0
    margin_used: float = 0.0
    margin_available: float = 0.0
    leverage: float = 1.0
    exposure: dict[str, float] = Field(default_factory=dict)

PortfolioState.model_rebuild()
Position.model_rebuild()
