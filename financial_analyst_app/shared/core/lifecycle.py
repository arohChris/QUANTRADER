"""
M1.2 — Lifecycle — The Conductor (startup/shutdown order)
INSTITUTIONAL HEDGE FUND GRADE — async, ordered, never half-boot half-dead

WHY ordered? M6 Execution must never start before M2 Data has a clock.
WHY reverse shutdown? Execution stops before Data so no new ticks arrive after execution closed.
WHY try/except per module? One module failing to start must not silently hide — mark UNHEALTHY + alert.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from typing import Sequence

from .engine_interface import BaseEngine, HealthStatus

logger = logging.getLogger(__name__)


class Lifecycle:
    """
    Orchestrates start/stop/health for all modules.
    WHY class not just functions? Holds ordered list + state for SIGHUP reload.
    """

    def __init__(self, engines: Sequence[BaseEngine]):
        # WHY ordered list not dict? Order matters: Foundation → Data → Analytics → ...
        self.engines: list[BaseEngine] = list(engines)
        self._running = False

    async def startup(self) -> None:
        """Start in dependency order. If one fails, mark unhealthy but continue others for observability."""
        logger.info("lifecycle.startup begin", extra={"engines": [e.name for e in self.engines]})
        for eng in self.engines:
            try:
                # WHY await? Each engine may open WS/DB — I/O bound
                await eng.start()
                logger.info("engine.started", extra={"engine": eng.name})
            except Exception as exc:  # noqa: BLE001 — must catch all to mark health
                # WHY not reraise? We want /health to show which engine failed, not a silent crash
                logger.exception("engine.start_failed", extra={"engine": eng.name, "error": str(exc)})
                # Health will report UNHEALTHY — M12 alerts will fire
        self._running = True
        logger.info("lifecycle.startup complete")

    async def shutdown(self) -> None:
        """Stop in reverse order — graceful drain, no lost ticks."""
        logger.info("lifecycle.shutdown begin")
        self._running = False
        for eng in reversed(self.engines):
            try:
                await eng.stop()
                logger.info("engine.stopped", extra={"engine": eng.name})
            except Exception as exc:  # noqa: BLE001
                logger.exception("engine.stop_failed", extra={"engine": eng.name, "error": str(exc)})
        logger.info("lifecycle.shutdown complete")

    async def health(self) -> dict:
        """
        WHY gather not sequential? 12 health checks in parallel < 10ms total vs 12*5ms sequential.
        Aggregate: if any DEGRADED → overall DEGRADED; if any UNHEALTHY → UNHEALTHY.
        """
        # WHY return_exceptions=True? One engine's health timing out must not hide others
        results: list[HealthStatus | BaseException] = await asyncio.gather(
            *(eng.health() for eng in self.engines), return_exceptions=True
        )
        modules: dict[str, dict] = {}
        worst = "healthy"
        for eng, res in zip(self.engines, results, strict=True):
            if isinstance(res, BaseException):
                modules[eng.name] = {"status": "unhealthy", "error": str(res)}
                worst = "unhealthy"
            else:
                modules[eng.name] = res.model_dump()
                if res.status == "unhealthy" and worst != "unhealthy":
                    worst = "unhealthy"
                elif res.status == "degraded" and worst == "healthy":
                    worst = "degraded"
                elif res.status == "offline" and worst == "healthy":
                    worst = "degraded"
        return {"status": worst, "modules": modules}

    def install_signal_handlers(self) -> None:
        """WHY SIGTERM? K8s sends SIGTERM on pod shutdown — must drain, not kill -9."""

        def _handle(sig: int, _frame: object) -> None:  # noqa: ARG001
            logger.info("signal received", extra={"signal": sig})
            # Schedule shutdown on loop — don't block signal handler
            asyncio.create_task(self.shutdown())

        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)
