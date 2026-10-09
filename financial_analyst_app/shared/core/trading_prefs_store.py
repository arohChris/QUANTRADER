"""
M1.5 — trading_prefs_store — per (user, broker) strategy briefing persistence

WHY: the runner (Step C) must read ONE row that says what this user's strategies
are allowed to do (timeframe, activation anchor, auto-trade, subscriptions, global
admin gate). Laws encoded here, not at call sites:

- FAIL-CLOSED (§3.3): any DB failure -> StoreError -> caller runs NOTHING. Reads
  never invent data; a missing admin row means "default TRUE" (platform table) and
  a missing prefs/subscription row means None.
- FULL-PK SCOPE (§4.3): every read WHERE clause names the table's complete PK —
  cross-tenant/cross-broker leakage is structurally impossible, not best-effort.
- UPSERT-ONLY writes (FakePrefsDB + spec): ensure* = ON CONFLICT DO NOTHING
  (never clobber a user's change), set_*/upsert* = DO UPDATE (last write wins).
- REGISTRY-CHECKED writes: unknown strategy_id -> UnknownStrategyStoreError
  before any SQL runs (adversarial `x'; DROP TABLE...` dies at the gate).
- CONNECTION INJECTED (m2.10 pattern): no asyncpg import here — pool/DSN wiring
  belongs to Step B/C, so unit tests never load a driver. conn=None is an unwired
  store and raises StoreError, never AttributeError.
- SYSTEM-MANAGED fields: updated_at is set by this layer only; callers cannot
  backdate or forge it. params/instruments are REPLACED wholesale (store never
  silently merges dicts — merge semantics would hide user data loss).
- CONCURRENCY (documented, Top-10 #4): read-modify-write in set_timeframe means
  concurrent writers resolve by commit order (row UPSERT is the serialization
  point, §3.6); stricter locking lands with the Postgres integration layer.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from shared.contracts import StrategyAdmin, StrategySubscription, TradingPreferences
from shared.contracts.trading_preferences import TimeframeKey

_SELECT_PREFS = """
SELECT user_id, broker_id, timeframe, activation_at, auto_trade, updated_at
FROM trading_preferences
WHERE user_id = $1 AND broker_id = $2
"""

_SELECT_SUB = """
SELECT user_id, broker_id, strategy_id, user_disabled, params, instruments, updated_at
FROM strategy_subscriptions
WHERE user_id = $1 AND broker_id = $2 AND strategy_id = $3
"""

_SELECT_ADMIN = """
SELECT strategy_id, admin_approved, updated_at
FROM strategy_admin
WHERE strategy_id = $1
"""

# WHY full column list in the update arm: keeps fake (row-replace) and real PG
# (column-set) behavior identical — a partial SET would diverge under FakePrefsDB.
_UPSERT_PREFS = """
INSERT INTO trading_preferences (user_id, broker_id, timeframe, activation_at, auto_trade, updated_at)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (user_id, broker_id) DO UPDATE SET
    timeframe = EXCLUDED.timeframe,
    activation_at = EXCLUDED.activation_at,
    auto_trade = EXCLUDED.auto_trade,
    updated_at = EXCLUDED.updated_at
"""

_ENSURE_PREFS = """
INSERT INTO trading_preferences (user_id, broker_id, timeframe, activation_at, auto_trade, updated_at)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT DO NOTHING
"""

_UPSERT_SUB = """
INSERT INTO strategy_subscriptions (user_id, broker_id, strategy_id, user_disabled, params, instruments, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (user_id, broker_id, strategy_id) DO UPDATE SET
    user_disabled = EXCLUDED.user_disabled,
    params = EXCLUDED.params,
    instruments = EXCLUDED.instruments,
    updated_at = EXCLUDED.updated_at
"""

_UPSERT_ADMIN = """
INSERT INTO strategy_admin (strategy_id, admin_approved, updated_at)
VALUES ($1, $2, $3)
ON CONFLICT (strategy_id) DO UPDATE SET
    admin_approved = EXCLUDED.admin_approved,
    updated_at = EXCLUDED.updated_at
"""


class StoreError(Exception):
    """WHY typed: persistence failure must surface as no-run (fail-closed §3.3),
    never as a silent default, an AttributeError, or a half-applied write."""


class UnknownStrategyStoreError(StoreError):
    """WHY subtype of StoreError: strategy ids are registry-gated before SQL;
    a caller catching StoreError still catches this (fail-closed both ways)."""


class TradingPrefsStore:
    """Repository over the 3 M1.5 tables. Async (I/O), injected connection."""

    def __init__(self, conn: Any | None, *, known_strategies: set[str] | None = None) -> None:
        self._conn = conn
        # WHY None-able: unit tests inject no registry restriction (known=None);
        # Step B/C wiring passes registry.names() so production gates every write.
        self._known = known_strategies
        self._last_stamp = datetime(1, 1, 1, tzinfo=UTC)

    # ---------------- internals ----------------

    def _stamp(self) -> datetime:
        # WHY clamp (REV5 #9): updated_at must be STRICTLY increasing even when the
        # OS clock resolution returns the same microsecond twice; monotonic per
        # store instance (single runner — Step C). Cross-process order = commit
        # order (REV5 #4). Caller-supplied updated_at is never consulted.
        now = datetime.now(UTC)
        if now <= self._last_stamp:
            now = self._last_stamp + timedelta(microseconds=1)
        self._last_stamp = now
        return now

    def _check_strategy(self, strategy_id: str) -> None:
        if self._known is not None and strategy_id not in self._known:
            raise UnknownStrategyStoreError(f"unknown strategy {strategy_id!r} (not in registry)")

    async def _fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        conn = self._conn
        if conn is None:
            raise StoreError("TradingPrefsStore not wired: no connection")
        try:
            return list(await conn.fetch(sql, *args))
        except Exception as exc:  # WHY broad: any DB ambiguity -> StoreError -> no-run
            raise StoreError(f"read failed: {exc}") from exc

    async def _execute(self, sql: str, *args: object) -> None:
        conn = self._conn
        if conn is None:
            raise StoreError("TradingPrefsStore not wired: no connection")
        try:
            await conn.execute(sql, *args)
        except Exception as exc:
            raise StoreError(f"write failed: {exc}") from exc

    def _prefs_from_row(self, row: dict[str, Any]) -> TradingPreferences:
        return TradingPreferences(
            user_id=row["user_id"],
            broker_id=row["broker_id"],
            timeframe=row["timeframe"],
            activation_at=row["activation_at"],
            auto_trade=row["auto_trade"],
            updated_at=row["updated_at"],
        )

    async def _ensure_row(self, user_id: str, broker_id: str) -> TradingPreferences:
        # WHY: sign-in may happen before any broker exists; materialize only the
        # full PK (user+broker) — a prefs row without broker_id has no scope.
        # WHY all-6-params (no SQL literals): the col/param parity law applies to
        # every write; '1m'/FALSE travel as $3/$5 so ensure is a plain upsert too.
        now = self._stamp()
        await self._execute(_ENSURE_PREFS.strip(), user_id, broker_id, "1m", now, False, now)
        stored = await self.read_preferences(user_id, broker_id)
        if stored is None:
            # WHY: ensure wrote, read missed — invariant broken, refuse loudly.
            raise StoreError(f"ensure did not materialize row for ({user_id!r}, {broker_id!r})")
        return stored

    # ---------------- reads ----------------

    async def read_preferences(self, user_id: str, broker_id: str) -> TradingPreferences | None:
        rows = await self._fetch(_SELECT_PREFS.strip(), user_id, broker_id)
        return self._prefs_from_row(rows[0]) if rows else None

    async def read_subscription(
        self, user_id: str, broker_id: str, strategy_id: str
    ) -> StrategySubscription | None:
        rows = await self._fetch(_SELECT_SUB.strip(), user_id, broker_id, strategy_id)
        if not rows:
            return None
        row = rows[0]
        # WHY normalize: asyncpg decodes jsonb -> dict, but a plain/fake store may
        # hand back the serialized str we wrote. Callers always see a dict (contract).
        raw_params = row["params"]
        return StrategySubscription(
            user_id=row["user_id"],
            broker_id=row["broker_id"],
            strategy_id=row["strategy_id"],
            user_disabled=row["user_disabled"],
            params=json.loads(raw_params) if isinstance(raw_params, str) else raw_params,
            instruments=row["instruments"],
            updated_at=row["updated_at"],
        )

    async def admin_approved(self, strategy_id: str) -> bool:
        # WHY absent -> True: platform table defaults approved (human law); the
        # per-user execution gate (auto_trade) is what keeps it inert by default.
        rows = await self._fetch(_SELECT_ADMIN.strip(), strategy_id)
        return True if not rows else bool(rows[0]["admin_approved"])

    # ---------------- lifecycle writes ----------------

    async def ensure_at_sign_in(
        self, user_id: str, broker_id: str | None = None
    ) -> TradingPreferences | None:
        # WHY broker optional: preferences are written at sign-in, but without a
        # broker there is no (user, broker) PK to write — hold until connect.
        if broker_id is None:
            return None
        return await self._ensure_row(user_id, broker_id)

    async def ensure_at_broker_connect(self, user_id: str, broker_id: str) -> TradingPreferences:
        # WHY idempotent: reconnects re-run this; DO NOTHING never clobbers the
        # user's own changes (test_broker_connect_preserves_user_set_timeframe).
        return await self._ensure_row(user_id, broker_id)

    # ---------------- user writes ----------------

    async def set_timeframe(
        self, user_id: str, broker_id: str, timeframe: str
    ) -> TradingPreferences:
        current = await self.read_preferences(user_id, broker_id)
        now = self._stamp()
        # Contract validates timeframe (Literal 9 keys) BEFORE any SQL — an
        # unknown key raises ValidationError and never half-persists a row.
        prefs = TradingPreferences(
            user_id=user_id,
            broker_id=broker_id,
            # WHY cast: signature stays str for callers; the Literal re-check still
            # runs inside the validator, so "3d" dies here as ValidationError pre-write.
            timeframe=cast(TimeframeKey, timeframe),
            activation_at=current.activation_at if current else now,
            auto_trade=current.auto_trade if current else False,
            updated_at=now,
        )
        await self._execute(
            _UPSERT_PREFS.strip(),
            prefs.user_id,
            prefs.broker_id,
            prefs.timeframe,
            prefs.activation_at,
            prefs.auto_trade,
            prefs.updated_at,
        )
        stored = await self.read_preferences(user_id, broker_id)
        if stored is None:
            raise StoreError("set_timeframe: row vanished after upsert")
        return stored

    async def upsert_subscription(self, sub: StrategySubscription) -> StrategySubscription:
        self._check_strategy(sub.strategy_id)
        now = self._stamp()  # system-managed: caller's updated_at never trusted
        # WHY serialize HERE: asyncpg's jsonb param wants a str (no codec registered
        # anywhere — double-encode trap if a pool ever adds one); read side decodes.
        params_json = json.dumps(sub.params)
        await self._execute(
            _UPSERT_SUB.strip(),
            sub.user_id,
            sub.broker_id,
            sub.strategy_id,
            sub.user_disabled,
            params_json,
            list(sub.instruments),  # text[] wants a list (fake stores it; contract re-tuples)
            now,
        )
        stored = await self.read_subscription(sub.user_id, sub.broker_id, sub.strategy_id)
        if stored is None:
            raise StoreError("upsert_subscription: row vanished after upsert")
        return stored

    async def set_admin(self, strategy_id: str, approved: bool) -> StrategyAdmin:
        self._check_strategy(strategy_id)
        admin = StrategyAdmin(
            strategy_id=strategy_id, admin_approved=approved, updated_at=self._stamp()
        )
        await self._execute(
            _UPSERT_ADMIN.strip(), admin.strategy_id, admin.admin_approved, admin.updated_at
        )
        rows = await self._fetch(_SELECT_ADMIN.strip(), strategy_id)
        if not rows:
            raise StoreError("set_admin: row vanished after upsert")
        row = rows[0]
        return StrategyAdmin(
            strategy_id=row["strategy_id"],
            admin_approved=row["admin_approved"],
            updated_at=row["updated_at"],
        )
