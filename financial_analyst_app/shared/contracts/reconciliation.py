"""
M1.1 — ReconciliationReport — Output of M7 Trust
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .base import BaseContract


class Diff(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    symbol: str | None = None
    field: str = Field(description="positions | orders | fills | cash")
    internal: float | str | dict | None = None
    external: float | str | dict | None = None
    type: Literal["mismatch", "missing_internal", "missing_external"] = "mismatch"


class ReconciliationReport(BaseContract):
    """
    WHY status matched/mismatched? M7.2 comparator diffs internal vs broker.
        mismatched → asymmetric_lock → M5 blocks new entries for that symbol+broker+user.
    WHY per-tenant? user 42/deriv vs external 42/deriv — never compare 42 vs 99.
    """

    user_id: str | None = None
    broker: str | None = None
    status: Literal["matched", "mismatched", "degraded", "unknown"] = "matched"
    diffs: list[Diff] = Field(default_factory=list)
    resolution: str | None = Field(default=None, description="asymmetric_lock | auto_corrected | manual")

ReconciliationReport.model_rebuild()
Diff.model_rebuild()
