"""
M1.1 — ExecutionResult / Fill — Output of M6 Execution, input to M7 Trust
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .base import BaseContract


class Fill(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    fill_id: str
    price: float
    qty: float
    side: Literal["buy", "sell"] | None = None
    timestamp: str | None = None
    commission: float | None = None


class ExecutionResult(BaseContract):
    """
    WHY fills list not single fill? Partial fills: 0.4 lot may fill as 0.2+0.2.
    WHY trade_plan_id? Lineage: ExecutionResult → TradePlan → Signal.
    WHY raw_broker_response? For audit, never for logic — logic uses typed fields only.
    """

    order_id: UUID
    trade_plan_id: UUID | None = None
    broker_order_id: str | None = None
    fills: list[Fill] = Field(default_factory=list)
    avg_fill_price: float | None = None
    state: str = Field(description="filled | partially_filled | rejected | unknown")
    latency_ms: float | None = None
    slippage_bps: float | None = None
    raw_broker_response: dict | None = Field(default=None, description="For audit only, never logic")

ExecutionResult.model_rebuild()
Fill.model_rebuild()
