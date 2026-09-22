"""
M1.2 — Lifecycle + Health Tests — startup order, health aggregation
"""
from __future__ import annotations

import pytest

from shared.core.engine_interface import BaseEngine, HealthStatus
from shared.core.lifecycle import Lifecycle


class HealthyEngine(BaseEngine):
    name = "healthy_engine"

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    async def health(self) -> HealthStatus:
        return HealthStatus(engine=self.name, status="healthy", latency_ms=5.0)


class DegradedEngine(BaseEngine):
    name = "degraded_engine"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health(self) -> HealthStatus:
        return HealthStatus(engine=self.name, status="degraded", details={"ws_connected": False})


class FailingEngine(BaseEngine):
    name = "failing_engine"

    async def start(self) -> None:
        raise RuntimeError("start failed — missing vault key")

    async def stop(self) -> None:
        pass

    async def health(self) -> HealthStatus:
        return HealthStatus(engine=self.name, status="unhealthy", details={"error": "start failed"})


@pytest.mark.asyncio
async def test_lifecycle_startup_order() -> None:
    order: list[str] = []

    class OrderedEngine(BaseEngine):
        def __init__(self, n: str):
            self.name = n

        async def start(self) -> None:
            order.append(self.name)

        async def stop(self) -> None:
            pass

        async def health(self) -> HealthStatus:
            return HealthStatus(engine=self.name, status="healthy")

    e1 = OrderedEngine("m1")
    e2 = OrderedEngine("m2")
    e3 = OrderedEngine("m3")
    lc = Lifecycle([e1, e2, e3])
    await lc.startup()
    assert order == ["m1", "m2", "m3"]


@pytest.mark.asyncio
async def test_lifecycle_shutdown_reverse() -> None:
    order: list[str] = []

    class OrderedEngine(BaseEngine):
        def __init__(self, n: str):
            self.name = n

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            order.append(self.name)

        async def health(self) -> HealthStatus:
            return HealthStatus(engine=self.name, status="healthy")

    e1 = OrderedEngine("m1")
    e2 = OrderedEngine("m2")
    lc = Lifecycle([e1, e2])
    await lc.shutdown()
    assert order == ["m2", "m1"]  # reverse


@pytest.mark.asyncio
async def test_lifecycle_health_aggregation() -> None:
    lc = Lifecycle([HealthyEngine(), DegradedEngine()])
    health = await lc.health()
    assert health["status"] == "degraded"  # worst
    assert health["modules"]["healthy_engine"]["status"] == "healthy"
    assert health["modules"]["degraded_engine"]["status"] == "degraded"


@pytest.mark.asyncio
async def test_lifecycle_health_unhealthy_wins() -> None:
    lc = Lifecycle([HealthyEngine(), FailingEngine()])
    health = await lc.health()
    assert health["status"] == "unhealthy"


@pytest.mark.asyncio
async def test_lifecycle_startup_continues_on_failure() -> None:
    # WHY test? One engine failing to start must not hide — others still start, health shows UNHEALTHY
    started = []

    class GoodEngine(BaseEngine):
        name = "good"

        async def start(self) -> None:
            started.append("good")

        async def stop(self) -> None:
            pass

        async def health(self) -> HealthStatus:
            return HealthStatus(engine=self.name, status="healthy")

    lc = Lifecycle([FailingEngine(), GoodEngine()])
    await lc.startup()  # should not raise, but log
    assert "good" in started
