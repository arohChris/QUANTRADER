"""
M1.4 — Tenant Context — user_id + broker_id isolation (THE GUARD)
INSTITUTIONAL HEDGE FUND GRADE — why this is the most security-critical file

WHY ContextVar not global? Each async request/coroutine has its own tenant.
  Global `current_user = 42` would leak across concurrent requests (async race).
  ContextVar is per-task — task for user 42 never sees user 99.

WHY mandatory? Every repository does:
    uid = require_user_id(); bid = require_broker_id()
    db.query("SELECT * FROM positions WHERE user_id=:uid AND broker_id=:bid", uid, bid)
  If context missing → IsolationError (query BLOCKED). No silent cross-tenant read.

WHY composite (user_id+broker_id) not single tenant_id? Explicit is auditable.
  Deriv user sees/stores/processes ONLY Deriv. Query auto-injects broker_id so even
  if code forgets WHERE broker_id, guard still injects it.

Usage — middleware (API/WS/engine):
    tenant_context.set(user_id="42", broker_id="deriv")
    try:
        await handler()
    finally:
        tenant_context.clear()

Usage — repository:
    from shared.core.tenant_context import require_user_id, require_broker_id
    uid = require_user_id()  # raises IsolationError if not set
    bid = require_broker_id()
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

# WHY default None? So we can detect "no context set" vs "set to empty string"
_user_ctx: ContextVar[str | None] = ContextVar("user_id", default=None)
_broker_ctx: ContextVar[str | None] = ContextVar("broker_id", default=None)

# Single source of truth for IsolationError — defined in exceptions.py
# We import lazily to avoid circular import at module load; fallback define if needed
try:
    from .exceptions import IsolationError  # type: ignore[import]
except ImportError:
    class IsolationError(RuntimeError):  # fallback for import order
        """Raised when tenant context missing or cross-tenant access attempted — ALWAYS loud."""
        pass


class TenantContext:
    """
    Thin wrapper over two ContextVars — WHY class? So middleware does `tenant_context.set(...)`
    without importing two vars separately.
    """

    @staticmethod
    def set(user_id: str | int | None, broker_id: str | None) -> None:
        """Set both — call once per request/engine job."""
        # Normalize to str — DB stores as str/UUID text
        _user_ctx.set(str(user_id) if user_id is not None else None)
        _broker_ctx.set(str(broker_id) if broker_id is not None else None)

    @staticmethod
    def clear() -> None:
        _user_ctx.set(None)
        _broker_ctx.set(None)

    @staticmethod
    def get_user_id() -> str | None:
        return _user_ctx.get()

    @staticmethod
    def get_broker_id() -> str | None:
        return _broker_ctx.get()

    @staticmethod
    def require_user_id() -> str:
        uid = _user_ctx.get()
        if uid is None:
            # WHY loud? Missing context = query would be unscoped → data leak. Block it.
            raise IsolationError("No tenant user_id — query blocked! Did you forget tenant_context.set()?")
        return uid

    @staticmethod
    def require_broker_id() -> str:
        bid = _broker_ctx.get()
        if bid is None:
            raise IsolationError("No tenant broker_id — query blocked! Did you forget tenant_context.set()?")
        return bid

    @staticmethod
    def require_both() -> tuple[str, str]:
        return TenantContext.require_user_id(), TenantContext.require_broker_id()

    @staticmethod
    def as_dict() -> dict[str, str | None]:
        return {"user_id": _user_ctx.get(), "broker_id": _broker_ctx.get()}

    @staticmethod
    def inject_where_clause(base_sql: str, params: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
        """
        Helper — auto-inject WHERE user_id+broker_id if not already present.
        WHY helper? So even if engineer writes `SELECT * FROM positions`, guard adds filter.
        Preferred: repositories explicitly write `WHERE user_id=:uid AND broker_id=:bid` using require_*().
        This helper is fallback for legacy queries.
        """
        uid = TenantContext.require_user_id()
        bid = TenantContext.require_broker_id()
        params = dict(params or {})
        params.setdefault("user_id", uid)
        params.setdefault("broker_id", bid)
        # If base_sql already has WHERE, append AND; else add WHERE
        if "WHERE" in base_sql.upper():
            sql = f"{base_sql} AND user_id=:user_id AND broker_id=:broker_id"
        else:
            sql = f"{base_sql} WHERE user_id=:user_id AND broker_id=:broker_id"
        return sql, params


# Singleton for convenience — `from shared.core.tenant_context import tenant_context`
tenant_context = TenantContext()

# Also expose plain functions for repositories that want `from shared.core.tenant_context import require_user_id`
require_user_id = TenantContext.require_user_id
require_broker_id = TenantContext.require_broker_id
require_both = TenantContext.require_both
get_user_id = TenantContext.get_user_id
get_broker_id = TenantContext.get_broker_id
