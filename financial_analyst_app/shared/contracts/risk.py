"""
M1.1 — RiskDecision — Output of M5 Risk, input to M6 Order Builder
"""
from __future__ import annotations

from uuid import UUID

from pydantic import Field

from .base import BaseContract


class RiskDecision(BaseContract):
    """
    WHY approved_qty not qty? Approved_qty is AFTER sizing + clamping (min/step/max).
    WHY trade_plan_id? Links RiskDecision → TradePlan (not Signal directly) — BEST APPROACH lineage.
    WHY limits_checked? M7 audit needs to know which of 10 checks passed.
    P95 <10ms — this contract must be tiny, no I/O.
    """

    decision_id: UUID = Field(description="Unique risk decision id")
    signal_id: UUID | None = Field(default=None, description="Original signal id")
    trade_plan_id: UUID | None = Field(default=None, description="BEST APPROACH: TradePlan id")
    approved: bool
    approved_qty: float | None = Field(default=None, description="None if rejected")
    rejected_reason: str | None = Field(default=None, description="ErrorCode: insufficient_margin etc.")
    sizing_method: str = Field(default="vol_target", description="vol_target | fixed_fractional | risk_budget")
    limits_checked: dict[str, bool] = Field(default_factory=dict)
    rejected_details: dict | None = None
    latency_budget_ms: float | None = None

RiskDecision.model_rebuild()
