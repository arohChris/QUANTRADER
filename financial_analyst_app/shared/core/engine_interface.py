"""
M1.2 — BaseEngine — The Interface Every Module Implements
INSTITUTIONAL HEDGE FUND GRADE — async lifecycle, well-commented WHY

WHY async def start/stop/health?
  M2 Data opens WS (I/O), M6 Execution opens broker connections (I/O) — blocking start
  would stall the whole process. Async lets lifecycle start 12 modules concurrently
  with asyncio.gather without blocking ingestion.

WHY ABC? Enforces every module has health() — M12 Ops can loop `for m in modules: await m.health()`
         without knowing which module.

WHY not sync def for health? Health may query DB/Redis/external (I/O) — must be async.
    Pure calc engines (M3 indicators) still implement async health but it's instant (<1ms).
"""
from __future__ import annotations

import abc
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class HealthStatus(BaseModel):
    """Frozen health snapshot — returned by every engine."""

    model_config = {"frozen": True}

    engine: str = Field(description="Module name — e.g., 'data', 'analytics', 'alpha'")
    status: Literal["healthy", "degraded", "unhealthy", "offline"] = Field(
        description="healthy=OK, degraded=partial (e.g., one broker down), unhealthy=needs intervention"
    )
    latency_ms: float | None = Field(default=None, description="Last operation latency — for p95 budgets")
    details: dict = Field(default_factory=dict, description="Free-form: {ws_connected: true, lag_ms: 12}")
    version: str = Field(default="1.0.0")


class EngineState(str, Enum):
    """Lifecycle state — WHY: prevents double-start, tracks shutdown gracefully."""

    initializing = "initializing"
    healthy = "healthy"
    degraded = "degraded"
    unhealthy = "unhealthy"
    shutting_down = "shutting_down"
    offline = "offline"


class BaseEngine(abc.ABC):
    """
    Every Module implements this. M1 lifecycle loops them without knowing concrete type.

    Example:
        class DataEngine(BaseEngine):
            name = "data"
            async def start(self): await self.connection_manager.start()
            async def stop(self): await self.connection_manager.drain()
            async def health(self): return HealthStatus(engine=self.name, status="healthy")
    """

    # WHY class var not instance? So lifecycle can log name before instantiation.
    name: str = "base"

    @abc.abstractmethod
    async def start(self) -> None:
        """
        WHY async? Setup connections, per-tenant vault, warm cache — all I/O.
        Called in order: M1.1 → M1.2 → ... → M7. Never blocks ingestion.
        """
        ...

    @abc.abstractmethod
    async def stop(self) -> None:
        """
        WHY async? Graceful drain: finish in-flight ticks, close WS, flush parquet.
        Called reverse: M7 → ... → M1. No lost ticks.
        """
        ...

    @abc.abstractmethod
    async def health(self) -> HealthStatus:
        """
        WHY async? May check DB + Redis + external. Never block caller.
        Returns HEALTHY/DEGRADED/OFFLINE — aggregated by M12 /health endpoint.
        """
        ...
