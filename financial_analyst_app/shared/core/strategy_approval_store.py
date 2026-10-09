"""
M4.8 — strategy_approval_store — persisted approval lifecycle per (strategy_id, version)

WHY: role-dependent visibility (REV1) and fail-closed runnability (REV2) need ONE
authoritative, PERSISTED state per strategy@version — a memory-only flag would silently
"approve" across a restart (AGENTS.md §3.9). REV3 (human 2026-10-07) REMOVED the former
"exactly one testing slot" law: several strategies may hold 'testing' at once, and the
runner's admin+paper confinement is what keeps refinement safe.

Laws encoded here, not at call sites (spec docs/specs/m4.8_strategy_approval.spec.md):
- FAIL-CLOSED (§3.3): any DB failure -> ApprovalStoreError (never a guessed default
  state); the runner gate treats an unreadable store as a NO. Absent row = implicit
  'draft' — missing approval is never approval.
- VALIDATION BEFORE SQL: the contract validates new_state (exact Literal), changed_by
  (non-empty after strip) and note (<=200) FIRST, then the registry gate rejects
  unknown/adversarial strategy_ids — zero statements are issued for either rejection
  (`x'; DROP TABLE ...` dies at the gate, parameterized reads stay no-ops).
- CONNECTION INJECTED (m2.10/m1.5 pattern): no asyncpg import here — pool/DSN wiring
  belongs to the composition root; conn=None is an unwired store and raises
  ApprovalStoreError, never AttributeError.
- SYSTEM-STAMPED changed_at with monotonic clamp (M1.5 law): callers have no changed_at
  parameter to forge/backdate; stamps are strictly increasing per store instance
  (max(now, last+1µs)) so two flips never share a timestamp.
- SINGLE-STATEMENT upsert = no partial writes, then a read-back proves persistence;
  REV3 (human 2026-10-07: "we should be able to test multiple strategies at a time")
  REMOVED the strategy_approvals_one_testing unique index and its
  TestingSlotOccupiedError translation — several strategies may hold 'testing' at once;
  admin+paper confinement (runner REV2 table) remains the control.
- LIST READS are state-scoped (audience law, REV1): trader = state 'approved' only;
  admin = union over every legal state, i.e. all explicit rows (implicit drafts surface
  via registry iteration through state(), spec Correct #4 — they have no row to list).
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from pydantic import TypeAdapter

from shared.contracts import StrategyApproval, StrategyState
from shared.core.logging import get_logger

logger = get_logger(__name__)

# WHY TypeAdapter and not a plain `if`: exact-Literal validation (REV1 vocabulary) with
# a typed pydantic error — "user"/"root"/None/1 must raise ValidationError, never fall
# through to a permissive default.
_AUDIENCE = TypeAdapter(Literal["trader", "admin"])

# The five CHECK-constrained states — also the admin list_visible iteration order.
_STATES: tuple[str, ...] = ("draft", "testing", "approved", "rejected", "discontinued")

# WHY a fixed namespace: BaseContract's correlation_id/trace_id default to random
# uuid4 and timestamp to now() ON EVERY CONSTRUCTION — a DB round-trip would then
# never compare equal, but `get() == set_state(...)` / `get() == pre` IS the
# round-trip law the spec tests assert. Row identity must therefore be a pure
# function of the row's own content: same 6 columns -> same reconstructed instance.
_ROW_NS = uuid5(UUID("00000000-0000-0000-0000-000000000000"),
                "shared.contracts.strategy_approval/row")

# Transition matrix (spec State machine section; same-state handled as idempotent
# no-op before this table is consulted). Single source of truth for legality.
_LEGAL: dict[str, frozenset[str]] = {
    "draft": frozenset({"testing", "rejected", "discontinued"}),
    "testing": frozenset({"draft", "approved", "rejected"}),
    "approved": frozenset({"testing", "rejected", "discontinued"}),
    "rejected": frozenset({"testing"}),
    "discontinued": frozenset({"testing", "approved"}),
}

_SELECT_ROW = """
SELECT strategy_id, version, state, changed_by, changed_at, note
FROM strategy_approvals
WHERE strategy_id = $1 AND version = $2
"""

_SELECT_STATE = """
SELECT state FROM strategy_approvals WHERE strategy_id = $1 AND version = $2
"""

# WHY state-scoped for BOTH audiences (fake + spec audience law): every list read is
# `WHERE state = $1` — trader asks once for 'approved', admin unions all five states
# (only explicit rows exist to find; the DB CHECK guarantees the union is complete).
_SELECT_BY_STATE = """
SELECT strategy_id, version, state, changed_by, changed_at, note
FROM strategy_approvals
WHERE state = $1
ORDER BY strategy_id, version
"""

# WHY full column list in both arms: keeps fake (row-replace) and real PG (column-set)
# behavior identical — a partial SET would diverge under FakeApprovalsDB.
_UPSERT = """
INSERT INTO strategy_approvals (strategy_id, version, state, changed_by, changed_at, note)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (strategy_id, version) DO UPDATE SET
    state = EXCLUDED.state,
    changed_by = EXCLUDED.changed_by,
    changed_at = EXCLUDED.changed_at,
    note = EXCLUDED.note
"""

# One-time legacy seed: never clobbers a later admin flip (idempotent re-run inserts 0).
_SEED = """
INSERT INTO strategy_approvals (strategy_id, version, state, changed_by, changed_at, note)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (strategy_id, version) DO NOTHING
"""


class ApprovalStoreError(Exception):
    """WHY typed: persistence failure must surface as fail-closed no-run (§3.3), never
    as a silent default state, an AttributeError, or a half-applied write."""


class UnknownStrategyApprovalError(ApprovalStoreError):
    """WHY subtype: strategy ids are registry-gated before SQL; a caller catching
    ApprovalStoreError still catches this (fail-closed both ways)."""


class ApprovalTransitionError(ApprovalStoreError):
    """Illegal matrix move; carries from/to so the audit line reconstructs the refusal."""

    def __init__(self, strategy_id: str, version: str, from_state: str, to_state: str) -> None:
        self.strategy_id = strategy_id
        self.version = version
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"illegal transition {from_state!r} -> {to_state!r} for {strategy_id}@{version}")


class StrategyApprovalStore:
    """Repository over the strategy_approvals table. Async (I/O), injected connection."""

    def __init__(self, conn: Any | None, *, known_strategies: set[str] | None = None) -> None:
        self._conn = conn
        # WHY None-able: tests inject no registry restriction (known=None); production
        # wiring passes registry.names() so every write is gated (M1.5 pattern).
        self._known = known_strategies
        self._last_stamp = datetime(1, 1, 1, tzinfo=UTC)

    # ---------------- internals ----------------

    def _stamp(self) -> datetime:
        # WHY clamp: changed_at must be STRICTLY increasing per store instance even when
        # the OS clock returns the same microsecond twice (spec Correct #6). Caller
        # stamps do not exist — this layer is the only clock.
        now = datetime.now(UTC)
        if now <= self._last_stamp:
            now = self._last_stamp + timedelta(microseconds=1)
        self._last_stamp = now
        return now

    def _check_strategy(self, strategy_id: str) -> None:
        if self._known is not None and strategy_id not in self._known:
            raise UnknownStrategyApprovalError(
                f"unknown strategy {strategy_id!r} (not in registry)")

    async def _fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        conn = self._conn
        if conn is None:
            raise ApprovalStoreError("StrategyApprovalStore not wired: no connection")
        try:
            return list(await conn.fetch(sql, *args))
        except Exception as exc:  # WHY broad: any DB ambiguity -> typed error -> no-run
            raise ApprovalStoreError(f"read failed: {exc}") from exc

    async def _execute(self, sql: str, *args: object) -> str:
        conn = self._conn
        if conn is None:
            raise ApprovalStoreError("StrategyApprovalStore not wired: no connection")
        try:
            # WHY the status string: seed counts "INSERT 0 1" vs "INSERT 0 0" so a
            # re-run honestly reports 0 instead of claiming rows it did not write.
            return str(await conn.execute(sql, *args))
        except Exception as exc:
            raise ApprovalStoreError(f"write failed: {exc}") from exc

    def _from_row(self, row: dict[str, Any]) -> StrategyApproval:
        # WHY derived auto-fields (see _ROW_NS): identity = f(row content), so two
        # reads of the same row reconstruct EQUAL instances (pydantic __eq__ includes
        # correlation_id/trace_id/timestamp — random defaults would break every
        # round-trip assertion). timestamp uses the row's only instant: changed_at.
        content = (
            f"{row['strategy_id']}|{row['version']}|{row['state']}|"
            f"{row['changed_by']}|{row['changed_at'].isoformat()}|{row['note']}"
        )
        return StrategyApproval(
            strategy_id=row["strategy_id"],
            version=row["version"],
            state=cast(StrategyState, row["state"]),
            changed_by=row["changed_by"],
            changed_at=row["changed_at"],
            note=row["note"],
            correlation_id=uuid5(_ROW_NS, content),
            trace_id=uuid5(_ROW_NS, f"trace|{content}"),
            timestamp=row["changed_at"],
        )

    def _implicit_draft(self, strategy_id: str, version: str) -> StrategyApproval:
        # WHY a synthetic row instead of None: set_state must return the contract type
        # for the idempotent (draft, draft) no-op, but NOTHING may materialize — the
        # sentinel changed_by/changed_at mark it display-only; get() stays None. This
        # resolves the BUILD_STATUS open question logged at Step 4. Auto-fields are
        # derived (not random) for the same round-trip-equality reason as _from_row.
        content = f"implicit|{strategy_id}|{version}"
        return StrategyApproval(
            strategy_id=strategy_id,
            version=version,
            state="draft",
            changed_by="system:implicit-draft",
            changed_at=datetime(1970, 1, 1, tzinfo=UTC),
            note=None,
            correlation_id=uuid5(_ROW_NS, content),
            trace_id=uuid5(_ROW_NS, f"trace|{content}"),
            timestamp=datetime(1970, 1, 1, tzinfo=UTC),
        )

    # ---------------- reads ----------------

    async def state(self, strategy_id: str, version: str) -> StrategyState:
        rows = await self._fetch(_SELECT_STATE.strip(), strategy_id, version)
        # WHY explicit ["state"] key, not fetchval: correct against BOTH real asyncpg
        # (only the state column comes back) and the fake (full-row dicts) — a
        # positional/first-value read would return strategy_id under the fake.
        return cast(StrategyState, rows[0]["state"]) if rows else "draft"

    async def get(self, strategy_id: str, version: str) -> StrategyApproval | None:
        rows = await self._fetch(_SELECT_ROW.strip(), strategy_id, version)
        return self._from_row(rows[0]) if rows else None

    async def list_visible(self, audience: str) -> tuple[StrategyApproval, ...]:
        role = _AUDIENCE.validate_python(audience)  # exact Literal (REV1); fails loud
        if role == "trader":
            rows = await self._fetch(_SELECT_BY_STATE.strip(), "approved")
            return tuple(self._from_row(r) for r in rows)
        listed: list[StrategyApproval] = []
        for state in _STATES:
            # Admin sees every explicit row; union over the CHECK-constrained state set
            # is complete by construction. Implicit drafts have no row (Correct #4).
            rows = await self._fetch(_SELECT_BY_STATE.strip(), state)
            listed.extend(self._from_row(r) for r in rows)
        return tuple(listed)

    # ---------------- lifecycle write ----------------

    async def set_state(
        self,
        strategy_id: str,
        version: str,
        new_state: StrategyState,
        *,
        changed_by: str,
        note: str | None = None,
    ) -> StrategyApproval:
        # 1. Contract validates state Literal / changed_by / note BEFORE any SQL — an
        #    unknown state or empty handle dies here as ValidationError, zero statements.
        validated = StrategyApproval(
            strategy_id=strategy_id,
            version=version,
            # WHY cast: the public signature stays StrategyState-typed; pydantic still
            # re-checks the Literal, so None/int/"approved " die as ValidationError.
            state=cast(StrategyState, new_state),
            changed_by=changed_by,
            changed_at=datetime.now(UTC),  # provisional — written stamps come from _stamp
            note=note,
        )
        # 2. Registry gate before SQL (adversarial ids rejected, db.calls stays empty).
        self._check_strategy(strategy_id)
        # 3. Read current row; absent = implicit draft (fail-closed).
        rows = await self._fetch(_SELECT_ROW.strip(), strategy_id, version)
        current = self._from_row(rows[0]) if rows else None
        from_state: StrategyState = current.state if current else "draft"
        # 4. Same-state = idempotent no-op: return the row unchanged, write NOTHING
        #    (changed_by is NOT re-recorded — a no-op is not an audit event).
        if validated.state == from_state:
            if current is not None:
                return current
            return self._implicit_draft(strategy_id, version)
        # 5. Matrix gate before any write; error carries from/to (spec Edges).
        if validated.state not in _LEGAL[from_state]:
            raise ApprovalTransitionError(
                strategy_id, version, from_state, validated.state)
        # 6. System-stamped legal flip — single statement, then prove persistence.
        #    REV3: 'testing' is an ordinary flip (no unique index, no slot translation).
        stamp = self._stamp()
        row = StrategyApproval(
            strategy_id=strategy_id,
            version=version,
            state=validated.state,
            changed_by=validated.changed_by,
            changed_at=stamp,
            note=validated.note,
        )
        await self._execute(
            _UPSERT.strip(),
            row.strategy_id, row.version, row.state,
            row.changed_by, row.changed_at, row.note,
        )
        logger.info(
            "strategy_approval_flip",
            **{"strategy_id": strategy_id, "version": version, "from": from_state,
               "to": row.state, "changed_by": row.changed_by, "note": row.note},
        )
        stored = await self._fetch(_SELECT_ROW.strip(), strategy_id, version)
        if not stored:
            raise ApprovalStoreError(
                f"set_state: row vanished after upsert ({strategy_id}@{version})")
        return self._from_row(stored[0])

    # ---------------- one-time legacy seed ----------------

    async def seed_legacy_approved(
        self, entries: Iterable[tuple[str, str]], *, changed_by: str
    ) -> int:
        """INSERT ... state='approved' ... ON CONFLICT DO NOTHING; returns rows inserted.
        WHY validate ALL entries before ANY SQL: a mid-list rejection must not leave a
        half-seeded table (contract probe + registry gate first, writes second)."""
        pairs = [(str(sid), str(ver)) for sid, ver in entries]
        if not pairs:
            return 0
        stamp = self._stamp()
        rows: list[StrategyApproval] = []
        for sid, ver in pairs:
            row = StrategyApproval(  # contract probe: changed_by/state validated here
                strategy_id=sid, version=ver, state="approved",
                changed_by=changed_by, changed_at=stamp, note=None)
            self._check_strategy(sid)  # registry gate — still before any SQL
            rows.append(row)
        inserted = 0
        for row in rows:
            status = await self._execute(
                _SEED.strip(),
                row.strategy_id, row.version, row.state,
                row.changed_by, row.changed_at, row.note,
            )
            if status.strip() == "INSERT 0 1":
                inserted += 1
        return inserted
