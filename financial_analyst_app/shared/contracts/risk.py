"""
M1.1 v2.0 — RiskDecision — Output of M5 Risk, input to M6 Order Builder

BUMP 1.0 -> 2.0 (docs/CONTRACT_V2_PROPOSAL.md, human LGTM 2026-09-27):
 - approved_qty float -> Decimal (D34)
 - REQUIRED trading_account_id, balance, required_margin (D35)
"""
from __future__ import annotations

from uuid import UUID

from pydantic import Field

from .base import AwareUtcDatetime, BaseContract, MoneyDecimal


class RiskDecision(BaseContract):
    """
    WHY approved_qty not qty? approved_qty is AFTER sizing + clamping (min/step/max).
    WHY trade_plan_id? Links RiskDecision -> TradePlan (not Signal directly) lineage.
    WHY limits_checked? M7 audit needs to know which of the 10 checks passed.
    P95 <10ms — this contract must be tiny, no I/O.

    WHY are balance and required_margin now REQUIRED (D35)? v1 had neither, so a risk
    decision could not be reconstructed: AGENTS.md 3.12 requires every risk decision
    logged "with exact numbers: balance, required_margin, approved_qty" and
    attributable to a user. Without them an auditor cannot answer "why was this size
    approved?" — and this is the contract under mandatory human review.

    WHY the evaluated_* fields are separate from the stored values? A verdict must
    record what it DECIDED and what it SAW. If balance and balance_at_decision are the
    same field, a later refresh silently rewrites history.
    """

    version: str = Field(default="2.0", description="Bumped 1.0->2.0: Decimal qty + required audit fields")
    decision_id: UUID = Field(description="Unique risk decision id")
    signal_id: UUID | None = Field(default=None, description="Original signal id")
    trade_plan_id: UUID | None = Field(default=None, description="BEST APPROACH: TradePlan id")
    approved: bool
    approved_qty: MoneyDecimal | None = Field(default=None, description="None if rejected")
    rejected_reason: str | None = Field(default=None, description="ErrorCode: insufficient_margin etc.")
    sizing_method: str = Field(default="vol_target", description="vol_target | fixed_fractional | risk_budget")
    limits_checked: dict[str, bool] = Field(default_factory=dict)
    rejected_details: dict | None = None
    latency_budget_ms: float | None = None

    # --- required for auditability (D35) ---
    trading_account_id: UUID = Field(description="Which account this decision applies to")
    balance: MoneyDecimal = Field(description="Balance this decision was evaluated against")
    required_margin: MoneyDecimal = Field(description="Margin this order would consume")

    # --- what the decision SAW, so a later refresh cannot rewrite history ---
    balance_as_of: AwareUtcDatetime | None = Field(
        default=None,
        description="When `balance` was true. Risk FAILS CLOSED when this is stale "
        "(AGENTS.md 3.3) — approving on possibly-spent equity over-levers the account.",
    )
    currency: str = Field(default="USD", description="Quote currency of balance/required_margin")


RiskDecision.model_rebuild()
