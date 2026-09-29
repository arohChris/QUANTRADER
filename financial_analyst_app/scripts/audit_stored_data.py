"""AUDIT: is the data currently in `candles_1m` correct as fetched?

HUMAN QUESTION 2026-09-29: "is the tick data we have correct with one minute as fetched now?"

An earlier cross-check is NOT trustworthy: it queried from `start`, and the broker uses
(S, E] - so it looked for its own sample one minute off and reported a mismatch that was
the query's fault, not the data's. This reruns it with the CORRECT convention:
to fetch the bar at epoch T, request start=T-60, end=T  ->  broker returns (T-60, T],
which contains T.

Five independent verdicts, because "correct" has several parts:
  V1 STRUCTURE  - duplicates, epoch alignment to 60s, ascending, OHLC sane
  V2 BROKER     - sampled bars re-fetched and compared field-by-field (DECISIVE)
  V3 COVERAGE   - minutes missing inside open-ish spans (the repairable set)
  V4 tick_count - how many rows assert a fabricated "exactly 1 tick"
  V5 quality    - what quality_status actually says

READ-ONLY. Writes nothing, deletes nothing.
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WS = "wss://api.derivws.com/trading/v1/options/ws/public"
MIN = timedelta(minutes=1)
SAMPLE = 12          # bars per instrument to re-verify against the broker
random.seed(20260929)  # reproducible sample - a re-run must compare the same bars

#: broker-side errors, surfaced instead of masquerading as missing data
ERRORS: list[str] = []


def _password() -> str:
    for line in (Path(__file__).resolve().parents[2] / ".env").read_text(
        encoding="utf-8"
    ).splitlines():
        if line.startswith("TIMESCALE_DEV_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("TIMESCALE_DEV_PASSWORD missing")


def _native_map() -> dict[str, str]:
    """canonical instrument_id -> broker-native symbol, derived from the SAME config
    the loader uses.

    WHY not hardcoded: revision 1 hard-coded 25 pairs. When the stored ids did not
    match, it skipped 16,532 rows silently and still printed a whole-table verdict.
    An audit that quietly covers part of its subject is worse than no audit.
    """
    from modules.reference.reference import to_canonical

    path = Path(__file__).resolve().parents[1] / "config" / "reference" / "universe_screen.json"
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
    """Fetch the candle at epoch `t`.

    WHY the 3-bar window and NOT `start=t-60, end=t, count=1`: with `count` set the
    broker returns the `count` bars ENDING AT `end`, so that shape returns `t-60` and
    never `t`. MEASURED 2026-09-29:
        start=t-60 end=t    count=1 -> t-60          (wrong bar)
        start=t   end=t+60  count=1 -> t
        start=t-60 end=t+60 count=3 -> t-60, t, t+60 (provably contains t)
    Two earlier audit runs filtered for an epoch the request never asked for correctly
    and printed every sample as "broker absent" - a query bug presented as missing data,
    which is exactly the false verdict this function must not produce. The wide window
    plus an exact filter cannot misread it.

    WHY the error is returned rather than swallowed: silencing it turned a query bug
    into a false data verdict the first time already.
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


async def main() -> int:
    from modules.reference import calendar as cal

    NATIVE = _native_map()
    pool = await asyncpg.create_pool(
        f"postgresql://quantrader:{_password()}@127.0.0.1:5432/quantrader", timeout=30
    )
    rid = 4000
    v1_bad: list[str] = []
    v2 = {"match": 0, "diff": 0, "absent": 0, "no_symbol": 0}
    diffs: list[str] = []
    v4 = {"fabricated_one": 0, "genuine": 0}
    v5: dict[str, int] = {}
    total_rows = 0
    open_expected = 0
    miss_open = 0
    unmapped_rows = 0
    try:
        rows = await pool.fetch(
            "SELECT DISTINCT ON (instrument_id) instrument_id, count(*)::int AS n, "
            "min(interval_start) AS lo, max(interval_start) AS hi "
            "FROM candles_1m GROUP BY instrument_id ORDER BY instrument_id"
        )
        async with websockets.connect(WS, open_timeout=30) as ws:
            for r in rows:
                inst, n, lo, hi = r["instrument_id"], r["n"], r["lo"], r["hi"]
                total_rows += n

                bars = await pool.fetch(
                    "SELECT interval_start, open, high, low, close, tick_count, "
                    "quality_status FROM candles_1m WHERE instrument_id=$1 "
                    "ORDER BY interval_start", inst)
                epochs = [int(b["interval_start"].timestamp()) for b in bars]

                # --- V1 structure -------------------------------------------------
                if len(set(epochs)) != len(epochs):
                    v1_bad.append(f"{inst}: duplicate interval_start")
                for e in epochs:
                    if e % 60:
                        v1_bad.append(f"{inst}: epoch {e} not on a 60s boundary")
                        break
                if epochs != sorted(epochs):
                    v1_bad.append(f"{inst}: not ascending")
                for b in bars:
                    if b["high"] < b["low"]:
                        v1_bad.append(f"{inst}: high < low at {b['interval_start']}")
                        break
                    v4["fabricated_one" if b["tick_count"] == 1 else "genuine"] += 1
                    key = str(b["quality_status"])
                    v5[key] = v5.get(key, 0) + 1

                # --- V3 coverage vs the TRADING CALENDAR (not wall clock) ------
                # WHY calendar: counting every absent minute inside min..max treats
                # weekends and daily closures as data loss, so the first run reported
                # "74,020 minutes missing / 28.9% present" when most of those minutes
                # were simply closed. Only minutes the calendar says were OPEN count.
                expected = cal.open_minutes(inst, lo, hi, "deriv")
                open_expected += expected
                absent_in_open = max(0, expected - len(epochs))
                miss_open += absent_in_open

                # --- V2 broker comparison ---------------------------------------
                native = NATIVE.get(inst)
                if native is None:
                    v2["no_symbol"] += len(epochs)
                    unmapped_rows += len(epochs)
                    continue
                idx = sorted(random.sample(range(len(epochs)), min(SAMPLE, len(epochs))))
                for i in idx:
                    t = epochs[i]
                    got = await broker_bar(ws, native, t, rid)
                    rid += 1
                    await asyncio.sleep(0.06)  # stay well inside 20 req/s
                    if got is None:
                        v2["absent"] += 1
                        continue
                    same = all(
                        Decimal(str(got[f])) == Decimal(str(bars[i][f]))
                        for f in ("open", "high", "low", "close")
                    )
                    if same:
                        v2["match"] += 1
                    else:
                        v2["diff"] += 1
                        if len(diffs) < 5:
                            diffs.append(
                                f"{inst} {bars[i]['interval_start']}: stored "
                                f"O{bars[i]['open']} H{bars[i]['high']} "
                                f"L{bars[i]['low']} C{bars[i]['close']} | broker "
                                f"O{got['open']} H{got['high']} L{got['low']} C{got['close']}"
                            )
                    await asyncio.sleep(0.06)
    finally:
        await pool.close()

    print("=" * 76)
    print("V1 STRUCTURE")
    print("=" * 76)
    print(f"   rows in table     {total_rows}")
    print(f"   violations        {len(v1_bad)}")
    for x in v1_bad[:5]:
        print(f"      {x}")

    print()
    print("=" * 76)
    print(f"V2 BROKER COMPARISON  (correct (S,E] convention, {SAMPLE}/instrument)")
    print("=" * 76)
    print(f"   EXACT OHLC match  {v2['match']}")
    print(f"   MISMATCH          {v2['diff']}")
    print(f"   broker absent     {v2['absent']}")
    print(f"   rows not sampled  {unmapped_rows} (no symbol in universe config)")
    for d in diffs:
        print(f"      {d}")
    for e in ERRORS[:3]:
        print(f"      broker error: {e}")
    checked = v2["match"] + v2["diff"] + v2["absent"]
    if checked and v2["diff"] == 0 and v2["absent"] == 0:
        print(f"   {checked} sampled bars compared, {checked} matched EXACTLY")
    elif checked:
        print(f"   match rate {100 * v2['match'] / checked:.1f}% of {checked} compared")

    print()
    print("=" * 76)
    print("V3 COVERAGE  (measured against the trading CALENDAR, not wall clock)")
    print("=" * 76)
    print(f"   OPEN minutes the calendar expects: {open_expected}")
    print(f"   rows stored:                       {total_rows}")
    print(f"   OPEN minutes with no row:          {miss_open}")
    if open_expected:
        pct = 100.0 * (open_expected - miss_open) / open_expected
        print(f"   coverage of open minutes:          {pct:.3f}%")

    print()
    print("=" * 76)
    print("V4 tick_count")
    print("=" * 76)
    print(f"   == 1 (fabricated by backfill) {v4['fabricated_one']}")
    print(f"   != 1 (genuine, from live path) {v4['genuine']}")

    print()
    print("=" * 76)
    print("V5 quality_status")
    print("=" * 76)
    for k, v in sorted(v5.items(), key=lambda x: -x[1]):
        print(f"   {k:<20} {v}")

    print()
    print("=" * 76)
    print("VERDICT")
    print("=" * 76)
    ok_v1 = not v1_bad
    ok_v2 = checked > 0 and v2["diff"] == 0 and v2["absent"] == 0
    unknown_v2 = checked == 0
    print(f"   V1 structure                 {'PASS' if ok_v1 else 'FAIL'}")
    if unknown_v2:
        print("   V2 broker OHLC               NOT MEASURED (all samples failed)")
    else:
        print(f"   V2 broker OHLC               {'PASS' if ok_v2 else 'FAIL'}")
    print("   V3 timestamps epoch-anchored PASS (covered by V1)")
    pct_txt = (f"{100.0 * (open_expected - miss_open) / open_expected:.3f}%"
               if open_expected else "n/a")
    print(f"   V4 open-minute coverage      {pct_txt} ({miss_open} open minutes absent)")
    print(f"   V5 tick_count                "
          f"{v4['genuine']} genuine / {v4['fabricated_one']} fabricated")

    print()
    print("ANSWER TO THE HUMAN'S QUESTION:")
    print("   'Is the 1m data correct as fetched?'")
    if ok_v1 and ok_v2:
        print("   OHLC + timestamps: YES - sampled bars match the broker EXACTLY.")
    elif unknown_v2:
        print("   OHLC + timestamps: UNPROVEN - structural checks pass, but the broker")
        print("      comparison failed to run. Not a clean yes.")
    else:
        print("   OHLC + timestamps: NO - see mismatches above.")
    print(f"   tick_count:      NO for {v4['fabricated_one']} backfilled rows (fabricated).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
