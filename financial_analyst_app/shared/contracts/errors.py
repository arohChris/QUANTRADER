"""
M1.1 — ErrorCode — Single source of rejection reasons (not free text)
WHY enum not string? Frontend can translate, alert can route, tests can assert.

M2b.1 typed reference errors (additive 2026-09-22):
WHY separate Exception classes? Spec requires typed errors (not bare Exception);
tests assert exact type, callers catch narrowly. Not Pydantic contracts — plain exceptions.
"""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    # Risk rejections
    insufficient_margin = "insufficient_margin"
    exposure_limit = "exposure_limit"
    leverage_limit = "leverage_limit"
    concentration_limit = "concentration_limit"
    spread_too_wide = "spread_too_wide"
    liquidity_proxy = "liquidity_proxy"
    data_quality_invalid = "data_quality_invalid"
    trade_plan_expired = "trade_plan_expired"
    missing_sl_tp = "missing_sl_tp"
    rr_too_low = "rr_too_low"
    # Signal
    stale_signal = "stale_signal"
    stale_alpha = "stale_alpha"
    duplicate_signal = "duplicate_signal"
    unknown_symbol = "unknown_symbol"
    capability_not_supported = "capability_not_supported"
    # Order / Execution
    qty_step = "qty_step"
    insufficient_qty = "insufficient_qty"
    broker_unavailable = "broker_unavailable"
    unknown_state = "unknown_state"
    duplicate_order = "duplicate_order"
    illegal_transition = "illegal_transition"
    # Data
    future_timestamp = "future_timestamp"
    negative_price = "negative_price"
    inverted_quote = "inverted_quote"
    gap_detected = "gap_detected"
    # System
    isolation_violation = "isolation_violation"
    permission_denied = "permission_denied"
    config_error = "config_error"


# --- M2b.1 typed reference errors (spec: UnknownBrokerError / UnknownInstrumentError / ValidationError-style) ---

class ReferenceError(Exception):
    """Base for M2b.1 identity/lookup errors — catch this to handle any reference failure."""


class UnknownBrokerError(ReferenceError):
    """Raised when broker is not one of deriv | mt5 | headway (or empty)."""

    def __init__(self, broker: str) -> None:
        self.broker = broker
        super().__init__(f"unknown broker {broker!r} — allowed: deriv, mt5, headway")


class UnknownInstrumentError(ReferenceError):
    """Raised when instrument_id is not in the instrument master."""

    def __init__(self, instrument_id: str) -> None:
        self.instrument_id = instrument_id
        super().__init__(f"unknown instrument_id {instrument_id!r}")


class ReferenceValidationError(ReferenceError):
    """ValidationError-style typed error for empty/invalid identity inputs (native/broker)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
