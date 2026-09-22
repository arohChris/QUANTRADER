"""
M1.1 — BaseContract — The Grandparent of All Contracts
INSTITUTIONAL HEDGE FUND GRADE — Speed, Zero-Error, Clean Async Event-Driven, Well-Commented

WHY this file exists:
  10 modules are written by different teams. They never import each other.
  They only import contracts. BaseContract is the single frozen root that
  guarantees: every trade event can be traced end-to-end (correlation_id),
  every schema change is versioned, and no code can accidentally mutate a
  contract after creation (frozen=True).

  If you change this file, ALL modules must bump and re-pin. That's intentional.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


def _now_utc() -> datetime:
    # WHY UTC only? Broker clocks differ. System clock is UTC via M1.2 clock.py.
    # Never use naive datetime — always timezone-aware to avoid DST bugs.
    return datetime.now(timezone.utc)


class BaseContract(BaseModel):
    """
    Frozen, versioned, traceable root.
    
    Frozen (frozen=True) — WHY: prevents accidental mutation after creation.
        Example: Signal(strength=0.76) cannot be changed to 0.90 mid-pipeline.
        If you need a new value, create a new contract (immutability = auditability).

    extra="forbid" — WHY: catches typo fields early. Adding a field requires
        explicit schema change + version bump + migration, not silent dict drift.

    version — WHY: Lake stores v1.0 parquet forever. When we add a field in v1.1,
        migrations/v1_to_v1_1.py can upgrade old rows without breaking replay.

    correlation_id — WHY: one trade = one id forever. Grep logs by this id and you
        see tick → MarketData → AnalyticsResult → Signal → TradePlan → RiskDecision
        → Order → ExecutionResult → ReconciliationReport → Ledger. No id = no audit.

    trace_id — WHY: distributed tracing (OpenTelemetry) — groups all spans of one request.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    version: str = Field(default="1.0", description="Schema version — bump on field add/remove")
    correlation_id: UUID = Field(
        default_factory=uuid.uuid4,
        description="End-to-end trade lineage id — same id flows M2→M7",
    )
    trace_id: UUID = Field(
        default_factory=uuid.uuid4,
        description="Request trace id — for OpenTelemetry / log correlation",
    )
    timestamp: datetime = Field(
        default_factory=_now_utc,
        description="When this contract was created — UTC, via clock.now()",
    )

    # Provenance — which code produced this contract (for M7 audit replay)
    provenance: Provenance | None = Field(default=None, description="Who/what produced this")


class Provenance(BaseModel):
    """
    WHY provenance? M7 Trust must answer: which strategy version + which analytics
    + which market watermark produced this fill? Without provenance, replay is guesswork.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    producer: str = Field(description="e.g., 'm4.alpha/trend_following@v2.1'")
    producer_version: str = Field(default="1.0")
    config_hash: str | None = Field(default=None, description="SHA of config YAML at time")
    analytics_ids: list[UUID] = Field(default_factory=list)
    market_watermark: int | None = Field(default=None)
    signal_strength: float | None = Field(default=None, description="Preserved into TradePlan")
    extra: dict[str, Any] = Field(default_factory=dict)


class Explainability(BaseModel):
    """WHY: M4 must say WHY it fired — not just 'long'. For M11 narrator + M7 audit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(description="Human-readable: 'EMA10 crossed above EMA20 + RSI>60'")
    feature_weights: dict[str, float] = Field(default_factory=dict)
    regime: str | None = None


# WHY rebuild? Pydantic v2 needs forward refs resolved — Provenance defined before BaseContract's annotation.
# Without this, MarketData etc that inherit BaseContract fail with "not fully defined".
BaseContract.model_rebuild()
Provenance.model_rebuild()
Explainability.model_rebuild()
