"""
M2.9 EXTENSION E1 — 1m rolling ring (240 bars) — Spec tests (WRITTEN BEFORE CODE).

WHY: the D1 ring law ("Keep 4h as decided") makes Valkey a bounded sliding FIFO of
     finalized 1m candles so analytics read sub-ms windows instead of the DB. Capacity
     exactness (240), FIFO eviction, A33 tail monotonic, A46 verbatim windows and
     fail-open sentinels must be locked by tests BEFORE the ring code exists
     (AGENTS §2 Step 3, spec docs/specs/m2.9_hot.spec.md §Extension E1, human A52).

Honest RED until Step 5: AttributeError `module 'modules.m2_market_data.hot' has no
attribute 'ring_push'` (etc.) — runtime, never skipped.
Tests never import `redis` (client injected) — hermetic per spec Dependencies.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from modules.m2_market_data import hot  # ring API appears at Step 5 (A52)
from shared.contracts.market_data import Candle

_BASE = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _c(minute: int, **over: object) -> Candle:
    """Deterministic 1m BTC-USD-realistic candle at _BASE + minute."""
    start = _BASE + timedelta(minutes=minute)
    base: dict[str, object] = {
        "open": 84000.0,
        "high": 84010.0,
        "low": 83990.0,
        "close": 84005.0,
        "interval": "1m",
        "interval_start": start,
        "interval_end": start + timedelta(minutes=1),
    }
    base.update(over)
    return Candle(**base)  # type: ignore[arg-type]


class FakeRedis:
    """In-memory stand-in for redis.asyncio.Redis — ONLY the subset E1 may use."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.ex: dict[str, int] = {}
        self.fail: set[str] = set()  # method names that raise ConnectionError

    async def rpush(self, name: str, *values: str) -> int:
        if "rpush" in self.fail:
            raise ConnectionError("simulated outage")
        items = self.lists.setdefault(name, [])
        items.extend(str(v) for v in values)
        return len(items)

    async def lpop(self, name: str) -> str | None:
        if "lpop" in self.fail:
            raise ConnectionError("simulated outage")
        items = self.lists.get(name) or []
        return items.pop(0) if items else None

    async def llen(self, name: str) -> int:
        if "llen" in self.fail:
            raise ConnectionError("simulated outage")
        return len(self.lists.get(name, []))

    async def lrange(self, name: str, start: int, end: int) -> list[str]:
        if "lrange" in self.fail:
            raise ConnectionError("simulated outage")
        items = self.lists.get(name, [])
        n = len(items)
        if n == 0:
            return []

        def _norm(i: int) -> int:
            return n + i if i < 0 else i

        s, e = max(_norm(start), 0), min(_norm(end), n - 1)
        return items[s : e + 1] if s <= e else []

    async def expire(self, name: str, ttl: int) -> bool:
        if "expire" in self.fail:
            raise ConnectionError("simulated outage")
        self.ex[name] = ttl
        return True


@pytest.fixture
def fake() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def store(fake: FakeRedis) -> hot.HotStore:
    return hot.HotStore("redis://fake:6379/0", client=fake)


# ---- E14 goldens ---------------------------------------------------------------

def test_ring_key_golden() -> None:
    assert hot.ring_key("deriv", "FX.EURUSD") == "hot:deriv:FX.EURUSD:ring:1m"


def test_ring_constants_goldens() -> None:
    assert hot.RING_CAPACITY == 240  # D1: 4h x 1m, human "Keep 4h as decided"
    assert hot.RING_TTL_SECONDS == 86400  # bounded §3.13, refreshed per append


# ---- E1/E2/E3/E4/E5 push semantics --------------------------------------------

async def test_push_appends_and_grows_len(store: hot.HotStore) -> None:
    res = await store.ring_push("deriv", "FX.EURUSD", _c(0))
    assert res.status == "appended"
    assert res.evicted is None
    assert await store.ring_len("deriv", "FX.EURUSD") == 1


async def test_capacity_overflow_evicts_oldest_fifo(
    store: hot.HotStore, fake: FakeRedis
) -> None:
    for m in range(240):  # fill to capacity exactly
        await store.ring_push("deriv", "FX.EURUSD", _c(m))
    res = await store.ring_push("deriv", "FX.EURUSD", _c(240))  # 241st → overflow
    assert res.status == "evicted"
    assert res.evicted == _c(0)  # exact oldest bar, FIFO never LIFO
    assert await store.ring_len("deriv", "FX.EURUSD") == 240  # hard cap, never 241
    window = await store.ring_read(
        "deriv", "FX.EURUSD", start=_BASE, end=_BASE + timedelta(minutes=300)
    )
    assert window[0] == _c(1)  # head advanced past the evicted bar
    assert window[-1] == _c(240)
    assert len(window) == 240


async def test_duplicate_tail_interval_skipped(store: hot.HotStore) -> None:
    await store.ring_push("deriv", "FX.EURUSD", _c(0))
    res = await store.ring_push("deriv", "FX.EURUSD", _c(0))
    assert res.status == "duplicate"
    assert res.evicted is None
    assert await store.ring_len("deriv", "FX.EURUSD") == 1  # no double-append


async def test_older_than_tail_rejected(store: hot.HotStore) -> None:
    await store.ring_push("deriv", "FX.EURUSD", _c(5))
    res = await store.ring_push("deriv", "FX.EURUSD", _c(4))
    assert res.status == "stale_rejected"
    window = await store.ring_read(
        "deriv", "FX.EURUSD", start=_BASE, end=_BASE + timedelta(minutes=60)
    )
    assert window == [_c(5)]  # tail unchanged (A33)


async def test_newer_with_gap_appended(store: hot.HotStore) -> None:
    await store.ring_push("deriv", "FX.EURUSD", _c(0))
    res = await store.ring_push("deriv", "FX.EURUSD", _c(5))  # t+5m gap
    assert res.status == "appended"  # cache heals gaps from WARM — no contiguity law
    assert await store.ring_len("deriv", "FX.EURUSD") == 2


# ---- E6/E7/E8 read semantics ---------------------------------------------------

async def test_read_empty_ring_returns_empty(store: hot.HotStore) -> None:
    got = await store.ring_read(
        "deriv", "FX.EURUSD", start=_BASE, end=_BASE + timedelta(minutes=5)
    )
    assert got == []


async def test_read_window_verbatim_no_auto_expand(store: hot.HotStore) -> None:
    for m in range(10):  # bars 0..9
        await store.ring_push("deriv", "FX.EURUSD", _c(m))
    got = await store.ring_read(
        "deriv", "FX.EURUSD", start=_BASE + timedelta(minutes=3), end=_BASE + timedelta(minutes=6)
    )
    assert got == [_c(3), _c(4), _c(5), _c(6)]  # inclusive both ends (A46)
    wide = await store.ring_read(
        "deriv", "FX.EURUSD", start=_BASE + timedelta(minutes=3), end=_BASE + timedelta(hours=9)
    )
    assert len(wide) == 7  # only real bars 3..9 exist — window NEVER auto-fills


async def test_read_roundtrip_equality_and_ascending(store: hot.HotStore) -> None:
    for m in range(4):
        await store.ring_push("deriv", "FX.EURUSD", _c(m))
    got = await store.ring_read(
        "deriv", "FX.EURUSD", start=_BASE, end=_BASE + timedelta(minutes=10)
    )
    assert got == [_c(0), _c(1), _c(2), _c(3)]  # frozen equality incl. tz-aware datetimes
    assert [c.interval_start for c in got] == sorted(c.interval_start for c in got)


# ---- E9 isolation ---------------------------------------------------------------

async def test_ring_isolation_two_instruments(store: hot.HotStore) -> None:
    await store.ring_push("deriv", "FX.EURUSD", _c(0))
    await store.ring_push("deriv", "CRYPTO.BTCUSD", _c(0))
    assert await store.ring_len("deriv", "FX.EURUSD") == 1
    assert await store.ring_len("deriv", "CRYPTO.BTCUSD") == 1
    empty = await store.ring_read(
        "deriv", "XAU.XAUUSD", start=_BASE, end=_BASE + timedelta(minutes=5)
    )
    assert empty == []


def test_ring_key_disjoint_from_latest_forming() -> None:
    rk = hot.ring_key("deriv", "FX.EURUSD")
    assert rk != hot.latest_key("deriv", "FX.EURUSD")
    assert rk != hot.forming_key("deriv", "FX.EURUSD", "1m")


# ---- E10 TTL --------------------------------------------------------------------

async def test_ttl_applied_on_append(store: hot.HotStore, fake: FakeRedis) -> None:
    await store.ring_push("deriv", "FX.EURUSD", _c(0))
    assert fake.ex[hot.ring_key("deriv", "FX.EURUSD")] == 86400


# ---- E11/E12/E13 fail-open ------------------------------------------------------

async def test_no_client_fail_open_sentinels() -> None:
    store = hot.HotStore("redis://fake:6379/0")  # no client, never connect()ed
    res = await store.ring_push("deriv", "FX.EURUSD", _c(0))
    assert res.status == "failed"
    assert res.evicted is None
    got = await store.ring_read(
        "deriv", "FX.EURUSD", start=_BASE, end=_BASE + timedelta(minutes=5)
    )
    assert got == []
    assert await store.ring_len("deriv", "FX.EURUSD") == 0


async def test_injected_io_failure_fail_open(store: hot.HotStore, fake: FakeRedis) -> None:
    fake.fail.add("rpush")
    res = await store.ring_push("deriv", "FX.EURUSD", _c(0))
    assert res.status == "failed"  # never raises
    fake.fail.discard("rpush")
    fake.fail.add("lrange")
    got = await store.ring_read(
        "deriv", "FX.EURUSD", start=_BASE, end=_BASE + timedelta(minutes=5)
    )
    assert got == []
    fake.fail.discard("lrange")
    fake.fail.add("llen")
    assert await store.ring_len("deriv", "FX.EURUSD") == 0


async def test_corrupt_element_skipped_on_read(store: hot.HotStore, fake: FakeRedis) -> None:
    await store.ring_push("deriv", "FX.EURUSD", _c(0))
    key = hot.ring_key("deriv", "FX.EURUSD")
    fake.lists.setdefault(key, []).append("not-json{{{")  # corrupt middle element
    await store.ring_push("deriv", "FX.EURUSD", _c(1))
    got = await store.ring_read(
        "deriv", "FX.EURUSD", start=_BASE, end=_BASE + timedelta(minutes=5)
    )
    assert got == [_c(0), _c(1)]  # valid elements survive; corrupt skipped, no raise


async def test_naive_stored_aware_push_rejected(
    store: hot.HotStore, caplog: pytest.LogCaptureFixture
) -> None:
    naive = datetime(2026, 9, 26, 13, 0)  # noqa: DTZ001 — naive ON PURPOSE (invalid state under test)
    await store.ring_push("deriv", "FX.EURUSD", _c(0, interval_start=naive,
                                                   interval_end=naive + timedelta(minutes=1)))
    res = await store.ring_push("deriv", "FX.EURUSD", _c(60))
    assert res.status == "stale_rejected"  # mismatch never = write permission (A33 r2)
    assert any("mismatch" in rec.message for rec in caplog.records)


# ---- B1..B4 adversarial (typed ValueError, never silent) ------------------------

async def test_push_rejects_non_candle(store: hot.HotStore) -> None:
    with pytest.raises(ValueError):
        await store.ring_push("deriv", "FX.EURUSD", None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await store.ring_push("deriv", "FX.EURUSD", "not-a-candle")  # type: ignore[arg-type]


async def test_read_rejects_naive_window(store: hot.HotStore) -> None:
    with pytest.raises(ValueError):
        await store.ring_read(
            "deriv", "FX.EURUSD",
            start=datetime(2026, 9, 26, 12, 0),  # noqa: DTZ001 — naive start is the attack
            end=_BASE + timedelta(minutes=5),
        )
    with pytest.raises(ValueError):
        await store.ring_read(
            "deriv", "FX.EURUSD",
            start=_BASE,
            end=datetime(2026, 9, 26, 13, 0),  # noqa: DTZ001 — naive end is the attack
        )


def test_key_injection_valueerror() -> None:
    with pytest.raises(ValueError):
        hot.ring_key("deriv", "a b")
    with pytest.raises(ValueError):
        hot.ring_key("deriv; DROP", "FX.EURUSD")


async def test_push_key_injection_valueerror(store: hot.HotStore) -> None:
    with pytest.raises(ValueError):
        await store.ring_push("deriv; DROP", "FX.EURUSD", _c(0))


async def test_read_rejects_inverted_window(store: hot.HotStore) -> None:
    with pytest.raises(ValueError):
        await store.ring_read(
            "deriv", "FX.EURUSD",
            start=_BASE + timedelta(minutes=10), end=_BASE,  # start > end
        )
