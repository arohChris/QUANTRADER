"""M4.3 Signal Validation — public surface (spec m4.3_signal_validation)."""
from .validator import BatchResult, SignalRejection, SignalRejectReason, SignalValidator

__all__ = ["BatchResult", "SignalRejectReason", "SignalRejection", "SignalValidator"]
