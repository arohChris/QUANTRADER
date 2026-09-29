"""
Reset the TEST market-data store. Clears `candles_1m` AND `backfill_cursors` together.

WHY THIS SCRIPT EXISTS (human directive, 2026-09-29: *"this are for testing sake ... when
we are done building we are going to delete all this data and start afresh"):

  A stale `backfill_cursors` row makes the M2.7 driver a SILENT NO-OP. `run()` loads the
  cursor, and `reached >= end` returns `status="complete"` with **0 requests** - a
  clean-looking success that loads nothing. This is not hypothetical: CRYPTO.BTCUSD
  finished a 120h universe load holding 2,030 bars while every peer held 4,133, purely
  because its cursor had already advanced from an earlier 24h run.

  So the two tables MUST be cleared together. Clearing only `candles_1m` looks fine
  (the table is empty!) and then every subsequent load does nothing while reporting
  success. That is the failure mode this script exists to make impossible.

SAFETY - deliberately conservative, because two real data losses already happened on
2026-09-27 from unverified destructive commands:
  * `--yes` is REQUIRED. Nothing is deleted without it.
  * `--dry-run` prints the exact SQL and the row counts that WOULD be deleted, and
    touches nothing. Run it first, every time.
  * The counts are printed BEFORE the delete, so a surprise is visible, and the
    post-delete state is re-queried and printed, so "it worked" is verified rather than
    assumed.
  * `ticks_live` is NOT touched. It is a separate concern and out of scope for a candle
    reset; pass `--include-ticks-live` to clear it deliberately.
  * Schema is NOT dropped. The hypertable, its compression settings and its chunk
    configuration survive, so a reset does not silently undo A42 (compression ON,
    >=1yr retention, NO auto-prune). Dropping and recreating would.

Run:  python scripts/reset_test_data.py --dry-run
      python scripts/reset_test_data.py --yes
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg

#: Order matters for readability, not for correctness - these are independent tables.
TABLES = (
    ("candles_1m", "the loaded 1-minute bars"),
    ("backfill_cursors", "the resume cursors - THE ONE THAT CAUSES SILENT NO-OPS"),
)


def _password() -> str:
    env = Path(__file__).resolve().parents[2] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("TIMESCALE_DEV_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("TIMESCALE_DEV_PASSWORD missing from .env")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--yes", action="store_true", help="actually delete (required)")
    ap.add_argument("--dry-run", action="store_true", help="print what WOULD be deleted")
    ap.add_argument("--include-ticks-live", action="store_true")
    args = ap.parse_args()

    if not args.yes and not args.dry_run:
        print("Refusing to do nothing. Pass --dry-run to preview, or --yes to delete.")
        return 2

    tables = list(TABLES)
    if args.include_ticks_live:
        tables.append(("ticks_live", "raw captured ticks (only with --include-ticks-live)"))

    conn = await asyncpg.connect(
        f"postgresql://quantrader:{_password()}@127.0.0.1:5432/quantrader", timeout=20
    )
    try:
        print("=" * 78)
        print("RESET TEST MARKET DATA")
        print("=" * 78)
        print("  candles_1m + backfill_cursors are cleared TOGETHER, always.")
        print("  Schema, compression and chunk config are NOT dropped (A42 preserved).")
        print()

        counts: dict[str, int] = {}
        for name, why in tables:
            n = int(await conn.fetchval(f"SELECT count(*) FROM {name}"))
            counts[name] = n
            print(f"  {name:<20} {n:>9,} rows   # {why}")
        print()

        if not args.yes:
            print("DRY RUN - nothing was deleted. SQL that WOULD run:")
            for name, _ in tables:
                print(f"    DELETE FROM {name};")
            print()
            print("Re-run with --yes to perform the reset.")
            return 0

        total = 0
        for name, _ in tables:
            # One transaction across every table: a half-done reset is exactly the state
            # that causes the silent no-op, so it must not be representable.
            async with conn.transaction():
                status = await conn.execute(f"DELETE FROM {name}")
            deleted = int(status.split()[-1])
            total += deleted
            print(f"  DELETED {name:<20} {deleted:>9,} rows")

        print()
        remaining: dict[str, int] = {}
        for name, _ in tables:
            remaining[name] = int(await conn.fetchval(f"SELECT count(*) FROM {name}"))
        print("  post-reset state (verified, not assumed):")
        for name, _ in tables:
            print(f"    {name:<20} {remaining[name]:>9,} rows")

        still = {k: v for k, v in remaining.items() if v}
        print()
        if still:
            print(f"RESULT: PARTIAL - not empty: {still}")
            return 1
        print(f"RESULT: PASS - {total:,} rows cleared across {len(tables)} table(s).")
        print("The next universe load will start from zero and issue real requests.")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
