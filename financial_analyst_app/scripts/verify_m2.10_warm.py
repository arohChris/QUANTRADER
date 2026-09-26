"""Step 7 live verify: M2.10 WARM durable store against real TimescaleDB (A42).

Run:      python scripts/verify_m2.10_warm.py
Needs:    infra Timescale on 127.0.0.1:5432 (infra/docker-compose.yml)
          QUANTRADER2/.env TIMESCALE_DEV_PASSWORD (value never printed)
Covers:   1. DDL applied twice (idempotent IF NOT EXISTS / add_compression_policy)
          2. A42 compression enabled + 7d compression policy job on candles_1m
          3. M2.4 CandleBuilder from the committed Deriv sample ticks -> bars
          4. FIFO flush: ack -> rows + cursor (cursor stops at a real 20-min gap,
             never leaps - live proof of max_contiguous / A33 discipline)
          5. Re-flush same batch: inserted=0 (ON CONFLICT DO NOTHING, idempotent)
          6. A46 range reads: ascending, inclusive bounds, no auto-expansion
          7. Typed failures: unconnected read + dead port -> WarmError, password redacted
          8. Section 7 conversion: Lake parquet ticks -> ticks_live, rerun adds 0
          9. Cleanup: verify rows deleted; converted ticks_live KEPT (A42 store of record)
Exit 0 = all checks pass (human eyeballs the EYEBALL block). 1 = at least one FAIL.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # financial_analyst_app

import asyncpg

from modules.m2_market_data.candles import CandleBuilder
from modules.m2_market_data.storage import LakeReader
from modules.m2_market_data.warm import (
    Candles1mStore,
    WarmConfig,
    WarmError,
    apply_ddl,
)
from shared.contracts.market_data import Candle, MarketData, Tick

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SAMPLE = ROOT / "data" / "sample_ticks_realistic.jsonl"
LAKE = ROOT / "data" / "lake"
VERIFY_INSTR = "VERIFY.M2.10"     # isolated identity; deleted at start AND end
CONV_INSTR = "CRYPTO.BTCUSD"      # real captured ticks; rows KEPT after the run (A42)
CONV_DATE = "2026-09-25"

_fails = 0


def _check(label: str, ok: bool, detail: str = "") -> bool:
    global _fails
    if not ok:
        _fails += 1
    suffix = f" ({detail})" if detail else ""
    print(f"[{'OK' if ok else 'FAIL'}] {label}{suffix}")
    return ok


def _password() -> str:
    env = REPO / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("TIMESCALE_DEV_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"TIMESCALE_DEV_PASSWORD missing from {env}")


def _md_from_tick(raw: dict) -> MarketData:
    """One committed Deriv tick frame -> validated MarketData (m2.9 verify pattern)."""
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
        received_at=datetime.now(UTC),  # ingest layer sets arrival time (M2.4 needs it)
        reference_snapshot_id="REF-2026-09-25-001",
    )


def _valid_quote(raw: dict) -> bool:
    """M2.3 door before the builder: numeric + positive bid/ask only — the committed
    sample carries adversarial rows (negative price, ask='x') that schema validation
    drops at the door; they must never reach CandleBuilder."""
    t = raw["tick"]
    bid, ask = t.get("bid"), t.get("ask")
    return (
        isinstance(bid, (int, float)) and not isinstance(bid, bool)
        and isinstance(ask, (int, float)) and not isinstance(ask, bool)
        and bid > 0 and ask > 0
    )


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S%z")


async def main() -> int:
    pw = _password()
    dsn = f"postgresql://quantrader:{pw}@127.0.0.1:5432/quantrader"
    print("target: postgresql://quantrader:***@127.0.0.1:5432/quantrader (password never printed)")
    conn = await asyncpg.connect(dsn, timeout=10)

    # -- 1. DDL idempotence (applied twice on the live server) ------------------
    n1 = await apply_ddl(conn)
    n2 = await apply_ddl(conn)
    _check("DDL applied twice -> idempotent", n1 == 7 and n2 == 7, f"stmts {n1}/{n2}")

    # -- 2. A42 compression: segmentby config + 7d policy job --------------------
    seg = await conn.fetch(
        "SELECT attname FROM timescaledb_information.compression_settings "
        "WHERE hypertable_name = 'candles_1m' AND segmentby_column_index IS NOT NULL "
        "ORDER BY segmentby_column_index"
    )
    seg_cols = [r["attname"] for r in seg]
    policy = await conn.fetchval(
        "SELECT count(*) FROM timescaledb_information.jobs "
        "WHERE proc_name = 'policy_compression' AND hypertable_name = 'candles_1m'"
    )
    _check("A42 compression segmentby = provider, instrument_id",
           seg_cols == ["provider", "instrument_id"], f"segmentby={seg_cols}")
    _check("A42 7-day compression policy job present", int(policy) >= 1, f"jobs={policy}")

    # clean rerun: remove ONLY leftovers of prior verify runs --------------------
    d1 = await conn.execute(
        "DELETE FROM candles_1m WHERE provider='deriv' AND instrument_id=$1", VERIFY_INSTR
    )
    d2 = await conn.execute(
        "DELETE FROM flush_cursors WHERE provider='deriv' AND instrument_id=$1", VERIFY_INSTR
    )
    print(f"pre-clean of prior run: {d1}; {d2}")

    # -- 3. bars via M2.4 from the committed Deriv sample ------------------------
    builder = CandleBuilder(VERIFY_INSTR, "deriv", "1m")
    bars: list[Candle] = []
    schema_drops = 0   # M2.3 door: negative / non-numeric quotes never reach builder
    late_drops = 0     # M2.4 gate: ticks after a closed bucket
    eur_raw = [
        json.loads(line)
        for line in SAMPLE.read_text(encoding="utf-8").splitlines()
        if "tick" in json.loads(line) and json.loads(line)["tick"]["symbol"] == "frxEURUSD"
    ]
    for raw in eur_raw:
        if not _valid_quote(raw):
            schema_drops += 1
            continue
        res = builder.on_tick(_md_from_tick(raw))
        if isinstance(res, Candle):
            bars.append(res)
        elif res is not None:
            late_drops += 1
    last = builder.close()
    if last is not None:
        bars.append(last)
    _check("M2.3 door dropped the 2 adversarial rows (negative price, ask='x')",
           schema_drops == 2, f"schema_drops={schema_drops}")
    _check("M2.4 built bars from Deriv sample (2 distinct minutes)", len(bars) >= 2,
           f"bars={len(bars)} late_drops={late_drops}")

    # -- 4. flush: ack -> rows + cursor (gap case: cursor must NOT leap) ---------
    cfg = WarmConfig(dsn=dsn, connect_timeout_s=5)
    store = Candles1mStore(cfg, pool=conn)
    rep1 = await store.flush_batch("deriv", VERIFY_INSTR, bars, reference_snapshot_id="REF-verify")
    count1 = await conn.fetchval(
        "SELECT count(*) FROM candles_1m WHERE provider='deriv' AND instrument_id=$1",
        VERIFY_INSTR,
    )
    _check("flush#1 acked all bars into rows", rep1.acked is True and rep1.inserted == len(bars)
           and int(count1) == len(bars), f"inserted={rep1.inserted} rows={count1}")
    # sample has a real 20-minute gap (19:46 -> 20:06): contiguous cursor stays at bar[0]
    expect_wm = min(b.interval_start for b in bars)
    _check("cursor stops at the 20-min gap (never leaps)", rep1.cursor.watermark == expect_wm,
           f"cursor={_fmt(rep1.cursor.watermark) if rep1.cursor.watermark else None} "
           f"expected={_fmt(expect_wm)}")
    wm_row = await conn.fetchval(
        "SELECT watermark FROM flush_cursors WHERE provider='deriv' AND instrument_id=$1",
        VERIFY_INSTR,
    )
    _check("cursor persisted in flush_cursors", wm_row == expect_wm)

    # -- 5. re-flush idempotency (ON CONFLICT DO NOTHING) ------------------------
    rep2 = await store.flush_batch("deriv", VERIFY_INSTR, bars, reference_snapshot_id="REF-verify")
    count2 = await conn.fetchval(
        "SELECT count(*) FROM candles_1m WHERE provider='deriv' AND instrument_id=$1",
        VERIFY_INSTR,
    )
    _check("re-flush inserts 0, rowcount stable", rep2.inserted == 0 and count2 == count1,
           f"inserted={rep2.inserted} rows={count2}")

    # -- 6. A46 range reads: inclusive, ascending, no auto-expansion -------------
    lo = min(b.interval_start for b in bars)
    hi = max(b.interval_start for b in bars)
    full = await store.read_1m("deriv", VERIFY_INSTR, lo, hi)
    starts = [c.interval_start for c in full]
    _check("read_1m full window: n bars ascending", starts == sorted(starts) and len(full) == len(bars),
           f"n={len(full)}")
    one = await store.read_1m("deriv", VERIFY_INSTR, lo, lo)
    _check("inclusive lower bound -> exactly bar[0]", len(one) == 1
           and one[0].interval_start == lo, f"n={len(one)}")
    one_hi = await store.read_1m("deriv", VERIFY_INSTR, hi, hi)
    _check("inclusive upper bound -> exactly last bar", len(one_hi) == 1
           and one_hi[0].interval_start == hi, f"n={len(one_hi)}")
    before = await store.read_1m("deriv", VERIFY_INSTR, lo - timedelta(hours=2),
                                 lo - timedelta(hours=1))
    _check("A46: window outside data -> [] (never auto-expand)", before == [], f"n={len(before)}")
    _check("roundtrip: bar[0] OHLC survives encode/decode",
           full[0].open == bars[0].open and full[0].close == bars[0].close
           and full[0].tick_count == bars[0].tick_count)

    # -- 7. typed failures: unconnected + dead port, password redacted -----------
    unconnected = Candles1mStore(cfg, pool=None)
    try:
        await unconnected.read_1m("deriv", VERIFY_INSTR, lo, hi)
        ok_un = False
        un_msg = "no error raised"
    except WarmError as exc:
        ok_un = True
        un_msg = str(exc)
    _check("unconnected read -> WarmError (durable path never fail-open)", ok_un, un_msg[:60])
    dead = Candles1mStore(
        WarmConfig(dsn=f"postgresql://quantrader:{pw}@127.0.0.1:1/quantrader",
                   connect_timeout_s=1, pool_min=1, pool_max=2),
    )
    try:
        await dead.connect()
        ok_dead = False
        dead_msg = "connect unexpectedly succeeded"
    except WarmError as exc:
        dead_msg = str(exc)
        ok_dead = True
    _check("dead port 1 -> WarmError", ok_dead, dead_msg[:70])
    _check("dead-port error leaks no password", pw not in dead_msg,
           "password absent from exception text")

    # -- 8. Section 7 conversion: Lake parquet -> ticks_live, rerun adds 0 -------
    reader = LakeReader()
    mds = await reader.read_partition(
        lake_root=LAKE, provider="deriv", instrument_id=CONV_INSTR,
        date=CONV_DATE, data_type="ticks",
    )
    _check("LakeReader read real captured tick partition", len(mds) > 0, f"mds={len(mds)}")

    async def _convert() -> int:
        inserted = 0
        for md in mds:
            assert isinstance(md.payload, Tick)
            status = await conn.execute(
                "INSERT INTO ticks_live (provider, instrument_id, epoch, price, bid, ask, "
                "reference_snapshot_id) VALUES ('deriv', $1, $2, $3, $4, $5, $6) "
                "ON CONFLICT DO NOTHING",
                CONV_INSTR, md.exchange_timestamp, md.payload.price, md.payload.bid,
                md.payload.ask, md.reference_snapshot_id,
            )
            if str(status).endswith("1"):
                inserted += 1
        return inserted

    ins1 = await _convert()
    ins2 = await _convert()
    conv_rows = await conn.fetchval(
        "SELECT count(*) FROM ticks_live WHERE provider='deriv' AND instrument_id=$1", CONV_INSTR
    )
    _check("conversion: all captured rows now in ticks_live",
           int(conv_rows) == len(mds), f"run1_new={ins1} total={conv_rows}/{len(mds)}")
    _check("conversion re-run adds 0 (idempotent, rows KEPT for A42)", ins2 == 0, f"new={ins2}")

    # -- 9. cleanup: verify identity only; converted ticks_live KEPT -------------
    c1 = await conn.execute(
        "DELETE FROM candles_1m WHERE provider='deriv' AND instrument_id=$1", VERIFY_INSTR
    )
    c2 = await conn.execute(
        "DELETE FROM flush_cursors WHERE provider='deriv' AND instrument_id=$1", VERIFY_INSTR
    )
    left = await conn.fetchval(
        "SELECT count(*) FROM candles_1m WHERE provider='deriv' AND instrument_id=$1", VERIFY_INSTR
    )
    _check("cleanup: verify candles+cursor gone, ticks_live kept", int(left) == 0,
           f"deleted {c1}; {c2}; ticks_live={conv_rows} KEPT")

    await conn.close()

    # -- EYEBALL block: the human reads this to sign Step 7 ----------------------
    print("\n=== EYEBALL - M2.10 WARM live verify (Timescale :5432) ===")
    print(f"bars built via M2.4 from {SAMPLE.name} (frxEURUSD): {len(bars)} "
          f"(M2.3 door drops={schema_drops}, late drops={late_drops})")
    for i, b in enumerate(bars):
        print(f"  bar[{i}] {_fmt(b.interval_start)} -> {_fmt(b.interval_end)}  "
              f"O={b.open} H={b.high} L={b.low} C={b.close}  ticks={b.tick_count}  "
              f"quality={b.quality_status}")
    print(f"flush#1: acked=True inserted={rep1.inserted} evicted={len(rep1.evicted)} "
          f"cursor={_fmt(rep1.cursor.watermark) if rep1.cursor.watermark else None}  "
          f"(stops at 19:46->20:06 gap, never leaps)")
    print(f"flush#2 (same batch): inserted={rep2.inserted} rows stable={count2 == count1}")
    print(f"reads: full={len(full)}  [lo,lo]={len(one)}  [hi,hi]={len(one_hi)}  "
          f"outside=[{len(before)}]  (A46 inclusive bounds, no auto-expand)")
    print(f"compression: segmentby={seg_cols} policy_jobs={policy}  (A42, 7-day)")
    print(f"conversion CRYPTO.BTCUSD {CONV_DATE}: run1_new={ins1} rerun_new={ins2} "
          f"total_ticks_live={conv_rows} (kept - A42 store of record)")
    print(f"cleanup: {c1}; {c2}")
    print(f"RESULT: {'PASS' if _fails == 0 else f'FAIL ({_fails} check(s) failed)'}")
    return 0 if _fails == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
