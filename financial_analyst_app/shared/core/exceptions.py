"""
M1.4 — Exception Hierarchy — typed, not strings
WHY hierarchy? `except RiskRejectedError` is precise; `except Exception("margin")` is fragile.
Each has error_code from shared.contracts.errors.ErrorCode — so frontend can translate,
alert can route, and M12 can count `insufficient_margin` vs `unknown_state` separately.
"""
from __future__ import annotations

from typing import Any

from shared.contracts.errors import ErrorCode


class BaseAppError(Exception):
    """Root — all app errors inherit this. WHY? So `except BaseAppError` catches app but not system (MemoryError)."""

    error_code: ErrorCode | str = ErrorCode.config_error
    status_code: int = 500  # HTTP status for API translation

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"error_code": str(self.error_code), "message": self.message, "details": self.details}


# ---- Data errors ----
class DataGapError(BaseAppError):
    error_code = ErrorCode.gap_detected
    status_code = 502


class FutureTickError(BaseAppError):
    error_code = ErrorCode.future_timestamp
    status_code = 422


class IsolationError(BaseAppError):
    error_code = ErrorCode.isolation_violation
    status_code = 403


class PermissionDeniedError(BaseAppError):
    error_code = ErrorCode.permission_denied
    status_code = 403


# ---- Risk errors ----
class RiskRejectedError(BaseAppError):
    error_code = ErrorCode.insufficient_margin
    status_code = 422

    def __init__(self, reason: str, required: float | None = None, available: float | None = None, **details: Any):
        # WHY reason param? So caller does `raise RiskRejectedError(\"insufficient_margin\", required, available)`
        try:
            self.error_code = ErrorCode(reason)
        except ValueError:
            self.error_code = reason  # type: ignore[assignment]
        super().__init__(f"Risk rejected: {reason}", details={"required": required, "available": available, **details})


# ---- Order / Execution errors ----
class OrderValidationError(BaseAppError):
    error_code = ErrorCode.qty_step
    status_code = 422


class UnknownStateError(BaseAppError):
    error_code = ErrorCode.unknown_state
    status_code = 503


class IllegalTransitionError(BaseAppError):
    error_code = ErrorCode.illegal_transition
    status_code = 422


class CapabilityNotSupportedError(BaseAppError):
    error_code = ErrorCode.capability_not_supported
    status_code = 422


# ---- System errors ----
class ConfigError(BaseAppError):
    error_code = ErrorCode.config_error
    status_code = 500
