"""
M1.4 — Tenant Isolation Tests — MUST pass for v0.1-foundation gate
Airplane Test: no DB, no broker — pure ContextVar + in-memory mock repo

Tests:
  - test_cross_tenant_query_blocked — query with no context → IsolationError
  - test_broker_isolation — user 42/deriv cannot read user 42/mt5 rows
  - test_user_isolation — user 42 cannot read user 99 rows
  - test_vault_isolation — vault per-tenant
  - test_inject_where_clause — helper auto-injects WHERE
"""
from __future__ import annotations

import pytest

from shared.core.broker_vault import create_vault
from shared.core.tenant_context import TenantContext, tenant_context
from shared.core.exceptions import IsolationError


# Mock repo — in-memory dict keyed by (user_id, broker, symbol)
class MockPositionRepo:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str, str], dict] = {}

    def insert(self, symbol: str, qty: float) -> None:
        uid = TenantContext.require_user_id()
        bid = TenantContext.require_broker_id()
        self._store[(uid, bid, symbol)] = {"symbol": symbol, "qty": qty, "user_id": uid, "broker": bid}

    def get(self, symbol: str) -> dict | None:
        uid = TenantContext.require_user_id()
        bid = TenantContext.require_broker_id()
        return self._store.get((uid, bid, symbol))

    def query_all(self) -> list[dict]:
        uid = TenantContext.require_user_id()
        bid = TenantContext.require_broker_id()
        return [v for (u, b, _), v in self._store.items() if u == uid and b == bid]


def test_cross_tenant_query_blocked() -> None:
    tenant_context.clear()
    repo = MockPositionRepo()
    with pytest.raises(IsolationError, match="No tenant"):
        repo.insert("EURUSD", 1.0)
    with pytest.raises(IsolationError):
        repo.get("EURUSD")


def test_broker_isolation() -> None:
    repo = MockPositionRepo()
    tenant_context.set("42", "deriv")
    repo.insert("EURUSD", 0.7)
    assert repo.get("EURUSD")["qty"] == 0.7

    # Switch broker — same user, different broker → no leak
    tenant_context.set("42", "mt5")
    assert repo.get("EURUSD") is None
    assert repo.query_all() == []

    # Switch back — still there
    tenant_context.set("42", "deriv")
    assert repo.get("EURUSD")["qty"] == 0.7
    tenant_context.clear()


def test_user_isolation() -> None:
    repo = MockPositionRepo()
    tenant_context.set("42", "deriv")
    repo.insert("EURUSD", 1.0)
    tenant_context.set("99", "deriv")
    assert repo.get("EURUSD") is None
    tenant_context.clear()


def test_vault_isolation() -> None:
    vault = create_vault(key="test-key-42-not-for-prod-1234567890")
    vault.clear_all()
    vault.store("42", "deriv", "deriv_token_42")
    vault.store("42", "mt5", "mt5_token_42")
    vault.store("99", "deriv", "deriv_token_99")

    assert vault.get("42", "deriv") == "deriv_token_42"
    assert vault.get("42", "mt5") == "mt5_token_42"
    assert vault.get("99", "deriv") == "deriv_token_99"
    # Cross-check fails
    assert vault.get("42", "deriv") != vault.get("99", "deriv")
    with pytest.raises(Exception):
        vault.get("99", "mt5")  # never stored
    vault.clear_all()


def test_inject_where_clause() -> None:
    tenant_context.set("42", "deriv")
    sql, params = TenantContext.inject_where_clause("SELECT * FROM positions", {})
    assert "WHERE" in sql
    assert params["user_id"] == "42"
    assert params["broker_id"] == "deriv"

    sql2, params2 = TenantContext.inject_where_clause("SELECT * FROM orders WHERE status='open'", {})
    assert "AND user_id" in sql2
    tenant_context.clear()


def test_context_is_per_task() -> None:
    """
    WHY this test? Proves ContextVar not global — two concurrent tasks don't leak.
    Simulated with sequential sets — real concurrency tested with anyio in integration.
    """
    import asyncio

    async def task(user: str, broker: str, symbol: str, qty: float) -> float | None:
        tenant_context.set(user, broker)
        repo = MockPositionRepo()
        # Use shared repo — but context isolates
        await asyncio.sleep(0.01)
        # Check that context still correct after sleep (not overwritten by other task)
        assert TenantContext.get_user_id() == user
        assert TenantContext.get_broker_id() == broker
        return qty

    async def run() -> None:
        # anyio would be better, but asyncio.gather proves ContextVar isolation (Python 3.11+)
        r1, r2 = await asyncio.gather(task("42", "deriv", "EURUSD", 0.7), task("99", "mt5", "GBPUSD", 1.0))
        assert r1 == 0.7
        assert r2 == 1.0

    asyncio.run(run())
    tenant_context.clear()
