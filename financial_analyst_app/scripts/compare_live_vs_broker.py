"""MEASURE: does our live tick->1m digestion agree with the broker's own 1m candle?

HUMAN QUESTION 2026-09-29: for live AND history, can we use the broker's 1-minute
candles directly instead of digesting ticks ourselves, to improve accuracy?

WHY THIS MATTERS (the asymmetry, not the aesthetic):
  * HISTORICAL bars come from `style=candles, granularity=60` -> DERIV's aggregation.
  * LIVE bars come from our `CandleBuilder` consuming ticks -> OUR aggregation.
  * BOTH are written to the SAME `candles_1m` table with ON CONFLICT DO NOTHING.

So a backtest trains on Deriv's series while live trading runs on ours, and nothing
reconciles them. If the two disagree, the walk-forward validates a different market
than we trade. If they agree exactly, the asymmetry is harmless and can be ignored.

Two parts:
  PART A  accuracy of our digestion: take every live-built bar (quality_status
          'COMPLETE' - the ones with a GENUINE tick_count, produced from ticks) and
          re-fetch the broker's candle for the same minute. Compare field by field.
  PART B  feasibility: can we subscribe to the BROKER's own live 1m stream
          (`style=candles` + `subscribe=1` -> `ohlc` frames)? If yes, live could use
          the same producer as history and the asymmetry would disappear entirely.

READ-ONLY. Writes nothing, deletes nothing, no schema change.
"""
from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WS = "wss://api.derivws.com/trading/v1/options/ws/public"
FIELDS = ("open", "high", "low", "close")


def _password() -> str:
    for line in (Path(__file__).resolve().parents[2] / ".env").read_text(
        encoding="utf-8"
    ).splitlines():
        if line.startswith("TIMESCALE_DEV_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("TIMESCALE_DEV_PASSWORD missing")


def _native_map() -> dict[str, str]:
    from modules.reference.reference import to_canonical

    path = (Path(__file__).resolve().parents[1]
            / "config" / "reference" / "universe_screen.json")
    screen = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for row in screen.get("selected", []):
        native = row.get("native")
        if not isinstance(native, str) or not native:
            continue
        try:
            out[to_canonical(native, "deriv")] = native
        except Exception:
            continue
    return out


async def broker_bar(ws: Any, native: str, t: int, rid: int) -> dict | None:
    """Broker's candle at epoch t.

    WHY the 3-bar window: a 1-minute request does NOT return the epoch you expect.
    MEASURED 2026-09-29:
        start=T-60 end=T   count=1 -> returns T-60   (not T)
        start=T   end=T+60 count=1 -> returns T
        start=T-60 end=T+60 count=3 -> returns T-60, T, T+60   <- provably contains T
    Two earlier audits filtered for an epoch the request never asked for correctly and
    reported every sample as "broker absent" - a query bug presented as missing data.
    The wide window plus an exact filter cannot misread it.
    """
    await ws.send(json.dumps({
        "ticks_history": native, "style": "candles", "granularity": 60, "count": 3,
        "start": t - 60, "end": str(t + 60),
        "adjust_start_time": 1, "req_id": rid,
    }))
    while True:
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
        if "error" in frame:
            ERRORS.append(f"{native}@{t}: {frame['error'].get('code')} "
                          f"{frame['error'].get('message')}")
            return None
        if frame.get("req_id") == rid:
            for c in frame.get("candles") or []:
                if int(c["epoch"]) == t:
                    return c
            return None


ERRORS: list[str] = []


async def part_a(pool: Any, native: dict[str, str]) -> dict[str, int]:
    """PART A: our live-built bars vs the broker's candle for the same minute."""
    rows = await pool.fetch(
        "SELECT instrument_id, interval_start, open, high, low, close, tick_count "
        "FROM candles_1m WHERE quality_status = 'COMPLETE' "
        "ORDER BY instrument_id, interval_start"
    )
    print("=" * 76)
    print(f"PART A   our live tick->1m digestion  vs  broker 1m candle  ({len(rows)} rows)")
    print("=" * 76)
    tally = {"match": 0, "diff": 0, "absent": 0, "unmapped": 0}
    shown = 0
    async with websockets.connect(WS, open_timeout=30) as ws:
        rid = 6000
        for r in rows:
            n = native.get(r["instrument_id"])
            if n is None:
                tally["unmapped"] += 1
                continue
            t = int(r["interval_start"].timestamp())
            got = await broker_bar(ws, n, t, rid)
            rid += 1
            await asyncio.sleep(0.06)
            if got is None:
                tally["absent"] += 1
                continue
            same = all(Decimal(str(got[f])) == Decimal(str(r[f])) for f in FIELDS)
            if same:
                tally["match"] += 1
            else:
                tally["diff"] += 1
                if shown < 6:
                    shown += 1
                    ours = " ".join(f"{f[0].upper()}{r[f]}" for f in FIELDS)
                    brk = " ".join(f"{f[0].upper()}{got[f]}" for f in FIELDS)
                    print(f"   DIFF {r['instrument_id']:<18} "
                          f"{r['interval_start']:%Y-%m-%d %H:%M}")
                    print(f"        ours {ours}  ticks={r['tick_count']}")
                    print(f"        brkr {brk}")
            await asyncio.sleep(0.06)
    compared = tally["match"] + tally["diff"]
    print(f"   exact matches        {tally['match']}")
    print(f"   MISMATCHES           {tally['diff']}")
    print(f"   broker absent        {tally['absent']}")
    print(f"   not compared (map)   {tally['unmapped']}")
    if compared:
        print(f"   agreement            "
              f"{100.0 * tally['match'] / compared:.1f}% of {compared} compared")
    else:
        print("   agreement            NOT MEASURED (0 bars retrieved)")
    for e in ERRORS[:3]:
        print(f"   broker error: {e}")
    return tally


async def part_b() -> None:
    """PART B: can we subscribe to the BROKER's own live 1m candle stream?"""
    print()
    print("=" * 76)
    print("PART B   broker LIVE 1m stream (style=candles + subscribe=1 -> ohlc)")
    print("=" * 76)
    try:
        async with websockets.connect(WS, open_timeout=20) as ws:
            await ws.send(json.dumps({
                "ticks_history": "cryBTCUSD", "style": "candles",
                "granularity": 60, "end": "latest", "count": 3,
                "adjust_start_time": 1, "subscribe": 1, "req_id": 7000,
            }))
            got_first = False
            deadline = asyncio.get_running_loop().time() + 25
            while asyncio.get_running_loop().time() < deadline:
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=25))
                msg = frame.get("msg_type")
                if "error" in frame:
                    print(f"   ERROR {frame['error'].get('code')}: "
                          f"{frame['error'].get('message')}")
                    return
                if not got_first:
                    print(f"   first frame  msg_type={msg} "
                          f"bars={len(frame.get('candles') or [])}")
                    got_first = True
                    continue
                if msg == "ohlc":
                    o = frame.get("ohlc") or {}
                    print(f"   LIVE ohlc frame keys: {sorted(o.keys())}")
                    print(f"   sample: {json.dumps(o)[:300]}")
                    extra = sorted(set(o.keys()) - {"epoch", "open", "high",
                                                    "low", "close"})
                    print(f"   non-OHLC fields: {extra if extra else 'NONE'}")
                    print("   -> PART B YES: broker streams its own 1m candle live.")
                    return
                print(f"   frame msg_type={msg}")
            print("   -> PART B: no ohlc frame within 25s (market/feed dependent)")
    except Exception as exc:
        print(f"   -> PART B FAILED: {type(exc).__name__}: {exc}")


async def main() -> int:
    native = _native_map()
    pool = await asyncpg.create_pool(
        f"postgresql://quantrader:{_password()}@127.0.0.1:5432/quantrader", timeout=30
    )
    try:
        tally = await part_a(pool, native)
    finally:
        await pool.close()
    await part_b()

    print()
    print("=" * 76)
    print("VERDICT")
    print("=" * 76)
    compared = tally["match"] + tally["diff"]
    if compared == 0:
        print("   PART A: NOT MEASURED - no bar was successfully re-fetched.")
        print("      The asymmetry remains untested. Not a clean yes.")
    elif tally["diff"] == 0 and tally["absent"] == 0:
        print(f"   PART A: our digestion and the broker's candle agree EXACTLY")
        print(f"      on all {compared} comparable live bars, 0 mismatches.")
        print("      -> switching live to broker 1m would change NOTHING numerically.")
        print("      The real benefit would be ONE producer for live + history,")
        print("      removing a divergence risk rather than fixing a live defect.")
    else:
        print(f"   PART A: {tally['diff']} MISMATCHES out of {compared} compared"
              f" ({tally['absent']} unretrievable).")
        if tally["diff"]:
            print("      -> live and history are NOT the same series. Backtest and")
            print("         live trade on different OHLC for the same minute.")
            print("         That is an accuracy defect, not a style preference.")
    if tally["unmapped"]:
        print(f"   note: {tally['unmapped']} rows skipped (instrument not in "
              f"universe config)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
