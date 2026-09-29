"""
M1.1 v2.0 — PortfolioState / Position — Owned by M5 Portfolio (per-tenant)

BUMP 1.0 -> 2.0 (docs/CONTRACT_V2_PROPOSAL.md, human LGTM 2026-09-27):
 - all money float -> Decimal via MoneyDecimal (D34)
 - required trading_account_id; user_id no longer defaults to None (D36)
 - Position now inherits BaseContract, so it carries correlation_id/version (D37)
 - symbol -> instrument_id, matching M2/M2b naming
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import Field

from .base import AwareUtcDatetime, BaseContract, MoneyDecimal


class Position(BaseContract):
    """
    WHY does this inherit BaseContract now (D37)? As a plain BaseModel it carried no
    correlation_id, no version and no timestamp, so a Position could not be traced to
    the trade that created it. That breaks the AGENTS.md 3.12 audit chain: grepping
    correlation_id must find tick -> ... -> position -> fill -> ledger. It was never
    exercised by any module, which is why the gap went unnoticed.

    WHY MoneyDecimal and not Decimal? A bare `Decimal` in pydantic lax mode silently
    accepts float. See shared.contracts.base._coerce_money.
    """

    # WHY explicit: only contracts that actually changed bump. BaseContract stays 1.0
    # so AnalyticsResult/Signal/MarketData do not falsely report v2.
    version: str = Field(default="2.0", description="Bumped 1.0->2.0: Decimal money + BaseContract")
    instrument_id: str
    qty: MoneyDecimal = Field(description="Lots — positive long, negative short, 0 flat")
    avg_entry: MoneyDecimal = Field(description="Weighted avg entry price")
    unrealized_pnl: MoneyDecimal | None = None
    realized_pnl: MoneyDecimal | None = None
    last_trade_plan_id: UUID | None = Field(default=None, description="For audit lineage")
    marked_at: AwareUtcDatetime | None = Field(
        default=None, description="When unrealized_pnl was last marked (aware UTC)"
    )


class PortfolioState(BaseContract):
    """
    WHY required trading_account_id (D36)? `user_id: str | None = None` made
    PortfolioState(user_id=None) constructible, so a branch like
    `if portfolio.user_id is None: load_all()` was reachable — a cross-tenant leak.
    Missing tenant must be a ValidationError, never a None that reads as "everyone".

    WHY user_id/broker are KEPT rather than removed: v1 rows and existing consumers
    still read them. broker_account_id (D24) is deliberately NOT here — a user holding
    two accounts at ONE broker is why it is needed, and it belongs with the RLS
    schema slice, not smuggled into a type bump.

    M5 is the ONLY writer. M3 reads this (read-only) for exposure/VaR.
    M7 compares vs broker.
    """

    version: str = Field(default="2.0", description="Bumped 1.0->2.0: Decimal money + required tenant")
    trading_account_id: UUID
    # WHY user_id stays `str | None` and not UUID: these two are explicitly v1-compat
    # fields, and shared/core/tenant_context.py still carries user_id as a str
    # (ContextVar[str | None]). Re-typing them to UUID here would break that context
    # contract for no gain. `trading_account_id` is the UUID because it is a new PK.
    # Converting the rest belongs to the D24/RLS slice, not a type bump.
    user_id: str | None = Field(default=None, description="v1 compat — use trading_account_id")
    broker: str | None = Field(default=None, description="v1 compat — use trading_account_id")
    positions: dict[str, Position] = Field(default_factory=dict)
    cash: dict[str, MoneyDecimal] = Field(
        default_factory=dict, description="{USD: 1000.00, EUR: 500.00}"
    )
    equity: MoneyDecimal = Decimal("0.00")
    margin_used: MoneyDecimal = Decimal("0.00")
    margin_available: MoneyDecimal = Decimal("0.00")
    leverage: MoneyDecimal = Decimal(1)
    exposure: dict[str, MoneyDecimal] = Field(default_factory=dict)
    as_of: AwareUtcDatetime | None = Field(
        default=None,
        description="When these figures were true. Risk FAILS CLOSED if this is stale "
        "(AGENTS.md 3.3) — approving an order on possibly-spent equity over-levers.",
    )


PortfolioState.model_rebuild()
Position.model_rebuild()
