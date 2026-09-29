"""PROBE: can we backfill from TICKS instead of pre-built candles?

THE IDEA (human, 2026-09-29): `style: "candles"` carries no tick_count and no volume, so
our backfill fabricates `tick_count=1` for every bar and `tick_rate` returns a constant
1.0 over 104k rows. Instead of failing closed on that, fetch RAW TICKS and build the 1m
candle ourselves - through the SAME CandleBuilder the live path already uses
(`candles.py:140` increments tick_count per tick). Then the backfilled data has a GENUINE
tick count and no fabricated field.

This decides whether that is feasible. Four things must hold, each measured:

  Q1 do ticks work for a HISTORICAL window (start/count), or only live?
  Q2 does a tick carry `volume`? (would give us a second genuine field)
  Q3 DATA COST - how many ticks per 1m window vs 1 candle?
  Q4 CROSS-VALIDATION - do our ticks, aggregated to 1m, reproduce the OHLC that
     `style: "candles"` returns? If not, the two paths disagree and we would be
     changing historical data by switching. This is the make-or-break check.

Read-only, one instrument, deleted after use.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import websockets

URL = "wss://api.derivws.com/trading/v1/options/ws/public"
MIN = timedelta(minutes=1)


async def recv(ws: object, rid: int) -> dict:
    while True:
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=40))
        if "error" in frame:
            return {"error": frame["error"]}
        if frame.get("req_id") == rid:
            return frame


async def main() -> int:
    # One closed hour, midweek, liquid - all 60 minutes are definitely open.
    start = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)
    end = start + timedelta(minutes=60)

    async with websockets.connect(URL, open_timeout=25) as ws:
        rid = 9000

        # ---- Q1 + Q2: ticks for a historical window --------------------------
        print("=" * 74)
        print(f"Q1/Q2  TICKS  frxEURUSD  {start:%a %Y-%m-%d} 10:00 -> 11:00 UTC")
        print("=" * 74)
        await ws.send(json.dumps({
            "ticks_history": "frxEURUSD", "style": "ticks",
            "count": 5000,
            "start": int(start.timestamp()),
            "end": str(int(end.timestamp())),
            "req_id": rid,
        }))
        resp = await recv(ws, rid)
        rid += 1
        if "error" in resp:
            print(f"   ERROR {resp['error']}")
            return 0
        ticks = resp.get("ticks") or resp.get("history") or []
        print(f"   response keys   {sorted(resp.keys())}")
        print(f"   tick fields     {sorted(ticks[0].keys()) if ticks else 'NONE'}")
        if ticks:
            print(f"   sample          {json.dumps(ticks[0])}")
            fields = sorted(ticks[0].keys())
            has_vol = any("vol" in f.lower() for f in fields)
            print(f"   HAS VOLUME      {has_vol}   <- Q2")
            print(f"   ticks returned  {len(ticks)}")
            in_window = [t for t in ticks
                         if start.timestamp() <= int(t["epoch"]) < end.timestamp()]
            print(f"   ticks in window {len(in_window)}   <- Q3 cost basis")
        else:
            print("   NO TICKS")
            return 0

        # ---- Q4: aggregate ticks to 1m, compare against candle endpoint ------
        print()
        print("=" * 74)
        print("Q4  CROSS-VALIDATION  ticks->1m  vs  style=candles")
        print("=" * 74)
        await ws.send(json.dumps({
            "ticks_history": "frxEURUSD", "style": "candles", "granularity": 60,
            "count": 60, "start": int(start.timestamp()), "end": str(int(end.timestamp())),
            "adjust_start_time": 1, "req_id": rid,
        }))
        cred = await recv(ws, rid)
        rid += 1
        candles = cred.get("candles") or []

        # build our own 1m bars from ticks
        buckets: dict[int, dict[str, Decimal]] = {}
        counts: dict[int, int] = {}
        for t in ticks:
            ep = int(t["epoch"])
            if not (start.timestamp() <= ep < end.timestamp()):
                continue
            minute = ep // 60 * 60
            q = Decimal(str(t.get("quote") or t.get("close") or t.get("bid")))
            b = buckets.get(minute)
            if b is None:
                buckets[minute] = {"open": q, "high": q, "low": q, "close": q}
                counts[minute] = 1
            else:
                b["high"] = max(b["high"], q)
                b["low"] = min(b["low"], q)
                b["close"] = q
                counts[minute] += 1

        print(f"   candle endpoint  {len(candles)} bars")
        print(f"   our tick-built   {len(buckets)} bars")
        print(f"   {'minute':<12}{'src O':>11}{'our O':>11}{'src H':>11}{'our H':>11}"
              f"{'src L':>11}{'our L':>11}{'src C':>11}{'our C':>11}{'ticks':>7}")
        exact = 0
        diff = 0
        for c in candles:
            ep = int(c["epoch"])
            mine = buckets.get(ep)
            if mine is None:
                print(f"   {ep:<12} missing from tick build")
                diff += 1
                continue
            src_o, src_h = Decimal(str(c["open"])), Decimal(str(c["high"]))
            src_l, src_c = Decimal(str(c["low"])), Decimal(str(c["close"]))
            match = (src_o == mine["open"] and src_h == mine["high"]
                     and src_l == mine["low"] and src_c == mine["close"])
            if match:
                exact += 1
            else:
                diff += 1
            if diff <= 6:  # show the first few either way
                print(f"   {datetime.fromtimestamp(ep, tz=UTC):%H:%M} "
                      f"{src_o:>11}{mine['open']:>11}{src_h:>11}{mine['high']:>11}"
                      f"{src_l:>11}{mine['low']:>11}{src_c:>11}{mine['close']:>11}"
                      f"{counts[ep]:>7}{'  OK' if match else '  DIFF'}")

        print()
        print(f"   EXACT matches   {exact}/{len(candles)}")
        print(f"   mismatches      {diff}")
        if exact == len(candles) and candles:
            print("   -> Q4 PASS: tick aggregation reproduces candle OHLC exactly.")
            print("      Switching to ticks is safe AND gives a genuine tick_count.")
        elif diff:
            print("   -> Q4 FAIL: the two sources disagree.")
            print("      Switching would CHANGE historical data. Do not switch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
