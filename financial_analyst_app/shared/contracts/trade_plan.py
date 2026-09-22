"""
M1.1 — TradePlan — BEST APPROACH M4.5 → M5 (NEW v1.3)
INSTITUTIONAL HEDGE FUND GRADE — auditable, deterministic, entry/SL/TP/timing preserved

WHY TradePlan not just Signal? Signal = "should we? how strongly?" (strength)
    TradePlan = "how? at what price, SL, TP, when valid?" (executable plan).
    M5 must validate SL/TP existence, RR, expiry BEFORE building Order.
    Storing TradePlan preserves calculation_meta for M7 ledger replay.

Flow: M4.4 Validated Signal → M4.5 TradePlanEngine → TradePlan → M5 Risk → RiskDecision
      M6 builds Order(client_order_id=uuid5(trade_plan_id)) from TradePlan (not Signal).
      M7 ledger stores trade_plan_id for lineage: Execution ← TradePlan ← Signal ← Features ← Raw
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .base import BaseContract
from .market_data import CapabilityStatus, DataQuality


class EntrySpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: str = Field(description="MARKET | LIMIT | BREAKOUT | PULLBACK | STRUCTURE | INDICATOR_TRIGGER")
    reference_price: float = Field(description="Entry reference — e.g., 1.085")
    valid_from: datetime
    valid_until: datetime
    buffer: str | None = Field(default=None, description="e.g., '2 pips' for breakout")


class StopSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: str = Field(description="STRUCTURE | ATR_2x | SWING | FIXED | STRATEGY_SPECIFIC")
    price: float
    invalidation: str = Field(description="Why this SL — e.g., 'swing_low' thesis invalidation")
    buffer: str | None = Field(default=None, description="e.g., '2*ATR' or '5 pips'")


class TargetSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: str = Field(description="RR | STRUCTURE | ATR_3x | PREVIOUS_EXTREME | TARGET_ZONE")
    price: float
    rr: float | None = Field(default=None, description="Risk-reward — e.g., 2.0 for 2:1")


class TimingSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    valid_from: datetime
    valid_until: datetime = Field(description="M5 rejects if clock.now() > valid_until")
    entry_window: str = Field(default="60s", description="e.g., 60s, 1m")
    timeframe: str = Field(description="Strategy timeframe — 1m, 5m")
    expiration_condition: str | None = Field(default=None)
    maximum_wait_time: str | None = None


class TradePlan(BaseContract):
    """
    Frozen, versioned, per-tenant executable plan.
    WHY frozen? Once M4.5 builds it, no one mutates entry/SL/TP — M5 validates as-is.
    WHY calculation_meta? M7 must replay: which ATR, which swing_low was used?
    """

    trade_plan_id: UUID = Field(description="Primary id — Order.client_order_id = uuid5(trade_plan_id)")
    signal_id: UUID = Field(description="Links to Signal.signal_id")
    strategy_id: str
    version: str
    instrument_id: str = Field(description="Canonical instrument — FX.EURUSD")
    broker: str
    user_id: str | UUID | None = Field(default=None, description="Tenant — per-tenant isolation")

    direction: Literal["long", "short"]

    entry: EntrySpec
    stop_loss: StopSpec = Field(description="Must exist — M5 validation requires SL")
    take_profit: TargetSpec = Field(description="Must exist — M5 validation requires TP")
    timing: TimingSpec

    data_quality: DataQuality | str = Field(default=DataQuality.pristine)
    capability_status: CapabilityStatus = Field(default=CapabilityStatus.FULLY_SUPPORTED)
    calculation_meta: dict = Field(default_factory=dict, description="{atr:0.0012, stop_model:'atr_2x'}")

    status: Literal["PENDING_RISK", "APPROVED", "REJECTED", "EXPIRED"] = "PENDING_RISK"

TradePlan.model_rebuild()
EntrySpec.model_rebuild()
StopSpec.model_rebuild()
TargetSpec.model_rebuild()
TimingSpec.model_rebuild()
