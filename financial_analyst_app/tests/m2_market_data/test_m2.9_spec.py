"""
M2.9 HOT Cache — Valkey latest-tick + forming-candle store — Spec tests (WRITTEN BEFORE CODE).

WHY: "cache is never the truth" fail-open semantics, tenant-law keys (no user_id),
     bounded TTL, and typed validation errors must be locked by tests BEFORE
     modules/m2_market_data/hot.py exists (AGENTS §2 Step 3, spec m2.9_hot.spec.md).

RED until Step 5: ModuleNotFoundError: modules.m2_market_data.hot — honest collection
RED, never skipped (same pattern as M3.0 A29).
Tests never import `redis` (client injected) — hermetic per spec Dependencies.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from modules.m2_market_data import hot  # RED until Step 5 (impl)
from shared.contracts.market_data import Candle, MarketData, Tick

# ---- fixtures (contract constructors only — no impl) -----------------------------

def _md(**over: object) -> MarketData:
    base: dict[str, object] = {
        "instrument_id": "FX.EURUSD",
        "symbol": "EURUSD",
        "broker": "deriv",
        "provider": "deriv",
        "provider_symbol": "frxEURUSD",
        "kind": "tick",
        "watermark": 1,
        "payload": Tick(price=1.08510, bid=1.08500, ask=1.08520, spread=0.00020),
        "exchange_timestamp": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "reference_snapshot_id": "REF-2026-09-25-001",
    }
    base.update(over)
    return MarketData(**base)  # type: ignore[arg-type]


def _candle(**over: object) -> Candle:
    base: dict[str, object] = {
        "open": 1.08500,
        "high": 1.08550,
        "low": 1.08450,
        "close": 1.08510,
        "interval": "5m",
        "interval_start": datetime(2026, 1, 2, 3, 0, tzinfo=UTC),
        "interval_end": datetime(2026, 1, 2, 3, 5, tzinfo=UTC),
    }
    base.update(over)
    return Candle(**base)  # type: ignore[arg-type]


class FakeRedis:
    """In-memory stand-in for redis.asyncio.Redis — ONLY the subset HotStore may use."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ex: dict[str, int] = {}
        self.fail: set[str] = set()  # method names that raise ConnectionError

    async def ping(self) -> bool:
        if "ping" in self.fail:
            raise ConnectionError("simulated outage")
        return True

    async def set(self, name: str, value: str, ex: int | None = None) -> bool:
        if "set" in self.fail:
            raise ConnectionError("simulated outage")
        self.data[name] = value
        if ex is None:
            self.ex.pop(name, None)
        else:
            self.ex[name] = ex
        return True

    async def get(self, name: str) -> str | None:
        if "get" in self.fail:
            raise ConnectionError("simulated outage")
        return self.data.get(name)

    async def delete(self, *names: str) -> int:
        removed = 0
        for n in names:
            removed += int(n in self.data)
            self.data.pop(n, None)
            self.ex.pop(n, None)
        return removed

    async def aclose(self) -> None:
        return None


def _store(fake: FakeRedis | None = None, **kw: object) -> tuple[Any, FakeRedis]:
    fake = fake if fake is not None else FakeRedis()
    store = hot.HotStore(url="redis://127.0.0.1:6379/0", client=fake, **kw)  # type: ignore[arg-type]
    return store, fake


# ---- edges: key format + tenant law ---------------------------------------------

def test_latest_key_format_golden() -> None:
    # Golden: exact key layout per spec Outputs
    assert hot.latest_key("deriv", "FX.EURUSD") == "hot:deriv:FX.EURUSD:latest"


def test_forming_key_format_golden() -> None:
    assert hot.forming_key("deriv", "FX.EURUSD", "5m") == "hot:deriv:FX.EURUSD:5m:forming"


async def test_no_user_id_in_keys() -> None:
    # Tenant law §4.3: even when the contract carries user_id, HOT keys never expose it
    store, fake = _store()
    md = _md(user_id="42")
    assert await store.set_latest(md) is True
    for key in fake.data:
        assert "user" not in key.lower()
        assert "42" not in key


# ---- edges: roundtrips + missing + overwrite + isolation -------------------------

async def test_latest_roundtrip_preserves_market_data() -> None:
    store, _ = _store()
    md = _md()
    assert await store.set_latest(md) is True
    got = await store.get_latest("deriv", "FX.EURUSD")
    assert got == md  # frozen-contract value equality through JSON


async def test_get_latest_missing_returns_none() -> None:
    store, _ = _store()
    assert await store.get_latest("deriv", "FX.EURUSD") is None


async def test_second_set_overwrites_latest() -> None:
    store, _ = _store()
    first = _md(watermark=1)
    second = _md(watermark=2, payload=Tick(price=1.09990))
    assert await store.set_latest(first) is True
    assert await store.set_latest(second) is True
    got = await store.get_latest("deriv", "FX.EURUSD")
    assert got == second
    assert got is not None and got.watermark == 2


async def test_stale_latest_watermark_rejected() -> None:
    # HUMAN LAW A33 (formal contract name): never replace newer cached state with older
    store, _ = _store()
    newer = _md(watermark=102)
    stale = _md(watermark=101, payload=Tick(price=1.05000))
    assert await store.set_latest(newer) is True
    assert await store.set_latest(stale) is False  # rejected, key untouched
    got = await store.get_latest("deriv", "FX.EURUSD")
    assert got == newer
    assert got is not None and got.watermark == 102


async def test_same_watermark_overwrites_not_ignores() -> None:
    # Documented decision A33: equal watermark = same sequence re-delivered ->
    # ACCEPT (overwrite, idempotent + self-healing), NOT ignore
    store, _ = _store()
    first = _md(watermark=102, payload=Tick(price=1.08510))
    second = _md(watermark=102, payload=Tick(price=1.08520))
    assert await store.set_latest(first) is True
    assert await store.set_latest(second) is True  # equal -> accepted
    got = await store.get_latest("deriv", "FX.EURUSD")
    assert got == second  # payload B proves overwrite happened


async def test_forming_roundtrip_preserves_candle() -> None:
    store, _ = _store()
    candle = _candle()
    assert await store.set_forming("deriv", "FX.EURUSD", "5m", candle) is True
    got = await store.get_forming("deriv", "FX.EURUSD", "5m")
    assert got == candle


async def test_get_forming_missing_returns_none() -> None:
    store, _ = _store()
    assert await store.get_forming("deriv", "FX.EURUSD", "5m") is None


async def test_forming_timeframes_isolated() -> None:
    store, _ = _store()
    c5 = _candle(interval="5m", close=1.08510)
    c15 = _candle(interval="15m", close=1.08770)
    assert await store.set_forming("deriv", "FX.EURUSD", "5m", c5) is True
    assert await store.set_forming("deriv", "FX.EURUSD", "15m", c15) is True
    assert await store.get_forming("deriv", "FX.EURUSD", "5m") == c5
    assert await store.get_forming("deriv", "FX.EURUSD", "15m") == c15


async def test_forming_overwrites_mutable_state() -> None:
    # Forming candle is MUTABLE state (human rule 2026-09-25) — last writer wins
    store, _ = _store()
    first = _candle(close=1.08510)
    second = _candle(close=1.09000)
    assert await store.set_forming("deriv", "FX.EURUSD", "5m", first) is True
    assert await store.set_forming("deriv", "FX.EURUSD", "5m", second) is True
    assert await store.get_forming("deriv", "FX.EURUSD", "5m") == second


async def test_stale_forming_candle_interval_rejected() -> None:
    # HUMAN LAW A33 (formal contract name): forming compares interval_start — older bucket rejected
    store, _ = _store()
    current = _candle(
        interval_start=datetime(2026, 1, 2, 3, 0, tzinfo=UTC),
        interval_end=datetime(2026, 1, 2, 3, 5, tzinfo=UTC),
    )
    stale = _candle(
        interval_start=datetime(2026, 1, 2, 2, 55, tzinfo=UTC),
        interval_end=datetime(2026, 1, 2, 3, 0, tzinfo=UTC),
        close=1.07000,
    )
    assert await store.set_forming("deriv", "FX.EURUSD", "5m", current) is True
    assert await store.set_forming("deriv", "FX.EURUSD", "5m", stale) is False
    assert await store.get_forming("deriv", "FX.EURUSD", "5m") == current


async def test_temporal_mismatch_rejects_write() -> None:
    # A33 r2: naive/aware mismatch = INVALID temporal state, NOT permission to overwrite
    store, _ = _store()
    # Intentionally naive (DTZ001 suppressed): test constructs invalid temporal state
    naive = _candle(
        interval_start=datetime(2026, 1, 2, 3, 0),  # noqa: DTZ001 — deliberate
        interval_end=datetime(2026, 1, 2, 3, 5),  # noqa: DTZ001 — deliberate
    )
    assert await store.set_forming("deriv", "FX.EURUSD", "5m", naive) is True  # empty key
    aware = _candle(
        interval_start=datetime(2026, 1, 2, 3, 0, tzinfo=UTC),
        interval_end=datetime(2026, 1, 2, 3, 5, tzinfo=UTC),
        close=1.09000,
    )
    assert await store.set_forming("deriv", "FX.EURUSD", "5m", aware) is False  # reject
    assert await store.get_forming("deriv", "FX.EURUSD", "5m") == naive  # unchanged


async def test_provider_falls_back_to_broker_when_absent() -> None:
    store, fake = _store()
    md = _md(provider=None, broker="deriv")
    assert await store.set_latest(md) is True
    assert "hot:deriv:FX.EURUSD:latest" in fake.data


async def test_fallback_namespace_read_symmetric() -> None:
    # A33 test 5: reader must hit using the SAME resolved namespace the writer used
    store, _ = _store()
    md = _md(provider=None, broker="deriv")  # writer resolves ns = "deriv" (broker)
    assert await store.set_latest(md) is True
    got = await store.get_latest("deriv", "FX.EURUSD")  # reader passes resolved ns
    assert got == md


def test_resolve_source_namespace_provider_priority() -> None:
    # ONE canonical namespace rule: provider wins when both present (human 2026-09-25)
    assert hot.resolve_source_namespace(_md(provider="deriv", broker="mt5")) == "deriv"
    assert hot.resolve_source_namespace(_md(provider=None, broker="mt5")) == "mt5"


# ---- edges: TTL + construction bounds (§3.13) ------------------------------------

async def test_default_ttl_applied_on_set_latest() -> None:
    store, fake = _store(default_ttl_seconds=60)
    assert await store.set_latest(_md()) is True
    assert fake.ex["hot:deriv:FX.EURUSD:latest"] == 60


async def test_forming_ttl_applied() -> None:
    # A33 test 4: TTL is not latest-only — forming keys expire on the same bound
    store, fake = _store(default_ttl_seconds=60)
    assert await store.set_forming("deriv", "FX.EURUSD", "5m", _candle()) is True
    assert fake.ex["hot:deriv:FX.EURUSD:5m:forming"] == 60


@pytest.mark.parametrize("bad_ttl", [0, -1, 86401])
def test_ttl_out_of_bounds_rejected(bad_ttl: int) -> None:
    with pytest.raises(ValueError, match="ttl"):
        hot.HotStore(url="redis://x", client=FakeRedis(), default_ttl_seconds=bad_ttl)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_ns", ["a b", "x:y"])
def test_invalid_namespace_rejected(bad_ns: str) -> None:
    with pytest.raises(ValueError, match="namespace"):
        hot.HotStore(url="redis://x", client=FakeRedis(), namespace=bad_ns)  # type: ignore[arg-type]


# ---- edges: fail-open (cache is NEVER the truth) ---------------------------------

async def test_set_failure_returns_false_fail_open() -> None:
    fake = FakeRedis()
    fake.fail.add("set")
    store, _ = _store(fake)
    assert await store.set_latest(_md()) is False  # swallowed, no raise


async def test_get_failure_returns_none_fail_open() -> None:
    fake = FakeRedis()
    fake.fail.add("get")
    store, _ = _store(fake)
    assert await store.get_latest("deriv", "FX.EURUSD") is None


async def test_ping_true_when_up_false_when_down() -> None:
    fake = FakeRedis()
    store, _ = _store(fake)
    assert await store.ping() is True
    fake.fail.add("ping")
    assert await store.ping() is False


async def test_cache_unavailable_never_propagates_to_caller() -> None:
    # cache_available != market_state_valid (human rule): total outage → sentinels only,
    # zero propagation — Valkey is never a hidden trading dependency
    fake = FakeRedis()
    fake.fail.update({"set", "get", "ping"})
    store, _ = _store(fake)
    assert await store.set_latest(_md()) is False
    assert await store.get_latest("deriv", "FX.EURUSD") is None
    assert await store.ping() is False


async def test_corrupt_stored_json_returns_none() -> None:
    store, fake = _store()
    fake.data["hot:deriv:FX.EURUSD:latest"] = "not-json{{{"
    assert await store.get_latest("deriv", "FX.EURUSD") is None


async def test_wrong_type_value_returns_none() -> None:
    store, fake = _store()
    fake.data["hot:deriv:FX.EURUSD:latest"] = "12345"  # valid JSON, not a MarketData
    assert await store.get_latest("deriv", "FX.EURUSD") is None


async def test_ops_without_client_fail_open() -> None:
    store = hot.HotStore(url="redis://127.0.0.1:6379/0")  # client=None, connect() never called
    assert await store.set_latest(_md()) is False
    assert await store.get_latest("deriv", "FX.EURUSD") is None
    assert await store.ping() is False


async def test_close_idempotent_returns_none() -> None:
    store, _ = _store()
    assert await store.close() is None
    assert await store.close() is None


# ---- edges: instrument_id required -----------------------------------------------

async def test_set_latest_requires_instrument_id() -> None:
    store, _ = _store()
    md = _md(instrument_id=None, symbol="EURUSD")
    with pytest.raises(ValueError, match="instrument_id"):
        await store.set_latest(md)


# ---- adversarial (typed failure, never silent-accept) ----------------------------

@pytest.mark.parametrize("bad", [None, "not-a-model"])
async def test_set_latest_non_marketdata_raises(bad: object) -> None:
    # A1: validation errors are NOT swallowed by the fail-open I/O path
    store, _ = _store()
    with pytest.raises(ValueError, match="MarketData"):
        await store.set_latest(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("provider", "instrument_id"),
    [
        ("deriv", "FX.EURUSD:evil"),  # key injection via colon
        ("", "FX.EURUSD"),            # empty provider
        ("deriv", "a b"),              # whitespace
    ],
)
async def test_key_part_injection_rejected(provider: str, instrument_id: str) -> None:
    # A2
    store, _ = _store()
    with pytest.raises(ValueError):
        await store.get_latest(provider, instrument_id)


async def test_set_latest_instrument_with_space_rejected() -> None:
    # A2 on the write path
    store, _ = _store()
    with pytest.raises(ValueError):
        await store.set_latest(_md(instrument_id="a b"))


@pytest.mark.parametrize("bad_tf", ["5m; DROP", "", "5min"])
async def test_bad_timeframe_rejected(bad_tf: str) -> None:
    # A3
    store, _ = _store()
    with pytest.raises(ValueError, match="timeframe"):
        await store.set_forming("deriv", "FX.EURUSD", bad_tf, _candle())
    with pytest.raises(ValueError, match="timeframe"):
        await store.get_forming("deriv", "FX.EURUSD", bad_tf)
