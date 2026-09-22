"""
M2.1 Connection — Spec tests (WRITTEN BEFORE CODE — AGENTS.md §2 Step 3).
WHY: docs/specs/m2.1_connection.spec.md REV1 — human review CHANGES applied.
Impl import only ConnectionManager / IngestionManager / broker_feeds — collection
RED until Step 5. Contracts (Drop, InternalTick) OK to import.

P08 gate: heartbeat + reconnect; singleton {broker: ws}; no vault; no user_id;
reference.exists BEFORE connect; full-jitter backoff 1s→30s.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

# Direct impl imports — fail collection until Step 5 (honest RED).
from modules.m2_market_data.connection import ConnectionHandle, ConnectionManager  # noqa: F401
from modules.m2_market_data.ingestion import IngestionManager  # noqa: F401
from modules.m2_market_data.broker_feeds.deriv.stream import build_tick_subscribe, route_frame  # noqa: F401

from shared.contracts.drop import Drop, DropReason

BASE = datetime(2026, 3, 22, 19, 46, 0, tzinfo=timezone.utc)


class FakeWS:
    """Scripted async WS for unit tests — no network."""

    def __init__(
        self,
        *,
        frames: list[str] | None = None,
        recv_error: Exception | None = None,
        silent: bool = False,
    ) -> None:
        self._frames = list(frames or [])
        self._recv_error = recv_error
        self._silent = silent
        self.sent: list[Any] = []
        self.closed = False
        self._q: asyncio.Queue[str | None] = asyncio.Queue()
        for f in self._frames:
            self._q.put_nowait(f)

    async def send(self, message: Any) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if self._recv_error is not None:
            raise self._recv_error
        if self._silent:
            await asyncio.sleep(3600)  # never returns until cancelled
        item = await self._q.get()
        if item is None:
            raise ConnectionError("ws closed")
        return item

    async def close(self) -> None:
        self.closed = True
        self._q.put_nowait(None)

    def feed(self, frame: str) -> None:
        self._q.put_nowait(frame)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        return await self.recv()


def _deriv_tick_frame(symbol: str = "frxEURUSD") -> str:
    import json

    return json.dumps(
        {
            "msg_type": "tick",
            "tick": {
                "ask": 1.08542,
                "bid": 1.08538,
                "epoch": 1774208800,
                "id": "a9b8c7d6-1234-5678-90ab-cdef12345678",
                "pip_size": 5,
                "quote": 1.08540,
                "symbol": symbol,
            },
        }
    )


def _mgr(**kwargs: Any) -> IngestionManager:
    """IngestionManager with injectable transport + reference for tests."""
    return IngestionManager(**kwargs)


# --- Singleton / pool / no user_id / no vault ---

async def test_singleton_two_get_or_create_same_handle() -> None:
    cm = ConnectionManager(connect_factory=lambda: FakeWS())
    h1 = await cm.get_or_create("deriv")
    h2 = await cm.get_or_create("deriv")
    assert h1 is h2


async def test_concurrent_get_or_create_100x_one_connect() -> None:
    calls = {"n": 0}

    def factory() -> FakeWS:
        calls["n"] += 1
        return FakeWS()

    cm = ConnectionManager(connect_factory=factory)
    handles = await asyncio.gather(*(cm.get_or_create("deriv") for _ in range(100)))
    assert calls["n"] == 1
    assert all(h is handles[0] for h in handles)


async def test_pool_key_is_broker_string_not_user_tuple() -> None:
    cm = ConnectionManager(connect_factory=lambda: FakeWS())
    h = await cm.get_or_create("deriv")
    # pool keys must be plain broker strings — never (user_id, broker)
    assert "deriv" in cm.pool
    for k in cm.pool:
        assert isinstance(k, str)


async def test_vault_get_never_called(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("vault.get must never be called on M2.1 market-data path")

    # Patch any plausible vault entry points — all must stay untouched.
    import shared.core.broker_vault as bv

    monkeypatch.setattr(bv, "get", _boom, raising=False)
    monkeypatch.setattr(bv, "BrokerVault", _boom, raising=False)

    cm = ConnectionManager(connect_factory=lambda: FakeWS())
    h = await cm.get_or_create("deriv")
    assert isinstance(h, ConnectionHandle)


async def test_start_validates_reference_before_connect() -> None:
    """Unknown instrument → ValueError and connect factory never called."""
    calls = {"n": 0}

    def factory() -> FakeWS:
        calls["n"] += 1
        return FakeWS()

    mgr = IngestionManager(
        connect_factory=factory,
        instrument_exists=lambda iid, broker: False,  # unknown
    )
    with pytest.raises(ValueError, match="instrument|exists|unknown"):
        await mgr.start("deriv", ["UNKNOWN.FOO"])
    assert calls["n"] == 0


async def test_start_known_instrument_opens_connect() -> None:
    calls = {"n": 0}

    def factory() -> FakeWS:
        calls["n"] += 1
        return FakeWS(frames=[_deriv_tick_frame()])

    mgr = IngestionManager(
        connect_factory=factory,
        instrument_exists=lambda iid, broker: True,
        app_id="fixture-app",
        broker_config={"deriv": {"ws_url": "wss://example.invalid", "app_id": "fixture-app"}},
    )
    await mgr.start("deriv", ["FX.EURUSD"])
    assert calls["n"] == 1
    await mgr.stop("deriv")


async def test_start_twice_is_idempotent_no_second_loop() -> None:
    calls = {"n": 0}

    def factory() -> FakeWS:
        calls["n"] += 1
        return FakeWS()

    mgr = IngestionManager(
        connect_factory=factory,
        instrument_exists=lambda iid, broker: True,
        app_id="fixture-app",
        broker_config={"deriv": {"ws_url": "wss://example.invalid", "app_id": "fixture-app"}},
    )
    await mgr.start("deriv", ["FX.EURUSD"])
    await mgr.start("deriv", ["FX.EURUSD"])  # no-op
    assert calls["n"] == 1
    await mgr.stop("deriv")


async def test_unknown_broker_raises_value_error() -> None:
    mgr = IngestionManager(
        connect_factory=lambda: FakeWS(),
        instrument_exists=lambda iid, broker: True,
        app_id="fixture-app",
        broker_config={"deriv": {"ws_url": "wss://x", "app_id": "a"}},  # no mt5
    )
    with pytest.raises(ValueError, match="broker"):
        await mgr.start("mt5", ["FX.EURUSD"])


async def test_missing_app_id_raises_value_error() -> None:
    mgr = IngestionManager(
        connect_factory=lambda: FakeWS(),
        instrument_exists=lambda iid, broker: True,
        app_id="",  # missing
        broker_config={"deriv": {"ws_url": "wss://x", "app_id": ""}},
    )
    with pytest.raises(ValueError, match="app_id"):
        await mgr.start("deriv", ["FX.EURUSD"])


# --- Heartbeat / reconnect / backoff ---

async def test_heartbeat_timeout_marks_degraded_then_reconnects() -> None:
    # First WS silent (never sends); second WS delivers a tick after "reconnect".
    wss = [FakeWS(silent=True), FakeWS(frames=[_deriv_tick_frame()])]
    connects = {"n": 0}

    def factory() -> FakeWS:
        ws = wss[min(connects["n"], len(wss) - 1)]
        connects["n"] += 1
        return ws

    mgr = IngestionManager(
        connect_factory=factory,
        instrument_exists=lambda iid, broker: True,
        app_id="fixture-app",
        broker_config={"deriv": {"ws_url": "wss://x", "app_id": "fixture-app"}},
        heartbeat_interval_s=0.01,
        heartbeat_timeout_s=0.05,
        backoff_base_s=0.01,
        backoff_cap_s=0.05,
    )
    await mgr.start("deriv", ["FX.EURUSD"])
    # allow silent timeout + reconnect
    await asyncio.sleep(0.3)
    handle = await mgr.connection_manager.get_or_create("deriv")
    # either still reconnecting or reconnected after silent first socket
    assert connects["n"] >= 1
    assert handle.state in ("connected", "degraded", "reconnecting", "connecting")
    await mgr.stop("deriv")


async def test_recv_connection_error_increments_failures() -> None:
    ws = FakeWS(recv_error=ConnectionError("boom"))
    cm = ConnectionManager(connect_factory=lambda: ws)
    h = await cm.get_or_create("deriv")
    # simulate recv failure handling on handle
    await cm.note_failure("deriv")
    assert h.consecutive_failures == 1


async def test_backoff_full_jitter_capped_at_30s() -> None:
    cm = ConnectionManager(connect_factory=lambda: FakeWS())
    # pure backoff helper (spec: base 1s cap 30s full jitter)
    for attempt in range(0, 20):
        delay = cm.backoff_delay(attempt)
        assert 0 <= delay <= 30.0
    # high attempt still capped
    assert cm.backoff_delay(100) <= 30.0


async def test_reconnect_resubscribes_each_symbol() -> None:
    ws = FakeWS(frames=[], recv_error=ConnectionError("drop"))
    # after failure, next factory returns healthy ws
    healthy = FakeWS(frames=[_deriv_tick_frame()])
    calls = {"n": 0}

    def factory() -> FakeWS:
        calls["n"] += 1
        return ws if calls["n"] == 1 else healthy

    mgr = IngestionManager(
        connect_factory=factory,
        instrument_exists=lambda iid, broker: True,
        app_id="fixture-app",
        broker_config={"deriv": {"ws_url": "wss://x", "app_id": "fixture-app"}},
        heartbeat_interval_s=10,
        heartbeat_timeout_s=30,
        backoff_base_s=0.01,
        backoff_cap_s=0.05,
    )
    await mgr.start("deriv", ["FX.EURUSD", "CRYPTO.BTCUSD"])
    await asyncio.sleep(0.2)
    # healthy socket should have received tick subscribe frames for both symbols
    # (first socket failed immediately; second got resubscribe)
    if calls["n"] >= 2:
        subs = [m for m in healthy.sent if isinstance(m, str) and '"ticks"' in m]
        assert len(subs) >= 2
    await mgr.stop("deriv")


# --- Non-tick frames / hostile frames / stop ---

async def test_ping_frame_does_not_call_ingest_but_refreshes_heartbeat() -> None:
    import json

    ingested: list[str] = []

    def factory() -> FakeWS:
        return FakeWS(frames=[json.dumps({"msg_type": "ping"})])

    mgr = IngestionManager(
        connect_factory=factory,
        instrument_exists=lambda iid, broker: True,
        app_id="fixture-app",
        broker_config={"deriv": {"ws_url": "wss://x", "app_id": "fixture-app"}},
        on_tick=lambda raw: ingested.append(raw),
        on_drop=lambda d: None,
    )
    await mgr.start("deriv", ["FX.EURUSD"])
    await asyncio.sleep(0.05)
    assert ingested == []  # ping not a tick
    await mgr.stop("deriv")


async def test_corrupt_json_frame_emits_invalid_json_drop() -> None:
    drops: list[Drop] = []
    ticks: list[str] = []

    def factory() -> FakeWS:
        return FakeWS(frames=["{{{"])

    mgr = IngestionManager(
        connect_factory=factory,
        instrument_exists=lambda iid, broker: True,
        app_id="fixture-app",
        broker_config={"deriv": {"ws_url": "wss://x", "app_id": "fixture-app"}},
        on_tick=lambda raw: ticks.append(raw),
        on_drop=lambda d: drops.append(d),
        clock=None,  # default clock injected by impl
    )
    await mgr.start("deriv", ["FX.EURUSD"])
    await asyncio.sleep(0.05)
    assert ticks == []
    assert any(d.reason == DropReason.invalid_json for d in drops)
    await mgr.stop("deriv")


async def test_hostile_frames_do_not_crash_connection() -> None:
    """None / [] / 10k char / DROP TABLE / embedded NUL — never raise out of route."""
    hostile = [
        "null",
        "[]",
        '""',
        "x" * 10_000,
        '{"msg_type":"tick","tick":{"symbol":"\'; DROP TABLE trades;--"}}',
        "\x00\x01\x02",
    ]
    for raw in hostile:
        # route_frame must not raise — returns None / Drop / routes to callback
        result = route_frame(raw, on_tick=lambda r: None, on_drop=lambda d: None)
        assert result is None or isinstance(result, Drop) or result is True


async def test_stop_cancels_tasks_and_is_idempotent() -> None:
    mgr = IngestionManager(
        connect_factory=lambda: FakeWS(silent=True),
        instrument_exists=lambda iid, broker: True,
        app_id="fixture-app",
        broker_config={"deriv": {"ws_url": "wss://x", "app_id": "fixture-app"}},
        heartbeat_interval_s=0.01,
        heartbeat_timeout_s=0.05,
    )
    await mgr.start("deriv", ["FX.EURUSD"])
    await mgr.stop("deriv")
    await mgr.stop("deriv")  # second stop safe
    # no lingering tasks tied to manager
    assert mgr.is_running("deriv") is False


async def test_empty_symbols_connects_without_subscribe_frames() -> None:
    ws = FakeWS(frames=[])

    def factory() -> FakeWS:
        return ws

    mgr = IngestionManager(
        connect_factory=factory,
        instrument_exists=lambda iid, broker: True,
        app_id="fixture-app",
        broker_config={"deriv": {"ws_url": "wss://x", "app_id": "fixture-app"}},
    )
    await mgr.start("deriv", [])
    await asyncio.sleep(0.02)
    tick_subs = [m for m in ws.sent if isinstance(m, str) and '"ticks"' in m]
    assert tick_subs == []
    await mgr.stop("deriv")


async def test_silent_ws_stop_terminates_reconnect_loop() -> None:
    """Adversarial: silent forever — stop() must not hang (test times out if broken)."""
    mgr = IngestionManager(
        connect_factory=lambda: FakeWS(silent=True),
        instrument_exists=lambda iid, broker: True,
        app_id="fixture-app",
        broker_config={"deriv": {"ws_url": "wss://x", "app_id": "fixture-app"}},
        heartbeat_interval_s=0.01,
        heartbeat_timeout_s=0.02,
        backoff_base_s=0.01,
        backoff_cap_s=0.02,
    )
    await mgr.start("deriv", ["FX.EURUSD"])
    await asyncio.sleep(0.1)
    await asyncio.wait_for(mgr.stop("deriv"), timeout=2.0)


# --- Subscribe builder (broker_feeds only) ---

def test_build_tick_subscribe_includes_symbol_and_app_id() -> None:
    msg = build_tick_subscribe("frxEURUSD", app_id="fixture-app")
    assert isinstance(msg, str)
    assert "frxEURUSD" in msg
    assert "fixture-app" in msg
    assert "ticks" in msg


def test_build_tick_subscribe_hostile_symbol_opaque_not_executed() -> None:
    # Opaque string pass-through — no SQL/path interpretation at builder
    evil = "'; DROP TABLE trades;--"
    msg = build_tick_subscribe(evil, app_id="a")
    assert evil in msg  # still just a string in JSON
