"""
M1.1 v2.0 — Order / OrderIntent — Output of M6.1 Order Builder
INSTITUTIONAL HEDGE FUND GRADE — idempotent via client_order_id = uuid5(trade_plan_id)

BUMP 1.0 -> 2.0 (docs/CONTRACT_V2_PROPOSAL.md, human LGTM 2026-09-27):
 - money float -> Decimal via MoneyDecimal (D34)
 - required trading_account_id (D36)
 - symbol -> instrument_id, matching M2/M2b naming
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from .base import AwareUtcDatetime, BaseContract, MoneyDecimal


class OrderIntent(BaseContract):
    """Risk -> Order — intent before Order object is built (lightweight).

    WHY is this a *pre-risk* intent and not an order? Because AGENTS.md 3.3 requires the
    risk gate to be structural: an Order must be structurally unreachable unless
    RiskDecision.approved is True. The intent is the request, not permission.
    """

    version: str = Field(default="2.0", description="Bumped 1.0->2.0: Decimal money + required tenant")
    signal_id: UUID | None = None
    risk_decision_id: UUID | None = None
    trade_plan_id: UUID | None = None
    instrument_id: str
    broker: str
    side: Literal["buy", "sell"]
    qty: MoneyDecimal
    order_type: Literal["market", "limit", "stop"]
    limit_price: MoneyDecimal | None = None
    time_in_force: str = Field(default="GTC")
    stop_loss: MoneyDecimal | None = None
    take_profit: MoneyDecimal | None = None
    trading_account_id: UUID
    user_id: str | None = Field(default=None, description="v1 compat — use trading_account_id")


class Order(BaseContract):
    """
    WHY client_order_id deterministic (uuid5 of trade_plan_id)? Retry same TradePlan
        yields same client_order_id -> broker deduplicates -> no double order.
        This is the idempotency key — NEVER random uuid4 for client_order_id.
    WHY state machine? Only valid transitions: pending->submitted->acked->filled etc.
        Invalid transition (filled->cancelled) is a bug — raise IllegalTransition.
    WHY broker_capability_checked? M6.1 validates order_type in broker_capabilities
        before send. An UNCHECKED order is not sendable.
    WHY 'unknown' is a legal state? An ambiguous broker response is not a failure. It
        is `unknown` until get_order_status(client_order_id) says otherwise. Assuming
        either success or failure here is how a platform double-fills (AGENTS.md 3.2).
    """

    version: str = Field(default="2.0", description="Bumped 1.0->2.0: Decimal money + required tenant")
    order_id: UUID = Field(description="Internal order id — random uuid4")
    client_order_id: str = Field(description="Idempotency key — uuid5(trade_plan_id) deterministic")
    intent_id: UUID | None = None
    trade_plan_id: UUID | None = Field(default=None, description="BEST APPROACH lineage")
    instrument_id: str
    broker: str
    side: Literal["buy", "sell"]
    qty: MoneyDecimal
    order_type: str = Field(default="market", description="market | limit | stop")
    price: MoneyDecimal | None = None
    stop_loss: MoneyDecimal | None = None
    take_profit: MoneyDecimal | None = None
    state: Literal[
        "pending", "submitted", "acked", "partially_filled", "filled", "cancelled", "rejected", "unknown"
    ] = "pending"
    broker_capability_checked: bool = False
    risk_decision_id: UUID | None = Field(
        default=None,
        description="REQUIRED before this is sendable — proves M5 approved THIS order",
    )
    trading_account_id: UUID
    user_id: str | None = Field(default=None, description="v1 compat — use trading_account_id")
    submitted_at: AwareUtcDatetime | None = Field(
        default=None, description="When sent to the broker (aware UTC) — for order->ack <500ms"
    )


Order.model_rebuild()
OrderIntent.model_rebuild()
