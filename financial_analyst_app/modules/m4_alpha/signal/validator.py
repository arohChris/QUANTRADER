"""WHY: M4.3 Signal Validation — the typed boundary between Strategy (M4) and
Risk (M5). A signal that is shadow, expired, slow, duplicate, or names a
non-tradable instrument must NEVER structurally reach M5 (AGENTS §3.3 fail-closed).

SCOPE (human architecture lock-in A65, 2026-09-26): validates the SIGNAL CONTRACT
ONLY — no candles, no warmup, no history-depth logic. Required historical depth is
decided upstream by the M3.1 requirement planner + quality gate.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from modules.reference import reference
from shared.contracts.signal import Signal


class SignalRejectReason(str, Enum):
    """Locked vocabulary — in-module (spec Discrepancy 2): shared DropReason is
    the M2 tick-reject enum (11 values, no stale/duplicate/shadow). Promote to
    shared/ only when a second module consumes rejections (§5 process)."""

    shadow = "shadow"
    stale = "stale"
    latency_exceeded = "latency_exceeded"
    unknown_symbol = "unknown_symbol"
    duplicate = "duplicate"
    invalid_timestamp = "invalid_timestamp"


class SignalRejection(BaseModel):
    """Frozen typed rejection — enough context for audit + dead-letter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    signal_id: UUID
    strategy_id: str
    reason: SignalRejectReason
    detail: str = ""
    rejected_at: datetime  # the caller's `now` — no hidden clock reads


class BatchResult(BaseModel):
    """Order preserved within each side (spec E13)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    validated: tuple[Signal, ...]
    rejected: tuple[SignalRejection, ...]


class SignalValidator:
    """Stateful ONLY for the dedup window (spec Discrepancy 3: one module, no
    engine). Check order pinned by spec steps 0-6 — first match wins."""

    def __init__(
        self,
        *,
        dedup_window_seconds: float = 60.0,
        latency_budget_seconds: float = 60.0,
    ) -> None:
        if dedup_window_seconds < 0:
            raise ValueError(
                f"dedup_window_seconds must be >= 0, got {dedup_window_seconds}")
        if latency_budget_seconds < 0:
            raise ValueError(
                f"latency_budget_seconds must be >= 0, got {latency_budget_seconds}")
        self._window = float(dedup_window_seconds)
        self._budget = float(latency_budget_seconds)
        # key -> record times of FULL PASSES only (rejects never recorded, E14)
        self._seen: dict[tuple[str, str, str, str], list[datetime]] = {}

    def validate(self, signal: object, *, now: datetime) -> Signal | SignalRejection:
        # step 0 — programmer error fails loud with the offending type (ADV1)
        if not isinstance(signal, Signal):
            raise TypeError(f"expected Signal, got {type(signal).__name__}")
        if now.tzinfo is None:
            raise ValueError(f"now must be timezone-aware (UTC), got {now!r}")

        def _reject(reason: SignalRejectReason, detail: str) -> SignalRejection:
            return SignalRejection(
                signal_id=signal.signal_id, strategy_id=signal.strategy_id,
                reason=reason, detail=detail, rejected_at=now)

        if signal.shadow:  # step 1 — tag beats defect (E2)
            return _reject(SignalRejectReason.shadow,
                           "shadow=True signals never forward to M5")
        # step 2 — timestamp validity BEFORE freshness (A65 pinned order)
        if signal.valid_until.tzinfo is None or signal.timestamp.tzinfo is None:
            return _reject(SignalRejectReason.invalid_timestamp,
                           "naive datetime — tz info required")
        if signal.timestamp > now:
            return _reject(SignalRejectReason.invalid_timestamp,
                           "signal.timestamp in the future")
        if now > signal.valid_until:  # step 3 — freshness, strict > (E3/E3b)
            return _reject(SignalRejectReason.stale,
                           f"now {now.isoformat()} > valid_until")
        age = (now - signal.timestamp).total_seconds()
        if age > self._budget:  # step 4 — latency, strict > (E5/E5b/E5c/HC-4)
            return _reject(SignalRejectReason.latency_exceeded,
                           f"age {age:.6f}s > budget {self._budget}s")
        if not reference.exists(signal.symbol, signal.broker):  # step 5 (E8*)
            # LAW: symbol == canonical instrument_id (A64/A65 locked)
            return _reject(SignalRejectReason.unknown_symbol,
                           f"{signal.symbol!r} not tradable on {signal.broker!r}")
        # step 6 — dedup over full passes only (E10-E12, E14); key = 4 parts (E11)
        key = (signal.strategy_id, signal.symbol, signal.direction,
               signal.user_id or "")
        hot = [t for t in self._seen.get(key, ())
               if 0.0 <= (now - t).total_seconds() <= self._window]
        if hot:  # HC-1; rejected dup does NOT refresh — hot list keeps first time
            self._seen[key] = hot
            return _reject(SignalRejectReason.duplicate,
                           f"same key within {self._window}s window")
        self._seen[key] = [now]
        return signal  # identity — never mutated (E1)

    def validate_batch(
        self, signals: list[Signal], *, now: datetime
    ) -> BatchResult:
        validated: list[Signal] = []
        rejected: list[SignalRejection] = []
        for sig in signals:
            out = self.validate(sig, now=now)
            if isinstance(out, Signal):
                validated.append(out)
            else:
                rejected.append(out)
        return BatchResult(validated=tuple(validated), rejected=tuple(rejected))
