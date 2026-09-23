"""
M2.1 Connection — per-broker singleton WS pool (async, no vault, no user_id).
WHY: Market-data is PUBLIC — one Deriv socket serves all tenants (AGENTS §4.3).
Pool key is str broker only; asyncio.Lock makes concurrent get_or_create one connect.
Backoff: full jitter, base 1s → cap 30s (spec m2.1_connection REV1).
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Any, Awaitable, Callable

__all__ = ["ConnectionHandle", "ConnectionManager"]

BackoffFactory = Callable[[int], float]


class ConnectionHandle:
    """One live (or reconnecting) connection for a single broker."""

    def __init__(self, broker: str, ws: Any, state: str = "connecting") -> None:
        self.broker = broker
        self.ws = ws
        self.state = state  # connecting|connected|degraded|reconnecting|closed
        self.consecutive_failures = 0
        self.last_heartbeat_at: datetime | None = None
        self.last_inbound_at: datetime | None = None

    def __repr__(self) -> str:
        return f"ConnectionHandle(broker={self.broker!r}, state={self.state!r})"


class ConnectionManager:
    """Singleton pool {broker: ConnectionHandle} — never keyed by user_id."""

    def __init__(
        self,
        connect_factory: Callable[[], Any],
        *,
        backoff_base_s: float = 1.0,
        backoff_cap_s: float = 30.0,
        rng: random.Random | None = None,
    ) -> None:
        self.pool: dict[str, ConnectionHandle] = {}
        self._lock = asyncio.Lock()
        self._connect_factory = connect_factory
        self._backoff_base_s = backoff_base_s
        self._backoff_cap_s = backoff_cap_s
        # Injectable for deterministic tests; default system RNG.
        self._rng = rng or random.Random()

    async def get_or_create(self, broker: str) -> ConnectionHandle:
        """One connect per broker under concurrency (N=100 → 1 factory call)."""
        async with self._lock:
            existing = self.pool.get(broker)
            if existing is not None and existing.state != "closed":
                return existing
            ws = self._connect_factory()
            handle = ConnectionHandle(broker, ws, state="connected")
            handle.last_inbound_at = None
            self.pool[broker] = handle
            return handle

    async def note_failure(self, broker: str) -> None:
        handle = self.pool.get(broker)
        if handle is not None:
            handle.consecutive_failures += 1

    def backoff_delay(self, attempt: int) -> float:
        """Full jitter: U(0, min(cap, base * 2**attempt)). Spec: 1s → 30s cap."""
        upper = min(self._backoff_cap_s, self._backoff_base_s * (2 ** max(0, attempt)))
        if upper <= 0:
            return 0.0
        return self._rng.uniform(0.0, upper)

    async def replace_socket(self, broker: str, ws: Any, state: str = "connected") -> ConnectionHandle:
        """Swap socket after reconnect (caller holds or has already coordinated)."""
        async with self._lock:
            handle = self.pool.get(broker)
            if handle is None:
                handle = ConnectionHandle(broker, ws, state=state)
                self.pool[broker] = handle
            else:
                handle.ws = ws
                handle.state = state
            return handle

    async def close(self, broker: str) -> None:
        async with self._lock:
            handle = self.pool.pop(broker, None)
        if handle is None:
            return
        handle.state = "closed"
        ws = handle.ws
        close = getattr(ws, "close", None)
        if close is not None:
            result = close()
            if asyncio.iscoroutine(result):
                await result

    async def close_all(self) -> None:
        for broker in list(self.pool.keys()):
            await self.close(broker)
