"""Rewind the bulk-load cursor so the driver re-scans history.

WHY: BackfillDriver is an INCREMENTAL catch-up loader - with resume=True it only ever walks
FORWARD from backfill_cursors.reached. That is correct for keeping up, and wrong for a bulk
history load: with the cursor at the previous high-water mark it loaded only the ~17 hours
since the last run, however large --hours was.

Rewinding is safe and idempotent: the writer is INSERT ... ON CONFLICT DO NOTHING, so bars
already present are not duplicated, and the driver re-verifies them against the venue. The
cursor is loader progress, not lineage - no MarketData/reference_snapshot_id is touched.

Usage: python scripts/rewind_backfill_cursor.py --hours 720 [--top 25]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_universe_data import dsn

DERIV = "deriv"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # `--hours` is relative and therefore BLUNT: repairing one known-bad minute needs the
    # cursor placed a little BEFORE that minute, and "a bit before" is not expressible in
    # whole hours. Measured 2026-09-30: `--hours 636` intended to rewind past 2026-09-03
    # 21:03Z and landed on 23:51Z instead, so the re-request never covered the target
    # minute and the repair silently did nothing while still reporting a PARTIAL run.
    # `--until` places the cursor exactly, which is what a targeted repair requires.
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--hours", type=int, help="rewind this many hours back from now")
    group.add_argument("--until", help="absolute ISO-8601 UTC instant to rewind to, e.g. "
                                       "2026-09-03T20:55:00Z")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.hours is not None and args.hours <= 0:
        print("--hours must be > 0", file=sys.stderr)
        return 2
    if args.until is not None:
        raw = args.until.strip().replace("Z", "+00:00")
        try:
            target = datetime.fromisoformat(raw)
        except ValueError:
            print(f"--until must be ISO-8601, got {args.until!r}", file=sys.stderr)
            return 2
        if target.tzinfo is None:
            print("--until must carry a UTC offset (or a trailing Z); naive is rejected",
                  file=sys.stderr)
            return 2
        target = target.astimezone(UTC).replace(second=0, microsecond=0)
    else:
        # argparse's mutually-exclusive group guarantees the other branch was supplied;
        # the explicit check is here for the type checker and for a None that slips
        # through, and fails loudly rather than defaulting to a target nobody asked for.
        hours = args.hours
        if hours is None:
            print("give --hours or --until", file=sys.stderr)
            return 2
        target = (datetime.now(UTC) - timedelta(hours=float(hours))).replace(
            second=0, microsecond=0
        )
    selection = json.loads(
        (ROOT / "config" / "reference" / "universe_screen.json").read_text(encoding="utf-8")
    )
    from modules.reference.reference import to_canonical

    pairs = []
    for row in selection["selected"][: args.top]:
        try:
            pairs.append((row["native"], to_canonical(row["native"], DERIV)))
        except Exception as exc:  # noqa: BLE001 - per-instrument isolation: one bad symbol
            # must not abort the other 24, exactly as the loader itself does. Logged, not
            # swallowed, so a screen entry that stops resolving is visible instead of silent.
            print(f"  {row.get('native')!r:<14} SKIP unresolved: {type(exc).__name__}: {exc}")
            continue

    conn = await asyncpg.connect(
        dsn(), timeout=20
    )
    try:
        print(f"rewinding {len(pairs)} cursors to {target.isoformat()}"
              f"{'  (DRY RUN)' if args.dry_run else ''}")
        n = 0
        for native, instrument in pairs:
            if args.dry_run:
                print(f"  {native:<12} {instrument:<18} would rewind to {target:%Y-%m-%d %H:%M}Z")
                continue
            res = await conn.execute(
                "INSERT INTO backfill_cursors (instrument_id, broker, reached) "
                "VALUES ($1,$2,$3) ON CONFLICT (instrument_id, broker) DO UPDATE "
                "SET reached = EXCLUDED.reached, updated_at = now()",
                instrument, DERIV, target,
            )
            n += 1
            if not args.dry_run:
                print(f"  {native:<12} {instrument:<18} {res.split()[-1]}")
        print(f"RESULT: {'DRY RUN - ' if args.dry_run else ''}{n} cursor(s) at {target:%Y-%m-%d %H:%M}Z")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
