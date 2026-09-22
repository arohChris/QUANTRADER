"""
M1.1 — contracts package — re-exports all frozen contracts
WHY re-export? `from shared.contracts import MarketData, Signal` — one import path.
"""
from .analytics import AnalyticsResult
from .base import BaseContract, Explainability, Provenance
from .errors import (
    ErrorCode,
    ReferenceError,
    ReferenceValidationError,
    UnknownBrokerError,
    UnknownInstrumentError,
)
from .drop import Drop, DropReason
from .execution import ExecutionResult, Fill
from .instrument import AssetClass
from .internal_tick import InternalTick
from .market_data import (
    Candle,
    CapabilityStatus,
    DataQuality,
    MarketData,
    OrderBookSnapshot,
    Tick,
    TradePrint,
)
from .order import Order, OrderIntent
from .portfolio import PortfolioState, Position
from .reconciliation import Diff, ReconciliationReport
from .risk import RiskDecision
from .signal import Signal
from .trade_plan import EntrySpec, StopSpec, TargetSpec, TimingSpec, TradePlan

__all__ = [
    "AnalyticsResult",
    "AssetClass",
    "BaseContract",
    "Candle",
    "CapabilityStatus",
    "DataQuality",
    "Diff",
    "Drop",
    "DropReason",
    "EntrySpec",
    "ErrorCode",
    "ExecutionResult",
    "Explainability",
    "Fill",
    "InternalTick",
    "MarketData",
    "Order",
    "OrderBookSnapshot",
    "OrderIntent",
    "PortfolioState",
    "Position",
    "Provenance",
    "ReferenceError",
    "ReferenceValidationError",
    "ReconciliationReport",
    "RiskDecision",
    "Signal",
    "StopSpec",
    "TargetSpec",
    "Tick",
    "TimingSpec",
    "TradePrint",
    "TradePlan",
    "UnknownBrokerError",
    "UnknownInstrumentError",
]

# Rebuild Pydantic forward refs — WHY? BaseContract uses Provenance before it's defined,
# and all contracts inherit BaseContract. Without rebuild, "not fully defined" error.
# This makes `from shared.contracts import MarketData` just work.
try:
    from .base import BaseContract as _BC

    _BC.model_rebuild()
    for _cls in [
        MarketData,
        AnalyticsResult,
        Signal,
        TradePlan,
        PortfolioState,
        RiskDecision,
        Order,
        ExecutionResult,
        ReconciliationReport,
    ]:
        _cls.model_rebuild()
except Exception:
    pass  # tests will surface real error

# Version of the contracts package — bump on breaking change
# 1.1.0: additive AssetClass enum (M2b.1 commodity scope) — minor bump per AGENTS.md §5
# 1.2.0: additive typed ReferenceError hierarchy (UnknownBroker/UnknownInstrument/Validation) — minor, M2b.1 review
# 1.3.0: additive Drop / DropReason (M2.1 reject vocabulary) — minor, no existing consumer yet
# 2.0.0: MAJOR — AssetClass narrowed to forex|crypto|commodity only (index/equity/synthetic removed)
#        per human directive 2026-09-22; breaking for any code that used removed members.
# 2.1.0: minor — InternalTick 1.1 (Deriv field map: optional sizes/quote/pip_size); re-export InternalTick.
__version__ = "2.1.0"
