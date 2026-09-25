#!/usr/bin/env python3
"""
M2.9 HOT cache — live-Valkey verify (spec Step 7 artifact).

WHY: prove the real redis-py client against the real Valkey container (:6379):
     ping / roundtrip of sample-tick-derived MarketData / TTL applied / overwrite /
     missing->None / forming roundtrip / fail-open to a dead endpoint (never raises).
Usage: python scripts/verify_m2.9_hot.py
Exit: 0 = ALL OK; 1 = first failed check.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # financial_analyst_app

from modules.m2_market_data.hot import HotStore, latest_key
from shared.contracts.market_data import Candle, MarketData, Tick

URL = "redis://127.0.0.1:6379/0"  # infra/docker-compose.yml Valkey, loopback only
SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample_ticks_realistic.jsonl"


def _check(label: str, ok: bool) -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}")
    return ok


def _md_from_sample() -> MarketData:
    """Realistic input: first raw Deriv tick frame from the committed sample."""
    raw = json.loads(SAMPLE.read_text(encoding="utf-8").splitlines()[0])
    t = raw["tick"]
    return MarketData(
        instrument_id="FX.EURUSD",
        symbol=t["symbol"].removeprefix("frx"),
        broker="deriv",
        provider="deriv",
        provider_symbol=t["symbol"],
        kind="tick",
        watermark=1,
        payload=Tick(
            price=(t["bid"] + t["ask"]) / 2,
            bid=t["bid"],
            ask=t["ask"],
            spread=t["ask"] - t["bid"],
        ),
        exchange_timestamp=datetime.fromtimestamp(t["epoch"], tz=UTC),
        reference_snapshot_id="REF-2026-09-25-001",
    )


async def main() -> int:
    ok = True
    store = HotStore(URL, default_ttl_seconds=60)
    await store.connect()
    ok &= _check("ping live Valkey :6379", await store.ping())

    md = _md_from_sample()
    assert isinstance(md.payload, Tick)  # sample frame is a tick — narrows union for type-checkers
    key = latest_key("deriv", "FX.EURUSD")
    ok &= _check(f"set_latest (bid={md.payload.bid} ask={md.payload.ask})", await store.set_latest(md))
    got = await store.get_latest("deriv", "FX.EURUSD")
    ok &= _check("get_latest exact roundtrip", got == md)
    ok &= _check("key has no user_id (tenant law)", "user" not in key.lower())

    # TTL actually applied by the server (inspect via injected real client)
    ttl = await store._client.ttl(key)  # type: ignore[union-attr]
    ok &= _check(f"TTL applied 0 < ttl <= 60 (server ttl={ttl})", 0 < ttl <= 60)

    second = MarketData.model_validate(md.model_copy(update={"watermark": 2}).model_dump())
    ok &= _check("overwrite: second write wins", await store.set_latest(second) is True
                 and await store.get_latest("deriv", "FX.EURUSD") == second)

    # Monotonic law A33 against the REAL server: stale wm=1 can never clobber wm=2
    ok &= _check("stale wm=1 rejected, wm=2 retained (monotonic A33)",
                 await store.set_latest(md) is False
                 and await store.get_latest("deriv", "FX.EURUSD") == second)

    candle = Candle(
        open=1.08500, high=1.08550, low=1.08450, close=1.08540, tick_count=7,
        interval="5m",
        interval_start=datetime(2026, 3, 22, 19, 45, tzinfo=UTC),
        interval_end=datetime(2026, 3, 22, 19, 50, tzinfo=UTC),
    )
    ok &= _check("set_forming 5m", await store.set_forming("deriv", "FX.EURUSD", "5m", candle) is True)
    ok &= _check("get_forming exact roundtrip", await store.get_forming("deriv", "FX.EURUSD", "5m") == candle)
    ok &= _check("missing key -> None", await store.get_latest("deriv", "FX.NOSUCH") is None)

    # fail-open: dead endpoint must yield sentinels, NEVER an exception
    dead = HotStore("redis://127.0.0.1:1/0")
    await dead.connect()
    ok &= _check("dead endpoint: ping -> False", await dead.ping() is False)
    ok &= _check("dead endpoint: set -> False (fail-open)", await dead.set_latest(md) is False)
    ok &= _check("dead endpoint: get -> None (fail-open)", await dead.get_latest("deriv", "FX.EURUSD") is None)
    await dead.close()

    # cleanup — leave no residue in the dev cache
    if store._client is not None:  # type: ignore[union-attr]
        await store._client.delete(key, "hot:deriv:FX.EURUSD:5m:forming")  # type: ignore[union-attr]
    ok &= _check("cleanup: keys deleted", await store.get_latest("deriv", "FX.EURUSD") is None)
    await store.close()

    print(f"snapshot ref={md.reference_snapshot_id} exchange_ts={md.exchange_timestamp} - {'ALL OK' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
