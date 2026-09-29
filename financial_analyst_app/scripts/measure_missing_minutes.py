"""MEASURE: which minutes are actually missing, and does the BROKER have them?

THE QUESTION THIS ANSWERS (asked 2026-09-29): the boundary bug drops one bar per
request. Is that a real hole in our data, or a minute the broker simply has no trade
in? The two demand opposite responses:

  * broker HAS a bar there  -> we lost real market data. OUR bug. Must be repaired.
  * broker has NOTHING     -> the minute is not missing data, it is a minute with no
                               tick in it. Fabricating a bar would be inventing a fact
                               (violates T9: a zero-tick bar is not data). LEAVE IT.

This script never writes. It only classifies, so the answer is safe to run against the
live dataset before deciding anything is deleted or repaired.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.m2_market_data.history import to_native  # noqa: E402

WS = "wss://api.derivws.com/trading/v1/options/ws/public"
MIN = timedelta(minutes=1)
NATIVE = {
    "FX.EURUSD": "frxEURUSD", "FX.GBPUSD": "frxGBPUSD", "FX.USDJPY": "frxUSDJPY",
    "FX.AUDUSD": "frxAUDUSD", "FX.USDCAD": "frxUSDCAD", "FX.NZDUSD": "frxNZDUSD",
    "FX.EURGBP": "frxEURGBP", "FX.EURJPY": "frxEURJPY",
}


def _password() -> str:
    for line in (Path(__file__).resolve().parents[2] / ".env").read_text(
        encoding="utf-8"
    ).splitlines():
        if line.startswith("TIMESCALE_DEV_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("TIMESCALE_DEV_PASSWORD missing")


async def broker_has_bars(
    ws: object, native: str, minutes: list[datetime], req_id: int
) -> dict[datetime, bool]:
    """Ask the broker, per missing minute, whether a candle EXISTS at that epoch.

    WHY one request per minute: `ticks_history` returns a RANGE, so a range query
    cannot distinguish "no bar at T" from "no bar anywhere in the range". Asking for a
    1-minute window at each T gives a per-minute yes/no. The range is requested
    [T-1m, T) because the broker EXCLUDES the candle at `start` - asking at T directly
    would report false absence.
    """
    out: dict[datetime, bool] = {}
    for t in minutes:
        await ws.send(json.dumps({
            "ticks_history": native, "style": "candles", "granularity": 60, "count": 1,
            "start": int((t - MIN).timestamp()), "end": str(int(t.timestamp())),
            "adjust_start_time": 1, "req_id": req_id,
        }))
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=25))
            if "error" in frame:
                out[t] = False
                break
            if frame.get("req_id") == req_id:
                out[t] = bool(frame.get("candles"))
                break
        req_id += 1
    return out


async def main() -> int:
    pool = await asyncpg.create_pool(
        f"postgresql://quantrader:{_password()}@127.0.0.1:5432/quantrader", timeout=20
    )
    rid = 5000
    totals = {"ours": 0, "brokers": 0}
    per_instrument: list[tuple[str, int, int]] = []
    try:
        async with websockets.connect(WS, open_timeout=25) as ws:
            for inst, native in NATIVE.items():
                rows = await pool.fetch(
                    "SELECT interval_start FROM candles_1m "
                    "WHERE instrument_id = $1 ORDER BY interval_start", inst
                )
                if not rows:
                    continue
                have = {r["interval_start"] for r in rows}
                lo, hi = min(have), max(have)
                # only look INSIDE the loaded span; the edges are where our requests began
                missing: list[datetime] = []
                t = lo + MIN
                while t < hi:
                    if t not in have:
                        missing.append(t)
                    t += MIN
                if not missing:
                    per_instrument.append((inst, 0, 0))
                    continue
                verdict = await broker_has_bars(ws, native, missing, rid)
                rid += len(missing) + 1
                ours = sum(1 for v in verdict.values() if v)
                brokers = len(missing) - ours
                totals["ours"] += ours
                totals["brokers"] += brokers
                per_instrument.append((inst, ours, brokers))
    finally:
        await pool.close()

    print("=" * 74)
    print("MISSING MINUTES INSIDE THE LOADED SPAN - ours vs the broker's")
    print("=" * 74)
    print(f"{'instrument':<14}{'ours lost':>11}{'broker empty':>15}")
    for inst, ours, brokers in per_instrument:
        print(f"{inst:<14}{ours:>11}{brokers:>15}")
    print("-" * 74)
    print(f"{'TOTAL':<14}{totals['ours']:>11}{totals['brokers']:>15}")
    print()
    print(f"OURS LOST   {totals['ours']:>6}  real market data we failed to store - a bug")
    print(f"BROKER EMPTY{totals['brokers']:>6}  no tick in that minute - NOT missing data")
    print()
    print("Only the first number may be repaired. The second must be left alone:")
    print("writing a bar there would invent a trade that never happened.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
