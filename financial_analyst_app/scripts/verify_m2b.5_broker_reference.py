"""Boot-time reference verification: OUR CONFIG vs THE LIVE BROKER. Mismatch = fail the boot.

Human decision 2026-09-27: **broker `pip_size` is authoritative; our config must agree, and a
mismatch is a boot failure** - not a warning.

WHY a hard failure. This check exists because it was needed: on 2026-09-27 it would have
caught two real money defects that no unit test could have found, because the wrong value was
in our own config and our tests were written against our own config:

    CRYPTO.BTCUSD    tick_size 0.01   vs broker 0.001    10x too coarse
    COMMODITY.XAGUSD  tick_size 0.01   vs broker 0.0001   100x too coarse

`tick_size` is the quantum every price is rounded to, so a 100x-coarse tick on Silver does not
add noise - it discards two orders of magnitude of the real price, and every indicator, gap and
backtest downstream inherits it invisibly. A warning would have been read once and ignored.

It also verifies, from the same no-auth public endpoint:
  * every shipped instrument actually EXISTS at the broker (we must not trade a fiction);
  * `is_trading_suspended`, so a suspended market is visible before we try to repair it;
  * `exchange_is_open` against our calendar, which is the staleness cross-check that was
    previously reported as unverifiable;
  * `trading_times.trading_days` against our weekday model.

NO CREDENTIALS REQUIRED. `active_symbols` and `trading_times` are `Auth: not required`.
Token handling is deliberately absent from this file so there is no secret to leak.

Run:  python scripts/verify_m2b.5_broker_reference.py [--offline]
Exit: 0 = config agrees with the broker. 1 = it does not; the boot must not proceed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import websockets

from modules.reference.calendar import is_open
from modules.reference.reference import get_instrument, to_native

WS_URL = "wss://api.derivws.com/trading/v1/options/ws/public"
FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "deriv"
WEEKDAY = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


def _ok(label: str, good: bool, detail: str = "") -> bool:
    print(f"[{'OK' if good else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    return good


async def _call(request: dict[str, Any], *, timeout: float = 25.0) -> dict[str, Any]:
    async with websockets.connect(WS_URL, open_timeout=15, close_timeout=3) as ws:
        await ws.send(json.dumps(request))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            # parse_float=Decimal so a JSON number never becomes a lossy Python float
            frame = json.loads(raw, parse_float=Decimal)
            if "error" in frame:
                raise RuntimeError(f"broker error: {frame['error']}")
            if frame.get("msg_type") in ("active_symbols", "trading_times"):
                return frame


def _pip(entry: dict[str, Any]) -> Decimal:
    value = entry["pip_size"]
    return value if isinstance(value, Decimal) else Decimal(str(value))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true",
                    help="use the committed real response instead of the live socket")
    args = ap.parse_args()

    print("=" * 78)
    print("BROKER REFERENCE VERIFICATION - config vs live broker")
    print("=" * 78)

    if args.offline:
        symbols = json.loads((FIXTURES / "active_symbols_brief.json").read_text(encoding="utf-8"),
                             parse_float=Decimal)["active_symbols"]
        times = json.loads((FIXTURES / "trading_times_today.json").read_text(encoding="utf-8"))
        source = "committed real fixtures"
    else:
        symbols = (await _call({"active_symbols": "brief"}))["active_symbols"]
        times = (await _call({"trading_times": "today"}))["trading_times"]
        source = WS_URL
    print(f"  source: {source}\n")

    by_native = {s["underlying_symbol"]: s for s in symbols}
    results: list[bool] = []

    # 1. every shipped instrument exists at the broker
    shipped = sorted(get_instrument(i)["asset_class"] and i
                     for i in _all_instrument_ids())
    missing = [i for i in shipped if to_native(i, "deriv") not in by_native]
    results.append(_ok(
        "1. every shipped instrument exists at the broker",
        not missing, f"{len(shipped)} shipped" + (f"; MISSING {missing}" if missing else ""),
    ))

    # 2. tick_size == pip_size  (the check that was needed)
    wrong: list[str] = []
    for instrument_id in shipped:
        entry = by_native.get(to_native(instrument_id, "deriv"))
        if entry is None:
            continue
        ours = get_instrument(instrument_id)["tick_size"]
        theirs = _pip(entry)
        mark = "" if ours == theirs else "   <-- MISMATCH"
        print(f"     {instrument_id:18s} ours={ours!s:<9} broker={theirs!s:<9}{mark}")
        if ours != theirs:
            wrong.append(f"{instrument_id}: ours {ours} vs broker {theirs}")
    results.append(_ok(
        "2. tick_size equals the broker's pip_size for EVERY instrument",
        not wrong, "; ".join(wrong) or "full agreement",
    ))

    # 3. suspension visibility
    suspended = [i for i in shipped
                 if by_native.get(to_native(i, "deriv"), {}).get("is_trading_suspended")]
    results.append(_ok(
        "3. no shipped instrument is suspended at the broker",
        not suspended, f"suspended: {suspended}" if suspended else "none suspended",
    ))

    # 4. calendar agreement right now (the staleness cross-check)
    from datetime import UTC, datetime

    now = datetime.now(tz=UTC)
    disagree: list[str] = []
    for instrument_id in shipped:
        entry = by_native.get(to_native(instrument_id, "deriv"))
        if entry is None:
            continue
        broker_open = bool(entry["exchange_is_open"])
        ours = is_open(instrument_id, now, broker="deriv")
        if ours is not broker_open:
            disagree.append(f"{instrument_id}: calendar={ours} broker={broker_open} at "
                            f"{now.isoformat()}")
    results.append(_ok(
        "4. our calendar agrees with the broker's exchange_is_open right now",
        not disagree, "; ".join(disagree) or f"full agreement at {now.isoformat()}",
    ))

    # 5. trading_days agree with our weekday model
    tt: dict[str, dict[str, Any]] = {}
    for market in times.get("markets", []):
        for sub in market.get("submarkets", []):
            for s in sub.get("symbols", []):
                tt[s["underlying_symbol"]] = s
    day_problems: list[str] = []
    for instrument_id in shipped:
        s = tt.get(to_native(instrument_id, "deriv"))
        if not s:
            continue
        broker_days = {WEEKDAY[d] for d in s.get("trading_days", []) if d in WEEKDAY}
        # a 7-day set means 24/7, which our class standard expresses as always_open
        ours_247 = get_instrument(instrument_id)["asset_class"] == "crypto"
        if len(broker_days) == 7 and not ours_247:
            day_problems.append(f"{instrument_id}: broker trades 7 days, our model is session-based")
        if ours_247 and len(broker_days) < 7:
            day_problems.append(f"{instrument_id}: broker trades {len(broker_days)} days, "
                                f"our model says 24/7")
    results.append(_ok(
        "5. broker trading_days agree with our weekday model",
        not day_problems, "; ".join(day_problems) or "full agreement",
    ))

    failed = results.count(False)
    print("\n" + "=" * 78)
    print(f"  {len(results) - failed}/{len(results)} checks passed")
    if failed:
        print("  CONFIG AND BROKER DISAGREE. The boot must not proceed: a wrong tick_size or a")
        print("  wrong session corrupts every price or every repair, silently and downstream.")
    print("=" * 78)
    return 1 if failed else 0


def _all_instrument_ids() -> list[str]:
    from modules.reference.reference import _MASTER

    return sorted(_MASTER)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
