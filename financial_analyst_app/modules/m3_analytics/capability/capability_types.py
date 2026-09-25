"""WHY: M3.0 `CapabilityEntry` — the frozen result shape of the P15 capability gate.

In-module model (NOT shared/contract) until a second module imports it — AGENTS §5
freeze rule; `status` reuses the already-frozen `CapabilityStatus` (no bump).
Spec: docs/specs/m3.0_capability.spec.md §Outputs (LGTM ACTION 27).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from shared.contracts.market_data import CapabilityStatus


class CapabilityEntry(BaseModel):
    """One feature's capability verdict for a broker (+ optional tenant, Day-2).

    status  — authoritative verdict; fail-closed vocabulary (enum).
    reason  — why UNAVAILABLE / provenance, e.g. "Deriv has_depth:false".
    requires— data dependencies this feature needs (empty tuple = none declared).
    warning — advisory shown when usable-with-caveat, e.g. "tick_count ≠ volume".
    method  — how a DERIVED/PROXY value is produced, e.g. "tick_count_proxy".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: CapabilityStatus
    reason: str | None = None
    requires: tuple[str, ...] = ()
    warning: str | None = None
    method: str | None = None
