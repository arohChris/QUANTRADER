"""
M2.1 IngestionManager — async start/stop lifecycle for the platform market-data WS.
WHY: Validates instruments BEFORE connect (no socket for UNKNOWN.FOO); never calls vault;
never keys by user_id. Heartbeat 20s / timeout 35s → degraded → reconnect full-jitter 1s→30s.
Frames → broker_feeds.deriv.route_frame → on_tick/on_drop (pure TickIngestor is the parse path;
routing only classifies msg_type here — full ingest callback is injected by the app).
Spec: docs/specs/m2.1_connection.spec.md REV1.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable

from modules.m2_market_data.broker_feeds.deriv.stream import build_tick_subscribe, route_frame
from modules.m2_market_data.connection import ConnectionManager

logger = logging.getLogger("m2.1.ingestion")

__all__ = ["IngestionManager"]


class _RunState:
    __slots__ = ("symbols", "tasks", "app_id")

    def __init__(self, symbols: list[str], app_id: str) -> None:
        self.symbols = list(symbols)
        self.app_id = app_id
        self.tasks: set[asyncio.Task[Any]] = set()


class IngestionManager:
    """Per-broker starter/stopper. Async I/O only; no vault; no user_id."""

    def __init__(
        self,
        *,
        connect_factory: Callable[[], Any],
        instrument_exists: Callable[[str, str], bool],
        app_id: str = "",
        broker_config: dict[str, dict[str, Any]] | None = None,
        on_tick: Callable[[str], Any] | None = None,
        on_drop: Callable[[Any], Any] | None = None,
        heartbeat_interval_s: float = 20.0,
        heartbeat_timeout_s: float = 35.0,
        backoff_base_s: float = 1.0,
        backoff_cap_s: float = 30.0,
        clock: Any | None = None,
    ) -> None:
        self.connection_manager = ConnectionManager(
            connect_factory,
            backoff_base_s=backoff_base_s,
            backoff_cap_s=backoff_cap_s,
        )
        self._connect_factory = connect_factory
        self._instrument_exists = instrument_exists
        self._app_id = app_id
        self._broker_config: dict[str, dict[str, Any]] = broker_config or {}
        self._on_tick = on_tick
        self._on_drop = on_drop
        self._hb_interval = heartbeat_interval_s
        self._hb_timeout = heartbeat_timeout_s
        self._clock = clock
        self._running: dict[str, _RunState] = {}
        self._start_lock = asyncio.Lock()

    def is_running(self, broker: str) -> bool:
        return broker in self._running

    async def start(self, broker: str, symbols: list[str]) -> None:
        """Idempotent start. Validates broker/app_id/instruments BEFORE any connect."""
        async with self._start_lock:
            if broker in self._running:
                logger.info("ingestion.already_running", extra={"broker": broker})
                return

            # Pre-WS: every instrument must exist — fail loud, connect not called.
            # Checked first so unknown-instrument tests fail on the instrument,
            # not on a missing default broker_config.
            for iid in symbols:
                if not self._instrument_exists(iid, broker):
                    raise ValueError(f"unknown instrument: {iid!r} (reference.exists false)")

            if broker not in self._broker_config:
                raise ValueError(f"unknown broker: {broker!r} (no config)")

            cfg = self._broker_config[broker]
            resolved_app = str(cfg.get("app_id") or self._app_id or "")
            if not resolved_app:
                raise ValueError(f"missing app_id for broker {broker!r}")

            handle = await self.connection_manager.get_or_create(broker)
            handle.state = "connected"
            handle.last_inbound_at = _now()

            # Subscribe each symbol (empty list → no frames; still connected).
            for native_or_canonical in symbols:
                # Symbols passed here are broker-native for subscribe (tests use FX.EURUSD
                # as opaque strings with FakeWS — production maps via reference before start).
                frame = build_tick_subscribe(native_or_canonical, resolved_app)
                await handle.ws.send(frame)

            state = _RunState(symbols, resolved_app)
            self._running[broker] = state

            recv_task = asyncio.create_task(self._recv_loop(broker), name=f"m21-recv-{broker}")
            hb_task = asyncio.create_task(self._heartbeat_loop(broker), name=f"m21-hb-{broker}")
            state.tasks.update((recv_task, hb_task))
            logger.info(
                "ingestion.connected",
                extra={"broker": broker, "singleton": True, "symbols": len(symbols)},
            )

    async def stop(self, broker: str) -> None:
        """Cancel loops + close socket. Idempotent."""
        async with self._start_lock:
            state = self._running.pop(broker, None)

        if state is None:
            # Still close any orphan handle (double-stop safety).
            await self.connection_manager.close(broker)
            return

        for task in list(state.tasks):
            task.cancel()
        if state.tasks:
            await asyncio.gather(*state.tasks, return_exceptions=True)
        state.tasks.clear()

        await self.connection_manager.close(broker)
        logger.info("ingestion.stopped", extra={"broker": broker})

    async def stop_all(self) -> None:
        for broker in list(self._running.keys()):
            await self.stop(broker)

    # --- internals ---

    async def _recv_loop(self, broker: str) -> None:
        state = self._running.get(broker)
        if state is None:
            return
        while broker in self._running:
            handle = self.connection_manager.pool.get(broker)
            if handle is None:
                await asyncio.sleep(0.01)
                continue
            try:
                raw = await handle.ws.recv()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # ConnectionError, WS close, etc.
                logger.warning("ingestion.recv_error", extra={"broker": broker, "error": str(exc)})
                await self.connection_manager.note_failure(broker)
                handle.state = "reconnecting"
                await self._reconnect(broker, state, handle)
                continue

            handle.last_inbound_at = _now()
            if handle.state in ("connecting", "degraded", "reconnecting"):
                handle.state = "connected"

            def _on_tick(frame: str, _broker: str = broker) -> None:
                if self._on_tick is not None:
                    self._on_tick(frame)

            def _on_drop(drop: Any, _broker: str = broker) -> None:
                if self._on_drop is not None:
                    self._on_drop(drop)

            # route_frame never raises on hostile input (spec adversarial).
            route_frame(raw, on_tick=_on_tick, on_drop=_on_drop)

    async def _heartbeat_loop(self, broker: str) -> None:
        state = self._running.get(broker)
        if state is None:
            return
        while broker in self._running:
            await asyncio.sleep(self._hb_interval)
            handle = self.connection_manager.pool.get(broker)
            if handle is None:
                continue
            last = handle.last_inbound_at
            if last is None:
                continue
            age = (_now() - last).total_seconds()
            if age > self._hb_timeout:
                logger.warning(
                    "ingestion.heartbeat_timeout",
                    extra={"broker": broker, "age_s": age},
                )
                handle.state = "degraded"
                try:
                    await handle.ws.close()
                except Exception:
                    pass
                handle.state = "reconnecting"
                await self._reconnect(broker, state, handle)

    async def _reconnect(self, broker: str, state: _RunState, handle: Any) -> None:
        """Full-jitter backoff loop until stop() or success. Re-subscribes all symbols."""
        attempt = handle.consecutive_failures
        while broker in self._running:
            delay = self.connection_manager.backoff_delay(attempt)
            await asyncio.sleep(delay)
            if broker not in self._running:
                return
            try:
                ws = self._connect_factory()
            except Exception as exc:
                logger.warning("ingestion.reconnect_factory_fail", extra={"error": str(exc)})
                await self.connection_manager.note_failure(broker)
                attempt = handle.consecutive_failures
                continue

            await self.connection_manager.replace_socket(broker, ws, state="connected")
            handle = self.connection_manager.pool[broker]
            handle.last_inbound_at = _now()
            # Success path: reset failures after first successful replace + resubscribe.
            try:
                for sym in state.symbols:
                    await ws.send(build_tick_subscribe(sym, state.app_id))
            except Exception as exc:
                logger.warning("ingestion.resubscribe_fail", extra={"error": str(exc)})
                await self.connection_manager.note_failure(broker)
                handle.state = "reconnecting"
                attempt = handle.consecutive_failures
                continue

            handle.consecutive_failures = 0
            handle.state = "connected"
            logger.info(
                "ingestion.reconnected",
                extra={"broker": broker, "attempt": attempt},
            )
            return


def _now() -> datetime:
    return datetime.now(timezone.utc)
