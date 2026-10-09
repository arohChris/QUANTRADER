"""Trim one instrument's history back to a cutoff, leaving a contiguous panel.

WHY this exists. A 250-day bulk load was rewound to 2026-01-23 and then killed part-way
through. That left COMMODITY.XAUUSD with TWO disjoint segments and a 151-day hole between
them:

    2026-01-23 -> 2026-04-02   (from the killed run)
    2026-04-02 -> 2026-08-31   MISSING - ~151 days
    2026-08-31 -> 2026-09-30   (the original 30-day load)

This is the dangerous shape of bad data. It is not obviously corrupt: row counts look
plausible, lineage is intact, there are no duplicates, and a naive `min`/`max` span check
reports a healthy 8-month history. But the 5-month hole is indistinguishable from a
market closure, so any indicator warmed across it - an EMA200, an ATR, a rolling z-score -
would be silently computed over a discontinuity and produce plausible garbage. Audit
class B2 reported it as 146,297 "missing minutes", which is how it was caught.

The fix chosen by the human (2026-09-30) is to drop the pre-cutoff segment and keep a
clean, contiguous, uniform 30-day panel across all 25 instruments, rather than spend hours
backfilling one instrument to 8 months while the other 24 have one month.

SAFETY. This deletes rows, so it is deliberately hard to do by accident:
  * dry run unless --apply is passed;
  * refuses a cutoff that would leave fewer than --keep-min rows, so a typo cannot
    empty an instrument;
  * prints the exact row count and time range that would go, BEFORE anything is removed;
  * writes what it removed to a JSON manifest, so the operation is recorded rather than
    merely asserted;
  * runs the delete in a single transaction with the count re-checked inside it, so a
    concurrent writer cannot make the reported number a lie;
  * touches candles_1m only. It never edits backfill_cursors - use
    scripts/sync_backfill_cursor_to_data.py afterwards so the cursor matches the data.

Usage:
    python scripts/trim_instrument_history.py --instrument COMMODITY.XAUUSD \\
        --before 2026-08-31T00:00:00Z [--apply] [--keep-min 1000]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_universe_data import dsn

MANIFEST_DIR = ROOT / "logs" / "trims"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str) -> datetime:
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise SystemExit(f"cutoff must be ISO-8601, got {value!r}") from None
    if parsed.tzinfo is None:
        raise SystemExit("cutoff must carry a UTC offset (or a trailing Z); naive is rejected")
    return parsed.astimezone(UTC)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--instrument", required=True)
    ap.add_argument("--before", required=True, help="ISO-8601 UTC; rows strictly before this go")
    ap.add_argument("--apply", action="store_true", help="write; without it this is a dry run")
    ap.add_argument("--keep-min", type=int, default=1000,
                    help="refuse if fewer than this many rows would remain")
    args = ap.parse_args()

    cutoff = _parse(args.before)
    conn = await asyncpg.connect(
        dsn(), timeout=20
    )
    try:
        span = await conn.fetchrow(
            "SELECT count(*) AS total, min(interval_start) AS lo, max(interval_start) AS hi "
            "FROM candles_1m WHERE instrument_id = $1",
            args.instrument,
        )
        if not span or not span["total"]:
            print(f"no rows for {args.instrument} - nothing to trim")
            return 0

        doomed = int(await conn.fetchval(
            "SELECT count(*) FROM candles_1m "
            "WHERE instrument_id = $1 AND interval_start < $2",
            args.instrument, cutoff,
        ))
        keep = int(span["total"]) - doomed

        print(f"instrument : {args.instrument}")
        print(f"  holds    : {span['total']:,} rows, {_iso(span['lo'])} -> {_iso(span['hi'])}")
        print(f"  cutoff   : {_iso(cutoff)}  (rows STRICTLY before this are removed)")
        print(f"  removing : {doomed:,} rows")
        print(f"  keeping  : {keep:,} rows")

        if doomed == 0:
            print("RESULT: nothing before the cutoff - no change needed")
            return 0
        if keep < args.keep_min:
            print(f"RESULT: REFUSED - would leave only {keep:,} rows, below --keep-min "
                  f"{args.keep_min:,}. Check the cutoff; refusing to empty the instrument.",
                  file=sys.stderr)
            return 2
        if not args.apply:
            print("RESULT: DRY RUN - re-run with --apply to delete")
            return 0

        # One transaction, and the count is re-checked inside it. If a concurrent writer
        # added or removed rows since the count above, the rollback happens instead of us
        # reporting a number that is no longer true.
        async with conn.transaction():
            live = int(await conn.fetchval(
                "SELECT count(*) FROM candles_1m "
                "WHERE instrument_id = $1 AND interval_start < $2",
                args.instrument, cutoff,
            ))
            if live != doomed:
                print(f"RESULT: REFUSED - row count changed under us "
                      f"({doomed:,} -> {live:,}); nothing deleted, re-run to re-plan",
                      file=sys.stderr)
                return 3
            await conn.execute(
                "DELETE FROM candles_1m WHERE instrument_id = $1 AND interval_start < $2",
                args.instrument, cutoff,
            )

        after = await conn.fetchrow(
            "SELECT count(*) AS total, min(interval_start) AS lo, max(interval_start) AS hi "
            "FROM candles_1m WHERE instrument_id = $1",
            args.instrument,
        )
        print(f"  now      : {after['total']:,} rows, {_iso(after['lo'])} -> {_iso(after['hi'])}")

        MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        manifest = MANIFEST_DIR / f"trim_{args.instrument}_{stamp}.json"
        manifest.write_text(json.dumps({
            "instrument": args.instrument,
            "cutoff": _iso(cutoff),
            "rows_removed": doomed,
            "rows_remaining": int(after["total"]),
            "span_before": {"lo": _iso(span["lo"]), "hi": _iso(span["hi"])},
            "span_after": {"lo": _iso(after["lo"]), "hi": _iso(after["hi"])},
            "reason": (
                "151-day hole between two disjoint segments left by a killed 250-day load; "
                "removed the pre-cutoff segment to restore a contiguous uniform 30-day panel"
            ),
        }, indent=2) + "\n", encoding="utf-8")
        print(f"  manifest : {manifest.relative_to(ROOT)}")
        print("RESULT: trimmed - now run scripts/sync_backfill_cursor_to_data.py --apply")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
