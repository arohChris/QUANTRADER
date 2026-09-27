"""Step 7 real-data verification for M2b.4 (trading calendar) — against REAL broker truth.

WHY this exists (AGENTS.md 2 Step 7): the calendar decides whether a market was tradable at an
instant. Every downstream number - gaps, indicators, backtests - inherits that answer, so a
wrong session boundary does not produce a wrong candle, it produces a wrong SYSTEM.

Two independent sources of real truth are used, deliberately:

  A. OFFLINE, FULLY REPRODUCIBLE - the 100 committed real Deriv tick frames under
     `data/live_capture/` and `data/live_e2e/`. These are broker-captured ticks spanning
     Fri 2026-09-25T23:11Z -> Sat 2026-09-26T01:06Z. The Saturday tail is the valuable part:
     a weekday-only calendar would call Saturday 01:06 "shut", yet the broker demonstrably
     traded there. Real ticks at a real instant are the strongest possible statement that the
     market was open, and this check is re-runnable offline forever.

  B. LIVE BROKER CROSS-CHECK - Deriv's own `exchange_is_open` for the five real assets at the
     current instant, compared with the calendar. This is the only source that can catch the
     calendar being STALE (a holiday we have not declared, a venue changing hours). It needs
     the network, so it DEGRADES GRADIENTLY and reports honestly rather than failing the run
     when the network is unavailable - a verify script that cries wolf when offline is worse
     than one that says "could not check".

Secrets: the Deriv token is read from REPO/.env and never printed (AGENTS.md 3.7).
Run:  python scripts/verify_m2b.4_calendar.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.reference.calendar import broker_agrees, is_open, open_minutes

REPO = Path(__file__).resolve().parents[2]
APP = Path(__file__).resolve().parents[1]

# (canonical instrument, broker-native symbol) for the five shipped assets.
REAL_ASSETS = [
    ("CRYPTO.BTCUSD", "cryBTCUSD"),
    ("FX.EURUSD", "frxEURUSD"),
    ("FX.GBPUSD", "frxGBPUSD"),
    ("COMMODITY.XAUUSD", "frxXAUUSD"),
    ("COMMODITY.XAGUSD", "frxXAGUSD"),
]


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    return ok


def _env_value(key: str) -> str | None:
    env = REPO / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def _real_tick_timestamps() -> list[tuple[str, datetime]]:
    """Every real captured tick: (native symbol, exchange timestamp)."""
    out: list[tuple[str, datetime]] = []
    for pattern in ("data/live_capture/**/*.jsonl", "data/live_e2e/**/*.jsonl"):
        for path in APP.glob(pattern):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                tick = frame.get("tick")
                if isinstance(tick, dict) and "epoch" in tick and "symbol" in tick:
                    out.append((tick["symbol"], datetime.fromtimestamp(tick["epoch"], tz=UTC)))
    return out


async def _live_exchange_is_open() -> dict[str, bool] | None:
    """Ask Deriv, per symbol, whether the market is open right now. None if unreachable.

    WHY this needs NO CREDENTIALS (corrected 2026-09-27): `active_symbols` is documented
    `Auth: not required` and carries `exchange_is_open` per symbol. An earlier revision of
    this script skipped the staleness cross-check entirely because the demo token was an
    empty placeholder - which was the WRONG reason. The cross-check was always available
    without authentication, so the gap was self-inflicted and is now closed.
    """
    from modules.m2_market_data.history import (
        WebSocketTransport,
        loads_money_safe,
    )

    try:
        async with WebSocketTransport() as ws:
            frame = await ws({"active_symbols": "brief"})
        out: dict[str, bool] = {}
        for entry in frame.get("active_symbols", []):
            native = entry.get("underlying_symbol")
            if native is None:
                continue
            out[native] = bool(entry.get("exchange_is_open"))
        return out or None
    except Exception:  # noqa: BLE001 - offline or broker-side; reported honestly below
        _ = loads_money_safe  # referenced so the import is meaningful
        return None


async def main() -> int:
    results: list[bool] = []
    print("=" * 78)
    print("M2b.4 STEP 7 - trading calendar against real broker truth")
    print("=" * 78)

    # -- A. real captured ticks: ticks exist => the market was open --------------------
    ticks = _real_tick_timestamps()
    print(f"\n  A. OFFLINE real tick frames: {len(ticks)}")
    if not ticks:
        results.append(_check("A1. real tick frames found", False, "none found - cannot verify"))
    else:
        syms = sorted({s for s, _ in ticks})
        lo, hi = min(t for _, t in ticks), max(t for _, t in ticks)
        print(f"     symbols={syms}")
        print(f"     range  ={lo.isoformat()} -> {hi.isoformat()}")
        sat = [t for _, t in ticks if t.weekday() == 5]
        print(f"     weekend frames = {len(sat)} (a weekday-only calendar would call these SHUT)")

        native_to_canon = {n: c for c, n in REAL_ASSETS}
        wrong = [
            (n, t) for n, t in ticks
            if native_to_canon.get(n) and not is_open(native_to_canon[n], t, broker="deriv")
        ]
        results.append(_check(
            "A1. every real captured tick falls in TRADABLE hours",
            not wrong,
            f"{len(ticks) - len(wrong)}/{len(ticks)} in-session"
            + (f"; offenders: {wrong[:3]}" if wrong else ""),
        ))
        results.append(_check(
            "A2. Saturday ticks are OPEN (24/7 asset, not shut)",
            bool(sat) and not any(not is_open("CRYPTO.BTCUSD", t) for t in sat),
            f"{len(sat)} weekend frames, all open",
        ))

    # -- B. session arithmetic a human can check by eye ---------------------------------
    print("\n  B. Session arithmetic (hand-checkable):")
    mon = datetime(2026, 9, 21, tzinfo=UTC)   # Monday
    week = mon + timedelta(days=7)
    expected = {"CRYPTO.BTCUSD": 168 * 60, "FX.EURUSD": 120 * 60,
                "COMMODITY.XAUUSD": 119 * 60, "COMMODITY.XAGUSD": 119 * 60}
    for inst, exp in expected.items():
        got = open_minutes(inst, mon, week, broker="deriv")
        results.append(_check(
            f"B1. {inst} open minutes in a Mon-Sun week",
            got == exp, f"{got} min (expect {exp} = {exp // 60}h)",
        ))
    sat_noon = datetime(2026, 9, 19, 12, tzinfo=UTC)
    results.append(_check(
        "B2. Saturday: FX and metals SHUT, crypto OPEN",
        (not is_open("FX.EURUSD", sat_noon)) and (not is_open("COMMODITY.XAUUSD", sat_noon))
        and is_open("CRYPTO.BTCUSD", sat_noon),
    ))
    results.append(_check(
        "B3. Sunday 21:00 UTC forex OPENS (the boundary a naive probe gets wrong)",
        is_open("FX.EURUSD", datetime(2026, 9, 20, 21, 0, tzinfo=UTC))
        and not is_open("FX.EURUSD", datetime(2026, 9, 20, 20, 59, tzinfo=UTC)),
    ))

    # -- C. live broker cross-check ----------------------------------------------------
    print("\n  C. LIVE broker cross-check (active_symbols.exchange_is_open, no auth):")
    live = await _live_exchange_is_open()
    if live is None:
        print("     [SKIP] broker unreachable from this environment (network/proxy).")
        print("     NOT COVERED BY THIS RUN: calendar STALENESS. No credential is involved -")
        print("     this endpoint needs none - so a skip here is purely a network condition.")
    else:
        now = datetime.now(tz=UTC)
        disagreements: list[str] = []
        for canon, native in REAL_ASSETS:
            if native not in live:
                continue
            cal = is_open(canon, now, broker="deriv")
            msg = broker_agrees(canon, now, calendar_open=cal, broker_open=live[native])
            state = "agrees" if msg is None else "DISAGREES"
            print(f"     {canon:18s} calendar={cal!s:5s} broker={live[native]!s:5s}  {state}")
            if msg:
                disagreements.append(msg)
        results.append(_check(
            "C1. calendar agrees with the LIVE broker on all assets",
            not disagreements, "; ".join(disagreements) or f"full agreement at {now.isoformat()}",
        ))

    failed = results.count(False)
    print("\n" + "=" * 78)
    print(f"  {len(results) - failed}/{len(results)} checks passed"
          + (f"  ({failed} FAILED)" if failed else ""))
    if live is None:
        print("  1 area SKIPPED for a network reason, not a credential one: the live broker")
        print("  staleness cross-check. No token is involved - see section C above.")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
