"""
M1.1 — contracts package — re-exports all frozen contracts
WHY re-export? `from shared.contracts import MarketData, Signal` — one import path.
"""
from .analytics import AnalyticsResult
from .base import BaseContract, Explainability, Provenance
from .drop import Drop, DropReason
from .errors import (
    ErrorCode,
    ReferenceError,
    ReferenceValidationError,
    UnknownBrokerError,
    UnknownInstrumentError,
)
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
from .strategy_admin import StrategyAdmin
from .strategy_approval import StrategyApproval, StrategyState
from .strategy_subscription import StrategySubscription
from .trade_plan import EntrySpec, StopSpec, TargetSpec, TimingSpec, TradePlan
from .trading_preferences import TradingPreferences

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
    "ReconciliationReport",
    "ReferenceError",
    "ReferenceValidationError",
    "RiskDecision",
    "Signal",
    "StopSpec",
    "StrategyAdmin",
    "StrategyApproval",
    "StrategyState",
    "StrategySubscription",
    "TargetSpec",
    "Tick",
    "TimingSpec",
    "TradePlan",
    "TradePrint",
    "TradingPreferences",
    "UnknownBrokerError",
    "UnknownInstrumentError",
]

# Rebuild Pydantic forward refs — WHY? BaseContract uses Provenance before it's defined,
# and all contracts inherit BaseContract. Without rebuild, "not fully defined" error.
# This makes `from shared.contracts import MarketData` just work.
#
# WHY the outer `try: ... except Exception: pass` was REMOVED (not just narrowed):
# it wrapped the BaseContract import too. If that import failed, the bare except
# swallowed it and the package imported "successfully" with a broken base class —
# the worst possible failure mode, because every downstream error would be a lie.
# A failed BaseContract import is already a loud ImportError on its own.
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
    try:
        _cls.model_rebuild()
    except Exception as exc:
        # WHY this was `except: pass` and why that was wrong: model_rebuild() fails on
        # an unresolvable forward reference. The bare except swallowed it AND abandoned
        # the rest of the loop, so every contract AFTER the failing one stayed
        # un-rebuilt with no error anywhere — surfacing much later as a baffling
        # validation error inside M5/M6, far from the cause. The old comment claimed
        # "tests will surface real error"; they surface a SYMPTOM, not the cause.
        #
        # The v2 contract bump (MoneyDecimal / AwareUtcDatetime Annotated types) is
        # precisely the change most likely to break a forward reference, so this is
        # now a loud, named failure instead of a silent one.
        raise RuntimeError(
            f"contract {_cls.__name__} failed model_rebuild() — the contract graph has an "
            f"unresolvable forward reference. Original error: {exc!r}"
        ) from exc

# Version of the contracts package — bump on breaking change
# 1.1.0: additive AssetClass enum (M2b.1 commodity scope) — minor bump per AGENTS.md §5
# 1.2.0: additive typed ReferenceError hierarchy (UnknownBroker/UnknownInstrument/Validation) — minor, M2b.1 review
# 1.3.0: additive Drop / DropReason (M2.1 reject vocabulary) — minor, no existing consumer yet
# 2.0.0: MAJOR — AssetClass narrowed to forex|crypto|commodity only (index/equity/synthetic removed)
#        per human directive 2026-09-22; breaking for any code that used removed members.
# 2.1.0: minor — InternalTick 1.1 (Deriv field map: optional sizes/quote/pip_size); re-export InternalTick.
# 2.2.0: minor — additive M1.5 briefing contracts (StrategyAdmin, TradingPreferences,
#        StrategySubscription) — new consumers only, no existing field changed (AGENTS.md §5).
# 2.3.0: minor — additive M4.8 approval-lifecycle contract (StrategyApproval,
#        StrategyState) — new consumers only (M4 runner gate, M10.2 admin API);
#        no existing contract field changed (AGENTS.md §5).
__version__ = "2.3.0"
