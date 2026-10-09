"""
M4.8 - StrategyApproval + StrategyState - persisted approval lifecycle, one row per
strategy@version

WHY: role-dependent visibility (REV1: admin sees every state, trader sees `approved`
only) and fail-closed runnability (REV2: `draft` = not fully built runs for nobody;
`testing` = refinement, admin paper-only) need ONE authoritative, typed state per
(strategy_id, version) that survives restarts (DB row - AGENTS.md 3.9, never
memory-only).

WHY a new contract instead of M1.5's `StrategyAdmin` boolean: five states x two roles
x two environments cannot be honestly encoded in one bool, and the absent-row
semantics differ on purpose - here an absent row is an implicit `draft`
(fail-closed, never approval); `StrategyAdmin`'s absent row stays default-TRUE
(Discrepancy 2 in the m4.8 spec, accepted at LGTM 2026-10-03).
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import AwareUtcDatetime, BaseContract

# WHY Literal and not a str Enum: the DB CHECK constraint stores these exact strings;
# an Enum would be a second vocabulary that can silently drift from the SQL.
StrategyState = Literal["draft", "testing", "approved", "rejected", "discontinued"]


class StrategyApproval(BaseContract):
    strategy_id: str  # registered strategy name (registry-gated on every write)
    # WHY overrides BaseContract.version (same precedent as Signal.version): this is
    # the STRATEGY version (parameter_registry "1.0"/"2.1"), required, PK part - a
    # bump is a NEW key whose missing row is an implicit 'draft': re-approval
    # required (fail-closed). No default on purpose: a row without a version is
    # nonsense, never "1.0".
    version: str = Field(description="strategy version - e.g. 1.0 (parameter_registry)")
    state: StrategyState  # lifecycle state; absent DB row == implicit 'draft'
    # WHY constraints here (spec: "validated via contract before SQL"): an empty
    # changed_by or a 201-char note must fail as pydantic ValidationError at the
    # contract boundary, never reach SQL (m4.8 Edges: writes & validation).
    changed_by: str = Field(min_length=1)  # admin handle, or 'seed:m4.8-init' for the
    #   one-time legacy seed; whitespace-only collapses to "" via strip -> rejected
    changed_at: AwareUtcDatetime  # UTC, SYSTEM-stamped (monotonic clamp) - a
    #   caller-supplied stamp is ignored by the store, never trusted (M1.5 law)
    note: str | None = Field(default=None, max_length=200)  # why the flip happened


# Consumers: M4 (runner gate), M10.2 (admin API + strategy list), M1.5 Step C (future)
StrategyApproval.model_rebuild()
