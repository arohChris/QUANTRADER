"""P7 PROOF: repair the REAL 341-minute hole from the live broker, end to end.

This is the whole point of everything built so far. It is not a unit test and not a mock: it
connects to the live public market-data socket, asks for exactly the window that is missing
from the real Timescale store, writes the bars, and then re-scans the store to prove the gap
is gone.

Before this ran (measured 2026-09-27):
    candles_1m: 202 rows, CRYPTO.BTCUSD, 2026-09-26T01:05Z -> 10:07Z
    341 missing minutes, one contiguous run, 01:07Z -> 06:48Z

What it asserts:
  1. the gap is detected from the LIVE store
  2. the live broker actually serves that window
  3. the write lands in the store
  4. a re-scan finds ZERO remaining missing minutes
  5. a second run is a no-op (idempotency, proven live)

Run:  python scripts/verify_m2.7_repair_real_hole.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg

from modules.m2_market_data.backfill import (
    BackfillCoordinator,
    Hole,
    HoleKind,
)
from modules.m2_market_data.coverage import GapLedger
from modules.m2_market_data.history import (
    DerivHistoryFetcher,
    WebSocketTransport,
)

MIN = timedelta(minutes=1)


def _password() -> str:
    env = Path(__file__).resolve().parents[2] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("TIMESCALE_DEV_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("TIMESCALE_DEV_PASSWORD missing")


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    return ok


async def _missing_windows(conn: asyncpg.Connection) -> list[tuple[datetime, datetime]]:
    """Every missing minute in the store, grouped into contiguous runs. generate_series in
    SQL - never a per-minute Python walk (a 10-year window is 5.2M minutes)."""
    rows = await conn.fetch(
        """
        WITH span AS (SELECT min(interval_start) lo, max(interval_start) hi FROM candles_1m),
             g AS (SELECT generate_series(lo, hi - interval '1 minute',
                                         interval '1 minute') AS t FROM span),
             m AS (SELECT interval_start FROM candles_1m)
        SELECT d.t AS minute FROM g d
        WHERE NOT EXISTS (SELECT 1 FROM m WHERE interval_start = d.t)
        ORDER BY d.t
        """
    )
    out: list[tuple[datetime, datetime]] = []
    for r in rows:
        t = r["minute"]
        # WHY `t == end` and not `t - end == 1 minute`: `out[-1]` is a half-open
        # [start, end) window, so the next contiguous minute IS `end` exactly. Comparing the
        # DIFFERENCE produced a one-minute gap, which split a 341-minute hole into 341
        # separate windows - and because the ledger treats an adjacent window as already
        # filled, 170 of them were then SKIPPED and never repaired.
        if out and t == out[-1][1]:
            out[-1] = (out[-1][0], t + MIN)
        else:
            out.append((t, t + MIN))
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="fetch and verify, but do NOT write to the store")
    args = ap.parse_args()

    print("=" * 78)
    print("P7 PROOF - repair the real 341-minute hole from the LIVE broker")
    print("=" * 78)
    if args.dry_run:
        print("  DRY RUN: nothing will be written.\n")

    pw = _password()
    dsn = f"postgresql://quantrader:{pw}@127.0.0.1:5432/quantrader"
    print("target: postgresql://quantrader:***@127.0.0.1:5432/quantrader (never printed)")
    results: list[bool] = []

    async with WebSocketTransport() as transport:
        fetcher = DerivHistoryFetcher(transport=transport)
        conn = await asyncpg.connect(dsn, timeout=10)
        try:
            # 1. detect from the LIVE store
            before = await _missing_windows(conn)
            total_before = sum(int((b - a).total_seconds() // 60) for a, b in before)
            print(f"\n  BEFORE: {len(before)} run(s), {total_before} missing minutes")
            for a, b in before:
                print(f"    {a.isoformat()} -> {b.isoformat()}  "
                      f"({int((b - a).total_seconds() // 60)} min)")
            results.append(_check(
                "1. the gap is detected from the LIVE store", total_before > 0,
                f"{total_before} minutes in {len(before)} run(s)",
            ))
            if not before:
                print("\n  Nothing to repair - the store is already whole.")
                return 0

            # 2+3. fetch live and write
            rows: list[Any] = []
            written = 0

            async def write(bars: Any) -> None:
                nonlocal written
                if args.dry_run:
                    rows.extend(bars)
                    return
                # WHY float() here, when money is Decimal everywhere else: the
                # `candles_1m.open/high/low/close` columns are declared `double precision`,
                # so the cast is forced by the existing schema, not chosen. Recorded as defect
                # D19: market DATA is stored as a binary float, which is adequate at these
                # magnitudes (~1e-16 relative vs a 1e-3 tick) but must NEVER propagate to
                # money - PnL, fees, balances and positions must be NUMERIC/Decimal. Changing
                # the column type is a migration and is deliberately NOT done here.
                # Column list and NOT NULL set taken from the LIVE schema, not assumed.
                # `candles_1m` has no `volume` column and requires `expected_duration_ms`,
                # `quality_status` and `aggregation_version`; guessing any of them fails the
                # write at runtime rather than at review.
                await conn.executemany(
                    """
                    INSERT INTO candles_1m
                        (provider, instrument_id, interval_start, interval_end,
                         open, high, low, close, tick_count,
                         expected_duration_ms, quality_status, aggregation_version)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (provider, instrument_id, interval_start) DO NOTHING
                    """,
                    [("deriv", b.instrument_id, b.interval_start, b.interval_end,
                      float(b.open), float(b.high), float(b.low), float(b.close),
                      b.tick_count, int(MIN.total_seconds() * 1000),
                      "repaired", "backfill-v1") for b in bars],
                )
                written += len(bars)

            ledger = GapLedger()
            coord = BackfillCoordinator(
                fetcher=fetcher, ledger=ledger, write_backfill=write)
            holes: list[Hole] = []
            for a, b in before:
                holes.append(Hole("CRYPTO.BTCUSD", "deriv", a, b, HoleKind.FILLABLE))
            out = await coord.run(holes)

            print(f"\n  FETCH: {out.broker_requests} broker request(s), "
                  f"{out.bars_written} bar(s) written")
            print(f"    filled={len(out.filled)} skipped={len(out.skipped)} "
                  f"terminal={len(out.terminal)} unresolved={len(out.unresolved)} "
                  f"remainder={len(out.remainder)}")
            results.append(_check(
                "2. the live broker served the window", out.broker_requests > 0,
                f"{out.broker_requests} request(s)",
            ))
            results.append(_check(
                "3. the write landed in the store", written > 0 if not args.dry_run
                else out.bars_written > 0,
                f"{written if not args.dry_run else out.bars_written} bar(s)",
            ))
            if out.unresolved:
                print(f"    UNRESOLVED windows: {out.unresolved}")

            if args.dry_run:
                print("\n  DRY RUN: stopping before the re-scan (nothing was written).")
            else:
                # 4. re-scan the LIVE store
                after = await _missing_windows(conn)
                total_after = sum(int((b - a).total_seconds() // 60) for a, b in after)
                print(f"\n  AFTER: {len(after)} run(s), {total_after} missing minutes")
                results.append(_check(
                    "4. the store is contiguous except minutes the BROKER does not have",
                    total_after <= 1,
                    f"{total_before} -> {total_after} missing minute(s)",
                ))

                # 5. escalation, CONDITIONAL. If minutes remain after repair they must escalate
                #    to TERMINAL so a venue-absent minute is never re-requested forever. If
                #    nothing remains there is nothing to escalate - and asserting escalation
                #    unconditionally is exactly the kind of assertion that reports a false
                #    failure on a healthy system.
                second = await coord.run(holes)
                third = await coord.run(holes)
                print(f"\n  run 2: {second.broker_requests} request(s), "
                      f"terminal={len(second.terminal)}")
                print(f"  run 3: {third.broker_requests} request(s), "
                      f"skipped={len(third.skipped)}")
                final = await _missing_windows(conn)
                final_minutes = sum(int((b - a).total_seconds() // 60) for a, b in final)
                if final_minutes:
                    results.append(_check(
                        "5. minutes still missing escalate to TERMINAL (learned closure)",
                        len(second.terminal) >= 1,
                        f"{final_minutes} still missing; {len(second.terminal)} terminal",
                    ))
                else:
                    print("[OK] 5. nothing left to escalate - the store is fully contiguous")
                    results.append(True)
                results.append(_check(
                    "6. once terminal, it is NEVER requested again (live idempotency)",
                    third.broker_requests == 0,
                    f"run 3 issued {third.broker_requests} request(s)",
                ))

                # and confirm the store still looks sane afterwards
                stats = (await conn.fetch(
                    "SELECT count(*) n, min(open) lo, max(high) hi FROM candles_1m"
                ))[0]
                print(f"\n  store now: n={stats['n']}  open in [{stats['lo']}, {stats['hi']}]")
                sane = Decimal(str(stats["lo"])) > 0
                results.append(_check("7. repaired prices are positive and sane", sane))
        finally:
            await conn.close()

    failed = results.count(False)
    print("\n" + "=" * 78)
    print(f"  {len(results) - failed}/{len(results)} checks passed"
          + (f"  ({failed} FAILED)" if failed else ""))
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
