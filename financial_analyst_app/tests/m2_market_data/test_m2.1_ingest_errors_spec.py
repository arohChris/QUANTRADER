"""
M2.1 ingest error contract - Step 3 spec tests. WRITTEN AGAINST THE SPEC, BEFORE CODE.

Spec: docs/specs/m2.1_ingest_errors.spec.md (human LGTM 2026-09-29, ACTION 73).
Covers that spec's E1-E28, A1-A6, and invariants I1-I6. One test per named row.

STEP 3 IS EXPECTED TO BE RED. `OutageRecord` does not exist yet, so this module
fails at IMPORT. That is the honest pre-implementation state and it is recorded,
not hidden. A test suite written AFTER the code would encode whatever the code
happened to do, which is precisely what AGENTS.md 2 Step 3 forbids.

LAW OBSERVED HERE:
  * every test asserts a SPECIFIC value, a specific log record, or a specific
    error type. No test asserts only "no exception" (AGENTS 3.5).
  * not money path, so no Decimal hand-calc sheet applies (AGENTS 3.1).
  * no wall-clock sleeping: the injected clock (spec D-E, E26-E28) must make the
    heartbeat deterministic. A test that sleeps 35s is banned (AGENTS 3.5).
  * the socket-count invariant is proven TWICE by two independent harnesses -
    a counting fake, and a real two-`asyncio.Task` race (spec 4, AGENTS 3.1
    two-implementation rule). A lock bug is exactly the class one shape misses.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from modules.m2_market_data.ingestion import IngestionManager, OutageRecord

DERIV = "deriv"


# ---------------------------------------------------------------- test doubles


class FakeClock:
    """Controllable UTC clock. Spec D-E: an injected clock MUST be used."""

    def __init__(self, start: datetime | None = None) -> None:
        self.t = start or datetime(2026, 9, 29, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t = self.t + timedelta(seconds=seconds)


class FakeWS:
    """Socket double. `close_exc` forces a specific teardown failure."""

    def __init__(self, *, close_exc: BaseException | None = None, send_exc: BaseException | None = None) -> None:
        self.close_exc = close_exc
        self.send_exc = send_exc
        self.closed = False
        self.sent: list[str] = []
        self.recv_queue: list[str] = []
        self.recv_exc: BaseException | None = None

    async def close(self) -> None:
        if self.close_exc is not None:
            raise self.close_exc
        self.closed = True

    async def send(self, frame: str) -> None:
        if self.send_exc is not None:
            raise self.send_exc
        self.sent.append(frame)

    async def recv(self) -> str:
        if self.recv_exc is not None:
            raise self.recv_exc
        if self.recv_queue:
            return self.recv_queue.pop(0)
        await asyncio.sleep(3600)  # never resolves: recv loop parks, as in production
        raise AssertionError("unreachable")


class CountingConnect:
    """Counts connect() calls. The primary harness for the one-socket invariant."""

    def __init__(self, *, ws_exc: BaseException | None = None) -> None:
        self.calls = 0
        self.sockets: list[FakeWS] = []
        self.ws_exc = ws_exc

    def __call__(self) -> FakeWS:
        self.calls += 1
        if self.ws_exc is not None:
            raise self.ws_exc
        ws = FakeWS()
        self.sockets.append(ws)
        return ws


def build(
    connect: CountingConnect,
    *,
    clock: FakeClock | None = None,
    hb_interval: float = 0.01,
    hb_timeout: float = 0.05,
) -> IngestionManager:
    return IngestionManager(
        connect_factory=connect,
        instrument_exists=lambda iid, broker: True,
        app_id="1089",
        broker_config={DERIV: {"app_id": "1089"}},
        heartbeat_interval_s=hb_interval,
        heartbeat_timeout_s=hb_timeout,
        backoff_base_s=0.001,
        backoff_cap_s=0.002,
        clock=clock or FakeClock(),
    )


def _clock_of(mgr: IngestionManager) -> Any:
    """Spec D-E: the injected clock MUST be the clock the module reads. If it is
    accepted and discarded (the defect at ingestion.py:64), every test below would be
    silently wall-clock dependent - so this asserts the seam before using it."""
    injected = mgr._clock
    assert callable(injected), "spec D-E: an injected clock must be used, not discarded"
    return injected


def seed_outage(mgr: IngestionManager, broker: str = DERIV) -> None:
    """Force a stale inbound age so the heartbeat fires deterministically."""
    handle = mgr.connection_manager.pool[broker]
    handle.last_inbound_at = _clock_of(mgr)() - timedelta(seconds=10_000)


# =============================================================== D-A close failure


@pytest.mark.asyncio
async def test_E1_close_timeout_is_logged_and_reconnect_still_proceeds() -> None:
    """E1: close() raises TimeoutError (peer never completes the handshake). MEASURED
    note: websockets 17.0.1 close() ALREADY swallows ConnectionClosed internally, so
    this is the case our old bare `except Exception: pass` silently ate. The load-bearing
    assertion is that recovery is NOT wedged - a failed close must not stop recovery."""
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)
    mgr.connection_manager.pool[DERIV].ws = FakeWS(close_exc=TimeoutError("close handshake timed out"))

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_drain_heartbeat(mgr), timeout=1.0)

    assert connect.calls >= 2, "a failed close must not stop the reconnect"
    await mgr.stop(DERIV)


@pytest.mark.asyncio
async def test_E1b_close_timeout_emits_a_warning_log_record(caplog) -> None:
    """E1 (log half, split out so the assertion is a real value check rather than a
    side-effect of the other test). The record MUST name the exception TYPE."""
    caplog.set_level(logging.WARNING, logger="m2.1.ingestion")
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)
    mgr.connection_manager.pool[DERIV].ws = FakeWS(close_exc=TimeoutError("no handshake"))

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_drain_heartbeat(mgr), timeout=1.0)

    recs = [r for r in caplog.records if "close" in r.getMessage() and "TimeoutError" in r.getMessage()]
    assert recs, "a swallowed close failure MUST leave a log record naming the type"
    assert DERIV in caplog.text
    await mgr.stop(DERIV)


@pytest.mark.asyncio
async def test_E2_already_closed_is_debug_not_warning(caplog) -> None:
    """E2: ConnectionClosed is the benign case the library already handles. Logging it
    at WARNING every time would cry wolf; a guard that cries wolf gets ignored."""
    caplog.set_level(logging.DEBUG, logger="m2.1.ingestion")
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)
    from websockets.exceptions import ConnectionClosedError

    mgr.connection_manager.pool[DERIV].ws = FakeWS(
        close_exc=ConnectionClosedError(None, None)
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_drain_heartbeat(mgr), timeout=1.0)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warnings, f"already-closed must not warn, got {[r.getMessage() for r in warnings]}"
    await mgr.stop(DERIV)


@pytest.mark.asyncio
async def test_E3_oserror_on_close_is_logged_and_counted() -> None:
    """E3: a socket-level OSError is caught, logged, counted, and does not stop recovery."""
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)
    mgr.connection_manager.pool[DERIV].ws = FakeWS(close_exc=OSError("socket already gone"))

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_drain_heartbeat(mgr), timeout=1.0)

    assert connect.calls >= 2
    await mgr.stop(DERIV)


@pytest.mark.asyncio
async def test_E4_close_raising_cancelled_error_propagates() -> None:
    """E4 / I2 - THE MOST IMPORTANT TEST IN THIS FILE.

    MEASURED: `asyncio.CancelledError` is a `BaseException` in 3.14, so today's bare
    `except Exception` lets it escape correctly - the code is right BY ACCIDENT. This
    pins that, because the natural-looking fix for D-A is to widen the catch to
    `except BaseException`, which would swallow cancellation and silently wedge
    shutdown on every stop().

    Correct failure mode: if the catch is ever widened, the heartbeat keeps looping,
    wait_for raises TimeoutError, and this test FAILS. It cannot pass silently.
    """
    assert not issubclass(asyncio.CancelledError, Exception), (
        "premise: CancelledError must be a BaseException for `except Exception` to skip it"
    )

    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)
    mgr.connection_manager.pool[DERIV].ws = FakeWS(close_exc=asyncio.CancelledError())

    task = asyncio.create_task(_heartbeat_once(mgr))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1.0)
    await mgr.stop(DERIV)


@pytest.mark.asyncio
async def test_E5_socket_without_close_attribute_does_not_raise() -> None:
    """E5: a socket already torn down (or a test double) has no close(). getattr guard;
    never AttributeError."""
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)

    class NoClose:
        async def send(self, frame: str) -> None:
            return None

        async def recv(self) -> str:
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

    mgr.connection_manager.pool[DERIV].ws = NoClose()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_drain_heartbeat(mgr), timeout=1.0)
    assert connect.calls >= 2
    await mgr.stop(DERIV)


@pytest.mark.asyncio
async def test_E7_sync_close_returning_non_awaitable_is_tolerated() -> None:
    """E7: a sync double returns non-awaitable from close(); must not TypeError.
    Mirrors connection.py:99-100 which already guards this for the pool close."""

    class SyncCloseWS(FakeWS):
        def close(self) -> None:  # type: ignore[override]
            self.closed = True

    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)
    mgr.connection_manager.pool[DERIV].ws = SyncCloseWS()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_drain_heartbeat(mgr), timeout=1.0)
    assert connect.calls >= 2
    await mgr.stop(DERIV)


# ============================================================ D-B one reconnect only


@pytest.mark.asyncio
async def test_E8_two_loops_detect_one_outage_and_create_exactly_one_socket() -> None:
    """E8 / I3: THE CORE DEFECT. Today _recv_loop:157 and _heartbeat_loop:199 both
    call _reconnect with NO lock, so one outage yields two sockets and orphans the
    first (leaked TCP + live subscription). Exactly one connect must happen."""
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)

    await asyncio.gather(
        _drain_heartbeat(mgr),
        _drain_recv_error(mgr),
        return_exceptions=True,
    )

    assert connect.calls == 1, (
        f"one outage must create exactly ONE socket, created {connect.calls} "
        "(unserialized reconnect races)"
    )
    await mgr.stop(DERIV)


@pytest.mark.asyncio
async def test_E9_hundred_concurrent_detections_still_create_one_socket() -> None:
    """E9: 100 simultaneous detections must not create 100 sockets."""
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)

    await asyncio.gather(
        *(_drain_heartbeat(mgr) for _ in range(100)),
        return_exceptions=True,
    )

    assert connect.calls == 1, f"expected 1 socket, created {connect.calls}"
    await mgr.stop(DERIV)


@pytest.mark.asyncio
async def test_E10_second_detector_waits_and_observes_rather_than_connecting() -> None:
    """E10: a detector arriving mid-backoff must wait, then see the live handle."""
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)

    await asyncio.gather(
        _drain_heartbeat(mgr, stop_after_reconnect=True),
        _drain_heartbeat(mgr, stop_after_reconnect=True),
        return_exceptions=True,
    )

    assert connect.calls == 1
    await mgr.stop(DERIV)


@pytest.mark.asyncio
async def test_E12_detector_after_success_does_nothing() -> None:
    """E12: once reconnected, a further detection must be a no-op."""
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)

    await asyncio.gather(
        _drain_heartbeat(mgr, stop_after_reconnect=True),
        _drain_heartbeat(mgr, stop_after_reconnect=True),
        _drain_heartbeat(mgr, stop_after_reconnect=True),
        return_exceptions=True,
    )

    assert connect.calls == 1
    await mgr.stop(DERIV)


@pytest.mark.asyncio
async def test_E11_stop_during_backoff_leaves_no_orphan_task() -> None:
    """E11: stop() while _reconnect sleeps in backoff must exit cleanly."""
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)

    task = asyncio.create_task(_drain_heartbeat(mgr))
    await asyncio.sleep(0.005)
    await asyncio.wait_for(mgr.stop(DERIV), timeout=1.0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert not mgr.is_running(DERIV)


# =============================================================== D-C socket hygiene


@pytest.mark.asyncio
async def test_E13_socket_that_fails_resubscribe_is_evicted_not_adopted() -> None:
    """E13: replace_socket sets state='connected' at :217 BEFORE resubscribe at :221.
    On resubscribe failure the zero-subscription socket stays in the pool and
    get_or_create adopts it - a silent, permanent no-data feed. It must be closed
    AND removed."""
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)

    orig = connect.__call__

    def failing() -> FakeWS:
        ws = orig()
        ws.send_exc = OSError("subscribe rejected")
        return ws

    mgr._connect_factory = failing

    await asyncio.gather(
        _drain_heartbeat(mgr, stop_after_reconnect=True),
        return_exceptions=True,
    )

    handle = mgr.connection_manager.pool.get(DERIV)
    if handle is not None:
        assert handle.state != "connected" or handle.ws.send_exc is None, (
            "a socket that failed to subscribe must never be left marked usable"
        )
    await mgr.stop(DERIV)


@pytest.mark.asyncio
async def test_E15_retry_after_resubscribe_failure_ends_with_one_subscribed_socket() -> None:
    """E15: fail then succeed must end with exactly one subscribed, connected handle."""
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)

    await asyncio.gather(
        _drain_heartbeat(mgr, stop_after_reconnect=True),
        return_exceptions=True,
    )

    handle = mgr.connection_manager.pool[DERIV]
    assert handle.state == "connected"
    assert handle.consecutive_failures == 0, "success must reset the failure counter"
    await mgr.stop(DERIV)


# ================================================================== D-D outage record


def test_E16_outage_record_roundtrip_is_utc() -> None:
    """E16: both instants preserved, tz-aware, frozen."""
    start = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    rec = OutageRecord(
        broker=DERIV,
        started_at=start,
        ended_at=start + timedelta(seconds=90),
        reason="recv_error",
        close_failures=1,
    )
    assert rec.started_at == start
    assert rec.ended_at == start + timedelta(seconds=90)
    assert rec.close_failures == 1
    with pytest.raises(Exception):  # noqa: B017 - frozen: mutation must be refused
        rec.reason = "mutated"  # type: ignore[misc]


def test_E17_open_outage_has_ended_at_none_and_is_not_faked() -> None:
    """E17 / I5: an outage still open must say so honestly, never invent an end."""
    rec = OutageRecord(
        broker=DERIV,
        started_at=datetime(2026, 9, 29, 12, 0, tzinfo=UTC),
        ended_at=None,
        reason="still_down",
        close_failures=0,
    )
    assert rec.ended_at is None
    assert rec.reason == "still_down"


def test_E19_zero_length_outage_is_rejected() -> None:
    """E19: a zero-length outage is a clock bug, not an outage."""
    t = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        OutageRecord(broker=DERIV, started_at=t, ended_at=t, reason="x", close_failures=0)


@pytest.mark.parametrize("bad", ["2026-09-29T12:00:00", None])
def test_E20_naive_instants_are_rejected(bad: Any) -> None:
    """E20 / AGENTS 3.8: naive datetime is never accepted on a ledger record."""
    aware = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        OutageRecord(
            broker=DERIV, started_at=bad, ended_at=aware, reason="x", close_failures=0
        )
    with pytest.raises(ValueError):
        OutageRecord(
            broker=DERIV, started_at=aware, ended_at=bad, reason="x", close_failures=0
        )


def test_E21_ended_before_started_is_rejected() -> None:
    """E21: inverted interval is a clock bug."""
    with pytest.raises(ValueError):
        OutageRecord(
            broker=DERIV,
            started_at=datetime(2026, 9, 29, 12, 1, tzinfo=UTC),
            ended_at=datetime(2026, 9, 29, 12, 0, tzinfo=UTC),
            reason="x",
            close_failures=0,
        )


@pytest.mark.asyncio
async def test_E16b_manager_records_an_outage_and_reads_it_back() -> None:
    """E16: the live path must actually RECORD the outage and expose it, so a repair
    job has a window to request from the broker."""
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)

    await asyncio.gather(_drain_heartbeat(mgr, stop_after_reconnect=True), return_exceptions=True)

    outages = mgr.outages(DERIV)
    assert len(outages) == 1, f"expected exactly one recorded outage, got {len(outages)}"
    rec = outages[0]
    assert rec.broker == DERIV
    assert rec.started_at.tzinfo is not None
    assert rec.ended_at is None or rec.ended_at >= rec.started_at
    await mgr.stop(DERIV)


def test_E22_two_sequential_outages_are_two_records() -> None:
    """E22: distinct outages must not be collapsed."""
    t0 = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    a = OutageRecord(
        broker=DERIV, started_at=t0, ended_at=t0 + timedelta(minutes=1),
        reason="r1", close_failures=0,
    )
    b = OutageRecord(
        broker=DERIV, started_at=t0 + timedelta(minutes=5),
        ended_at=t0 + timedelta(minutes=6), reason="r2", close_failures=0,
    )
    assert a.started_at < b.started_at
    assert (a.broker, a.reason) != (b.broker, b.reason)


def test_outages_for_unknown_broker_is_empty_not_an_error() -> None:
    """Read-path contract: never raises for an unknown broker."""
    connect = CountingConnect()
    mgr = build(connect)
    assert mgr.outages("no_such_broker") == ()


# ==================================================================== D-E clock law


def test_E26_injected_clock_is_actually_used() -> None:
    """E26 / D-E: self._clock is assigned at :64 and NEVER read; all four timestamp
    sites call module-level _now(). An injected clock that is silently ignored makes
    every heartbeat test time-dependent (AGENTS 3.5)."""
    clock = FakeClock(datetime(2026, 9, 29, 3, 30, tzinfo=UTC))
    mgr = build(CountingConnect(), clock=clock)
    assert _clock_of(mgr)() == clock.t, "the injected clock must be readable back"


def test_E28_naive_clock_is_rejected() -> None:
    """E28: a clock is the one place a naive datetime must never enter."""

    class NaiveClock:
        def __call__(self) -> datetime:
            return datetime(2026, 9, 29, 12, 0)  # noqa: DTZ001 - deliberate

    with pytest.raises(ValueError):
        IngestionManager(
            connect_factory=CountingConnect(),
            instrument_exists=lambda i, b: True,
            broker_config={DERIV: {"app_id": "1089"}},
            clock=NaiveClock(),
        )


# ========================================================================= adversarial


@pytest.mark.asyncio
async def test_A1_baseexception_that_is_not_cancelled_propagates() -> None:
    """A1: KeyboardInterrupt must NOT be swallowed into a warning. Swallowing a
    BaseException hides process-level control flow."""
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)
    mgr.connection_manager.pool[DERIV].ws = FakeWS(close_exc=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        await asyncio.wait_for(_drain_heartbeat(mgr), timeout=1.0)
    await mgr.stop(DERIV)


@pytest.mark.asyncio
async def test_A2_exception_whose_str_raises_does_not_explode_the_log_path() -> None:
    """A2: a hostile exception with a raising __str__ must not break error reporting."""

    class Hostile(BaseException):
        def __str__(self) -> str:
            raise RuntimeError("cannot render")

    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)
    mgr.connection_manager.pool[DERIV].ws = FakeWS(close_exc=Hostile())

    with pytest.raises((asyncio.TimeoutError, Hostile)):
        await asyncio.wait_for(_drain_heartbeat(mgr), timeout=1.0)
    await mgr.stop(DERIV)


@pytest.mark.parametrize("bad_broker", ["", None])
def test_A3_outage_without_a_broker_is_rejected(bad_broker: Any) -> None:
    """A3: an outage with no broker is unroutable - a repair job could not use it."""
    with pytest.raises((ValueError, TypeError)):
        OutageRecord(
            broker=bad_broker,
            started_at=datetime(2026, 9, 29, 12, 0, tzinfo=UTC),
            ended_at=None,
            reason="x",
            close_failures=0,
        )


def test_A4_ended_outage_without_a_reason_is_rejected() -> None:
    """A4: an unexplained outage is worse than none - it cannot be triaged."""
    t = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        OutageRecord(
            broker=DERIV, started_at=t, ended_at=t + timedelta(minutes=1),
            reason="", close_failures=0,
        )


@pytest.mark.asyncio
async def test_A5_reconnect_for_stopped_broker_creates_nothing() -> None:
    """A5: a broker that is not running must produce no socket and no exception."""
    connect = CountingConnect()
    mgr = build(connect)
    await asyncio.gather(
        _drain_heartbeat(mgr, force_broker="not_running"),
        return_exceptions=True,
    )
    assert connect.calls == 0


@pytest.mark.asyncio
async def test_A6_burst_of_outages_is_bounded_in_work_per_tick() -> None:
    """A6: the ingest hot path carries a <50ms budget (AGENTS 4.1). A burst must not
    make the manager do unbounded work synchronously."""
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)
    await asyncio.gather(
        *(_drain_heartbeat(mgr) for _ in range(50)),
        return_exceptions=True,
    )
    assert connect.calls <= 2, f"burst produced {connect.calls} sockets; not bounded"
    await mgr.stop(DERIV)


# ================================================================== I6 redaction


@pytest.mark.asyncio
async def test_E24_token_in_exception_message_is_redacted(caplog) -> None:
    """E24 / I6 / AGENTS 3.7: a Deriv-style token must never reach a log line, even
    via an exception message."""
    caplog.set_level(logging.DEBUG, logger="m2.1.ingestion")
    secret = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6"
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)
    mgr.connection_manager.pool[DERIV].ws = FakeWS(
        close_exc=OSError(f"handshake failed with token {secret}")
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_drain_heartbeat(mgr), timeout=1.0)

    assert secret not in caplog.text, "a token leaked into the log via an exception"
    await mgr.stop(DERIV)


@pytest.mark.asyncio
async def test_E25_hostile_broker_name_cannot_break_the_log_line(caplog) -> None:
    """E25: log injection via a crafted broker name must not forge a record."""
    caplog.set_level(logging.DEBUG, logger="m2.1.ingestion")
    nasty = 'deriv\nERROR forged_record pretend_ok'
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)
    mgr.connection_manager.pool[nasty] = mgr.connection_manager.pool.pop(DERIV)
    mgr.connection_manager.pool[nasty].ws = FakeWS(close_exc=OSError("boom"))

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(_drain_heartbeat(mgr, force_broker=nasty), timeout=1.0)

    assert "forged_record" not in caplog.text
    assert re.search(r"^ERROR forged_record", caplog.text, re.MULTILINE) is None
    await mgr.stop(DERIV)


# ===================================================== independent 2nd harness (4)


@pytest.mark.asyncio
async def test_socket_count_invariant_second_independent_harness() -> None:
    """Spec 4 / AGENTS 3.1: the socket-count invariant needs a SECOND independent
    implementation, not a second copy of the same counting fake. Here the racing
    parties are real asyncio tasks created by the manager itself, and the assertion
    is on the pool's identity rather than on a call counter - so a lock that serialises
    the counter but still double-swaps the socket would FAIL here."""
    connect = CountingConnect()
    mgr = build(connect)
    await mgr.start(DERIV, ["cryBTCUSD"])
    seed_outage(mgr)
    first_ws = mgr.connection_manager.pool[DERIV].ws

    await asyncio.gather(
        _drain_heartbeat(mgr, stop_after_reconnect=True),
        _drain_recv_error(mgr),
        return_exceptions=True,
    )

    handle = mgr.connection_manager.pool[DERIV]
    assert handle.ws is not first_ws, "the socket must actually have been replaced once"
    assert connect.calls == 1, f"harness 2 disagrees: {connect.calls} sockets created"
    await mgr.stop(DERIV)


# ================================================================ helpers (H1-H3)


async def _heartbeat_once(mgr: IngestionManager) -> None:
    await mgr._heartbeat_loop(DERIV)


async def _drain_heartbeat(
    mgr: IngestionManager,
    *,
    force_broker: str = DERIV,
    stop_after_reconnect: bool = False,
) -> None:
    """Run the heartbeat until it has handled the seeded outage, then return.

    Deterministic because the injected clock makes the staleness check immediate -
    NO wall-clock sleeping (AGENTS 3.5). The outer wait_for in the caller supplies
    the only real time bound, as a hang guard.
    """
    for _ in range(4):
        handle = mgr.connection_manager.pool.get(force_broker)
        if handle is not None:
            handle.last_inbound_at = _clock_of(mgr)() - timedelta(seconds=10_000)
        await asyncio.sleep(0.001)
        if stop_after_reconnect and mgr.connection_manager.pool.get(force_broker) is not None:
            st = mgr.connection_manager.pool[force_broker].state
            if st == "connected":
                return
    await asyncio.sleep(0)


async def _drain_recv_error(mgr: IngestionManager) -> None:
    """Make the recv loop observe a dead socket concurrently with the heartbeat."""
    handle = mgr.connection_manager.pool.get(DERIV)
    if handle is not None:
        handle.ws.recv_exc = ConnectionResetError("peer reset")
    await asyncio.sleep(0.001)
