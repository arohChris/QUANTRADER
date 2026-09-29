"""
M1.1 v2.0 — ExecutionResult / Fill — Output of M6 Execution, input to M7 Trust

BUMP 1.0 -> 2.0 (docs/CONTRACT_V2_PROPOSAL.md, human LGTM 2026-09-27):
 - price/qty/commission/avg_fill_price/slippage_bps float -> Decimal (D34)
 - Fill now inherits BaseContract, so it carries correlation_id (D37)
 - timestamp str|None -> AwareUtcDatetime (D38)
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from .base import AwareUtcDatetime, BaseContract, MoneyDecimal


class Fill(BaseContract):
    """
    WHY does Fill now inherit BaseContract (D37)? As a plain BaseModel it had no
    correlation_id and no timestamp, so a fill — the single most audit-critical record
    in the system — could not be tied to the order or the decision that caused it.

    WHY is the broker time `filled_at` and NOT an override of `timestamp` (D38)? Two
    different facts were being conflated: `timestamp` is when WE constructed the object,
    `filled_at` is when the BROKER filled. An override also made the field optional,
    which is a variance violation against the base class. A fill with no time is
    meaningless, so `filled_at` is REQUIRED and aware-UTC.

    WHY AwareUtcDatetime? v1 typed it `str | None`, which invites `timestamp[:10]` and
    silently mis-reads a fill across a timezone edge. Deriv sends epoch integers, and
    AwareUtcDatetime accepts those, an aware datetime, and NOTHING else — a naive
    datetime is refused rather than assumed to be UTC.
    """

    version: str = Field(default="2.0", description="Bumped 1.0->2.0: Decimal money + BaseContract")
    fill_id: str
    price: MoneyDecimal
    qty: MoneyDecimal
    filled_at: AwareUtcDatetime = Field(description="When the BROKER filled (aware UTC)")
    side: Literal["buy", "sell"] | None = None
    commission: MoneyDecimal | None = None
    slippage_cost: MoneyDecimal | None = Field(
        default=None, description="Cost of slippage in quote ccy — a real loss, not a metric"
    )


class ExecutionResult(BaseContract):
    """
    WHY fills list not single fill? Partial fills: 0.4 lot may fill as 0.2 + 0.2.
    WHY trade_plan_id? Lineage: ExecutionResult -> TradePlan -> Signal.
    WHY raw_broker_response? For audit, never for logic — logic uses typed fields only.
    WHY is 'unknown' a state? An ambiguous broker response is neither success nor
    failure. AGENTS.md 3.2 requires querying get_order_status(client_order_id) before
    deciding; assuming either outcome is how a platform double-fills.
    """

    version: str = Field(default="2.0", description="Bumped 1.0->2.0: Decimal money + idempotency echo")
    order_id: UUID
    client_order_id: str | None = Field(
        default=None, description="Echoed back so M7 can reconcile by idempotency key"
    )
    trade_plan_id: UUID | None = None
    broker_order_id: str | None = None
    fills: list[Fill] = Field(default_factory=list)
    avg_fill_price: MoneyDecimal | None = None
    filled_qty: MoneyDecimal | None = None
    state: str = Field(description="filled | partially_filled | rejected | unknown")
    latency_ms: float | None = None
    slippage_bps: MoneyDecimal | None = Field(
        default=None,
        description="Basis points of slippage. Decimal, not float: it is a COST and "
        "feeds money arithmetic. latency_ms stays float — a duration is not money.",
    )
    raw_broker_response: dict | None = Field(default=None, description="For audit only, never logic")


ExecutionResult.model_rebuild()
Fill.model_rebuild()
