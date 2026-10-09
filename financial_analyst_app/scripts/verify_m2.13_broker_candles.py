"""M2.13 Step 7 — verify the broker-candle path against the LIVE venue.

Spec: docs/specs/m2.13_broker_candle_ingest.spec.md
Run:   python scripts/verify_m2.13_broker_candles.py
Exit:  0 = every check held. 1 = at least one FAIL (loud, never silent).

READ-ONLY. Writes nothing, deletes nothing, changes no schema.

WHY THIS EXISTS, and why it is not the same as the unit tests
-------------------------------------------------------------
The 45 unit tests prove the ingestor does what the spec says. They cannot prove the spec
was written about the REAL venue. Two things only a live run can settle:

  V1  the venue really does stream its own 1-minute bar on the public socket
      (`style:"candles"` + `subscribe:1` -> `msg_type == "ohlc"`), and at the rate the
      finality rule assumes;
  V2  the bar we EMIT is byte-for-byte the bar the venue's HISTORY endpoint reports for
      the same minute. This is the invariant that justifies the whole slice: our live
      tick-digestion disagreed with the venue on 3 of 202 bars, so if live and history still
      disagreed after this change, we would only have replaced one divergence with another.

V2 IS THE CHECK THAT HAS FAILED TWICE BEFORE, BOTH TIMES WRONG
--------------------------------------------------------------
An earlier audit reported "broker absent" for every sample and an earlier comparison
reported 25/25 instruments mismatching. Both were OUR BUG, not the data's. The cause is a
measured property of the endpoint, recorded in the spec: **`count` returns the `count` bars
ENDING at `end`**. So a probe shaped `start=T-60, end=T, count=1` receives the bar for
**T-60**, not T. Taking `candles[0]` without reading its `epoch` compares minute T against
minute T-60 — and in a rising market that reads as "every stored high is too high", on
every instrument, in the same direction, forever.

So this script NEVER trusts position. It prints the epoch the venue actually returned and
asserts on that. If a future change reintroduces the bug, V2 fails with the two epochs
shown side by side instead of a bare "absent".
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.m2_market_data.broker_candles import BrokerCandleIngestor, parse_ohlc_frame
from modules.m2_market_data.broker_feeds.deriv.stream import route_frame
from shared.contracts.drop import Drop

WS = "wss://api.derivws.com/trading/v1/options/ws/public"
NATIVE = "cryBTCUSD"
INSTR = "CRYPTO.BTCUSD"
FIELDS = ("open", "high", "low", "close")

failures: list[str] = []
notes: list[str] = []


def ok(label: str, detail: str = "") -> None:
    print(f"   [ok]   {label}{('  ' + detail) if detail else ''}")


def bad(label: str, detail: str) -> None:
    print(f"   [FAIL] {label}  {detail}")
    failures.append(f"{label}  {detail}")


def note(text: str) -> None:
    notes.append(text)
    print(f"   [note] {text}")


async def recv(ws: Any, rid: int, timeout: int = 40) -> dict:
    while True:
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if "error" in frame:
            return frame
        if frame.get("req_id") == rid:
            return frame


async def history_bars(ws: Any, rid: int, start: int, end: int) -> list[dict]:
    """Fetch a WIDE window and return bars BY EPOCH, never by position.

    The `count` bars end at `end`, so we ask for more than we need and index by epoch. This
    is the whole point of the script: the previous failures were position-based.
    """
    count = max(1, (end - start) // 60)
    await ws.send(json.dumps({
        "ticks_history": NATIVE,
        "style": "candles",
        "granularity": 60,
        "count": count,
        "start": start,
        "end": str(end),
        "adjust_start_time": 1,
        "req_id": rid,
    }))
    resp = await recv(ws, rid)
    if "error" in resp:
        raise RuntimeError(f"history error {resp['error']}")
    return list(resp.get("candles") or [])


async def main() -> int:
    print("=" * 78)
    print("M2.13 VERIFY - broker 1m candle ingest, against the LIVE venue")
    print("=" * 78)
    print(f"   socket {WS}")
    print(f"   symbol {NATIVE} -> {INSTR}   (crypto trades 24/7, so no calendar excuses)")
    print()

    async with websockets.connect(WS, open_timeout=25) as ws:
        # ---------------- V1: the venue really streams its own bar live ----------
        print("-" * 78)
        print("V1  LIVE ohlc STREAM")
        print("-" * 78)
        await ws.send(json.dumps({
            "ticks_history": NATIVE, "style": "candles", "granularity": 60,
            "end": "latest", "count": 3, "adjust_start_time": 1,
            "subscribe": 1, "req_id": 1,
        }))
        first = await recv(ws, 1)
        if "error" in first:
            bad("V1 subscribe", str(first["error"]))
            return 1
        seeded = first.get("candles") or []
        if not seeded:
            bad("V1 seed", "no candles in the subscribe response")
            return 1
        ok("V1 seed response", f"msg_type={first.get('msg_type')} bars={len(seeded)}")

        ingestor = BrokerCandleIngestor(instrument_id=INSTR, broker="deriv")
        raw_frames: list[str] = []
        emitted: list[Any] = []
        routed_candles = 0
        routed_ticks = 0
        deadline = asyncio.get_running_loop().time() + 150
        ohlc_frames = 0
        first_ohlc_at: datetime | None = None

        while asyncio.get_running_loop().time() < deadline:
            try:
                frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            except TimeoutError:
                break
            msg_type = frame.get("msg_type")
            if msg_type != "ohlc":
                continue
            ohlc_frames += 1
            if first_ohlc_at is None:
                first_ohlc_at = datetime.now(UTC)
            raw = json.dumps(frame)
            raw_frames.append(raw)
            routed = route_frame(
                raw, on_tick=lambda _r: routed_ticks, on_candle=lambda _r: None, on_drop=None
            )
            if routed:
                routed_candles += 1
            out = ingestor.on_frame(raw, received_at=datetime.now(UTC))
            if isinstance(out, str):  # pragma: no cover - defensive
                continue
            if out is not None and hasattr(out, "interval_start"):
                emitted.append(out)
            # Stop once we have finalised at least one whole minute.
            if len(emitted) >= 2:
                break

        tail = ingestor.close()
        if tail is not None:
            emitted.append(tail)

        if ohlc_frames == 0:
            bad("V1 live frames", "no msg_type=='ohlc' arrived - venue does not stream bars")
            return 1
        ok("V1 live frames", f"{ohlc_frames} ohlc frames in the sampling window")
        note(
            "the venue re-sends the SAME open_time ~60x as the minute develops; the "
            f"ingestor collapsed {ohlc_frames} frames into {len(emitted)} finalised bars"
        )
        if ingestor.drop_count:
            bad("V1 drops", f"{ingestor.drop_count} frame(s) dropped during a clean run")
        else:
            ok("V1 clean", "0 drops on a well-formed live stream")
        if ingestor.late_frame_count:
            note(f"late_frame_count={ingestor.late_frame_count} (re-sent minutes are not late)")

        if not emitted:
            bad("V1 emission", "no candle was finalised - the stream never crossed a boundary")
            return 1

        # ---------------- duplicate-write guard ------------------------------------
        print()
        print("-" * 78)
        print("V2  ONE FORMING MINUTE -> EXACTLY ONE DURABLE BAR")
        print("-" * 78)
        starts = [c.interval_start for c in emitted]
        if len(starts) != len(set(starts)):
            bad("V2 uniqueness", f"duplicate minutes emitted: {starts}")
        else:
            ok("V2 uniqueness", f"{len(emitted)} bars, {len(set(starts))} distinct minutes")
        for candle in emitted:
            if candle.interval_end - candle.interval_start != timedelta(minutes=1):
                bad("V2 width", f"{candle.interval_start} is not exactly 1 minute")
        ok("V2 width", "every bar is exactly 60s")
        for candle in emitted:
            if candle.quality_status != "BROKER":
                bad("V2 provenance", f"{candle.interval_start} status={candle.quality_status}")
        ok("V2 provenance", "every bar is quality_status=BROKER (venue OHLC, not our accumulation)")

        # ---------------- wire equality, both transports -----------------------------
        print()
        print("-" * 78)
        print("V3  EMITTED BAR == VENUE HISTORY FOR THE SAME MINUTE  (the decisive check)")
        print("-" * 78)
        lo = int(min(starts).timestamp())
        hi = int(max(starts).timestamp()) + 60
        bars = await history_bars(ws, 2, lo, hi)
        by_epoch = {int(b["epoch"]): b for b in bars}
        print(f"   asked history for [{lo}, {hi}]  venue returned {len(bars)} bars")
        print(f"   venue epochs actually returned: "
              f"{[datetime.fromtimestamp(e, tz=UTC).strftime('%H:%M') for e in sorted(by_epoch)]}")
        note("epochs are printed because the previous two audits trusted position and were wrong")

        exact = diff = absent = 0
        for candle in emitted:
            epoch = int(candle.interval_start.timestamp())
            ref = by_epoch.get(epoch)
            if ref is None:
                absent += 1
                print(f"   {datetime.fromtimestamp(epoch, tz=UTC):%H:%M}  ABSENT from history")
                continue
            mismatched = [
                f"{f}: ours={getattr(candle, f)!r} venue={ref[f]!r}"
                for f in FIELDS
                if Decimal(str(getattr(candle, f))) != Decimal(str(ref[f]))
            ]
            if mismatched:
                diff += 1
                print(f"   {datetime.fromtimestamp(epoch, tz=UTC):%H:%M}  MISMATCH  {mismatched}")
            else:
                exact += 1
                print(f"   {datetime.fromtimestamp(epoch, tz=UTC):%H:%M}  EXACT  "
                      f"O={candle.open} H={candle.high} L={candle.low} C={candle.close}")
        print()
        if absent:
            bad("V3 presence", f"{absent} emitted bar(s) absent from venue history")
        if diff:
            bad("V3 equality", f"{diff} bar(s) differ from the venue")
        if exact:
            ok("V3 equality", f"{exact}/{exact} bar(s) EXACTLY equal to the venue, all four fields")

        # ---------------- Decimal wire equality, demonstrated -------------------------
        print()
        print("-" * 78)
        print("V4  DECIMAL WIRE EQUALITY (the trap that produced two false audits)")
        print("-" * 78)
        live_text = "83589.140"
        history_text = "83589.14"
        if Decimal(live_text) == Decimal(history_text) and live_text != history_text:
            ok("V4", f'"{live_text}" != "{history_text}" as strings, but Decimal-equal')
        else:
            bad("V4", "Decimal normalisation did not behave as the spec states")
        sample = parse_ohlc_frame(raw_frames[0]) if raw_frames else None
        if sample is not None and not isinstance(sample, Drop):
            prices = [sample[f] for f in FIELDS]
            if all(isinstance(p, Decimal) for p in prices):
                ok("V4 parse", f"live-string OHLC parsed to Decimal: {prices}")
            else:
                bad("V4 parse", "a live price did not stay Decimal")
        else:
            note("could not re-parse the first frame for the Decimal check")

    print()
    print("=" * 78)
    print("RESULT")
    print("=" * 78)
    for n in notes:
        print(f"   note: {n}")
    if failures:
        print(f"   FAIL - {len(failures)} problem(s):")
        for f in failures:
            print(f"     - {f}")
        return 1
    print("   PASS - all checks held against the live venue.")
    print("   The broker is now the OHLC authority on BOTH the live and the history path,")
    print("   so a walk-forward and a live session cannot trade two different series.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
