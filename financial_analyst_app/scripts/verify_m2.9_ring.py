#!/usr/bin/env python3
"""
M2.9 EXTENSION E1 — 1m rolling ring — live-Valkey verify (spec Step 7 artifact).

WHY: prove the real redis-py client against the real Valkey container (:6379) —
     unit tests use FakeRedis, this proves the actual server path: capacity 240,
     FIFO eviction (eviction IS the Timescale handoff — human ruling 2026-09-26
     "on eviction (4h later)"), A33 tail monotonic, A46 verbatim window, server
     TTL, fail-open sentinels, tenant-isolated key; then a REAL-data phase:
     1m candles built from the committed Deriv sample ticks via CandleBuilder,
     pushed and read back roundtrip.
Usage: python scripts/verify_m2.9_ring.py
Exit: 0 = ALL OK; 1 = first failed check.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # financial_analyst_app

from modules.m2_market_data.candles import CandleBuilder
from modules.m2_market_data.hot import (
    RING_CAPACITY,
    RING_TIMEFRAME,
    RING_TTL_SECONDS,
    HotStore,
    ring_key,
)
from shared.contracts.market_data import Candle, MarketData, Tick

URL = "redis://127.0.0.1:6379/0"  # infra/docker-compose.yml Valkey, loopback only
SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample_ticks_realistic.jsonl"
_BASE = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)  # synthetic walk anchor (matches tests)


def _check(label: str, ok: bool) -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}")
    return ok


def _c(minute: int) -> Candle:
    """Deterministic synthetic 1m candle (same fixture as spec tests)."""
    start = _BASE + timedelta(minutes=minute)
    return Candle(
        open=84000.0, high=84010.0, low=83990.0, close=84005.0, tick_count=5,
        interval="1m", interval_start=start, interval_end=start + timedelta(minutes=1),
    )


def _md_from_raw(raw: dict) -> MarketData:
    """Real Deriv tick frame -> MarketData (realistic input, sample file)."""
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
        received_at=datetime.now(tz=UTC),  # builder requires both stamps (line 63)
        reference_snapshot_id="REF-2026-09-25-001",
    )


async def main() -> int:
    ok = True
    store = HotStore(URL, default_ttl_seconds=60)
    await store.connect()
    ring = ring_key("deriv", "FX.EURUSD")

    # ---- R1/R2: live server + goldens ------------------------------------------------
    ok &= _check("ping live Valkey :6379", await store.ping())
    ok &= _check(f"ring_key golden ({ring})", ring == "hot:deriv:FX.EURUSD:ring:1m")
    ok &= _check(
        "constants golden (240 / 86400 / 1m)",
        RING_CAPACITY == 240 and RING_TTL_SECONDS == 86400 and RING_TIMEFRAME == "1m",
    )
    ok &= _check("key has no user_id (tenant law)", "user" not in ring.lower())

    # ---- R3/R4: FIFO mechanics against the REAL server --------------------------------
    first_status = (await store.ring_push("deriv", "FX.EURUSD", _c(0))).status
    for m in range(1, 240):
        await store.ring_push("deriv", "FX.EURUSD", _c(m))
    ok &= _check("240 pushes -> appended, len 240 (exact cap)",
                 first_status == "appended" and await store.ring_len("deriv", "FX.EURUSD") == 240)

    over = await store.ring_push("deriv", "FX.EURUSD", _c(240))
    ok &= _check("241st push -> evicted, payload = oldest bar (_c(0))",
                 over.status == "evicted" and over.evicted == _c(0))
    for m in range(241, 245):
        over = await store.ring_push("deriv", "FX.EURUSD", _c(m))
    ok &= _check("overflow walk -> each evicts exact predecessor, len stays 240",
                 over.status == "evicted" and over.evicted == _c(4)
                 and await store.ring_len("deriv", "FX.EURUSD") == 240)

    dup = await store.ring_push("deriv", "FX.EURUSD", _c(244))
    stale = await store.ring_push("deriv", "FX.EURUSD", _c(10))
    ok &= _check("tail duplicate -> duplicate; stale tail -> stale_rejected (A33)",
                 dup.status == "duplicate" and stale.status == "stale_rejected")

    # ---- R5/R6: verbatim window reads (A46) + latency advisory ------------------------
    window = await store.ring_read(
        "deriv", "FX.EURUSD", start=_BASE, end=_BASE + timedelta(minutes=300)
    )
    ok &= _check("full window: 240 ascending, first=_c(5) last=_c(244)",
                 len(window) == 240 and window[0] == _c(5) and window[-1] == _c(244)
                 and all(a.interval_start <= b.interval_start for a, b in pairwise(window)))
    ok &= _check("spot roundtrip window[100] == _c(105)", window[100] == _c(105))
    sub = await store.ring_read(
        "deriv", "FX.EURUSD", start=_BASE + timedelta(minutes=215),
        end=_BASE + timedelta(minutes=244),
    )
    ok &= _check("subset window last-30-min -> exactly 30 (no auto-expand A46)", len(sub) == 30)

    samples = []
    for _ in range(100):  # ADVISORY (spec E12): full-window read latency, not pass/fail
        t0 = time.perf_counter()
        await store.ring_read("deriv", "FX.EURUSD", start=_BASE,
                              end=_BASE + timedelta(minutes=300))
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    print(f"[info] full-window (240-bar) read latency median={samples[50]:.3f}ms "
          f"p95={samples[95]:.3f}ms (advisory)")

    # ---- R7: server-side TTL actually applied -----------------------------------------
    pttl = await store._client.pttl(ring)  # type: ignore[union-attr]
    ok &= _check(f"server TTL 0 < pttl <= 86400s (pttl={pttl}ms)",
                 0 < pttl <= RING_TTL_SECONDS * 1000)

    # ---- R8: fail-open + loud validation against a DEAD endpoint -----------------------
    dead = HotStore("redis://127.0.0.1:1/0")
    await dead.connect()
    dpush = await dead.ring_push("deriv", "FX.EURUSD", _c(0))
    dread = await dead.ring_read("deriv", "FX.EURUSD", start=_BASE,
                                 end=_BASE + timedelta(minutes=1))
    ok &= _check("dead endpoint: push->failed, read->[], len->0 (fail-open)",
                 dpush.status == "failed" and dread == []
                 and await dead.ring_len("deriv", "FX.EURUSD") == 0)
    naive_raises = False
    try:
        await dead.ring_read("deriv", "FX.EURUSD",
                             start=datetime(2026, 9, 26, 12, 0),  # noqa: DTZ001 — deliberate
                             end=_BASE + timedelta(minutes=1))
    except ValueError:
        naive_raises = True
    ok &= _check("dead endpoint: naive window still raises ValueError (loud, never silent)",
                 naive_raises)
    await dead.close()

    # cleanup phase A
    if store._client is not None:  # type: ignore[union-attr]
        await store._client.delete(ring)  # type: ignore[union-attr]
    ok &= _check("cleanup A: ring empty", await store.ring_len("deriv", "FX.EURUSD") == 0)

    # ---- R9: REAL-data phase — sample Deriv ticks -> CandleBuilder -> ring roundtrip ---
    # mixed sample: echo/ack frames + OTHER symbols + adversarial rows (bid=-1.1,
    # ask="x") share this fixture — feed only clean frxEURUSD numeric ticks
    frames = [o for line in SAMPLE.read_text(encoding="utf-8").splitlines()
              if line and isinstance(o := json.loads(line), dict)
              and isinstance((t := o.get("tick")), dict)
              and t.get("symbol") == "frxEURUSD"
              and isinstance(t.get("bid"), (int, float)) and isinstance(t.get("ask"), (int, float))
              and t["bid"] > 0 and t["ask"] > t["bid"]]
    frames.sort(key=lambda r: r["tick"]["epoch"])  # chronological feed for the builder
    builder = CandleBuilder("FX.EURUSD", "deriv", interval="1m")
    built: list[Candle] = []
    for raw in frames:
        out = builder.on_tick(_md_from_raw(raw))
        if isinstance(out, Candle):
            built.append(out)
    tail = builder.close()
    if isinstance(tail, Candle):
        built.append(tail)
    ok &= _check(f"real ticks -> {len(built)} finalized 1m candles (>=1)", len(built) >= 1)

    if built:  # crash-safe: roundtrip only meaningful when the builder emitted bars
        statuses = [(await store.ring_push("deriv", "FX.EURUSD", c)).status for c in built]
        real = await store.ring_read(
            "deriv", "FX.EURUSD", start=built[0].interval_start, end=built[-1].interval_end
        )
        ok &= _check(f"real roundtrip: pushed {len(statuses)}, read {len(real)} exact roundtrip",
                     statuses == ["appended"] * len(built)
                     and len(real) == len(built)
                     and real[0] == built[0] and real[-1] == built[-1])
        print(f"[info] real candle sample: first O/H/L/C="
              f"{built[0].open}/{built[0].high}/{built[0].low}/{built[0].close} "
              f"start={built[0].interval_start} | last close={built[-1].close} "
              f"start={built[-1].interval_start} (eyeball)")

    # cleanup — leave no residue in the dev cache
    if store._client is not None:  # type: ignore[union-attr]
        await store._client.delete(ring)  # type: ignore[union-attr]
    ok &= _check("cleanup B: ring empty", await store.ring_len("deriv", "FX.EURUSD") == 0)
    await store.close()

    print(f"E1 ring verify ({RING_CAPACITY} cap, 5 evicted, {len(built)} real bars) "
          f"- {'ALL OK' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
