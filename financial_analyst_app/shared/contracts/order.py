"""
M1.1 — Order / OrderIntent — Output of M6.1 Order Builder
INSTITUTIONAL HEDGE FUND GRADE — idempotent via client_order_id = uuid5(trade_plan_id)
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from .base import BaseContract


class OrderIntent(BaseContract):
    """Risk → Order — intent before Order object is built (lightweight)."""

    signal_id: UUID | None = None
    risk_decision_id: UUID | None = None
    trade_plan_id: UUID | None = None
    symbol: str
    broker: str
    side: Literal["buy", "sell"]
    qty: float
    order_type: Literal["market", "limit", "stop"]
    limit_price: float | None = None
    time_in_force: str = Field(default="GTC")
    stop_loss: float | None = None
    take_profit: float | None = None
    user_id: str | None = None


class Order(BaseContract):
    """
    WHY client_order_id deterministic (uuid5 of trade_plan_id)? Retry same TradePlan
        yields same client_order_id → broker deduplicates → no double order.
        This is the idempotency key — NEVER random uuid4 for client_order_id.
    WHY state machine? Only valid transitions: pending→submitted→acked→filled etc.
        Invalid transition (filled→cancelled) is a bug — raise IllegalTransition.
    WHY broker_capability_checked? M6.1 validates order_type in broker_capabilities before send.
    """

    order_id: UUID = Field(description="Internal order id — random uuid4")
    client_order_id: str = Field(description="Idempotency key — uuid5(trade_plan_id) deterministic")
    intent_id: UUID | None = None
    trade_plan_id: UUID | None = Field(default=None, description="BEST APPROACH lineage")
    symbol: str
    broker: str
    side: Literal["buy", "sell"]
    qty: float
    order_type: str = Field(default="market", description="market | limit | stop")
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    state: Literal[
        "pending", "submitted", "acked", "partially_filled", "filled", "cancelled", "rejected", "unknown"
    ] = "pending"
    broker_capability_checked: bool = False
    user_id: str | None = None

Order.model_rebuild()
OrderIntent.model_rebuild()
