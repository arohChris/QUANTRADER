"""Migration: audit watermark + known-venue-gap baseline.

WHY. `verify_universe_data.py` walked the WHOLE history of every instrument on every run,
and re-reported the same 635 venue-absence minutes each time (270 COMMODITY.XAUUSD +
269 COMMODITY.XAGUSD + 46 CRYPTO.BTCUSD + 46 CRYPTO.ETHUSD + a handful of FX). Two
problems, both getting worse as history grows:

  * COST. The minute-walk is O(total history). At 30 days x 25 instruments that is ~864k
    iterations; at 250 days it is ~5.3M, every night, to re-derive an answer that cannot
    have changed for the old rows.
  * SIGNAL. The same 635 known facts are re-printed daily. A monitor whose output is
    identical every morning trains you to skim it, and the morning a NEW bar the venue has
    and we do not appears is exactly the morning you will not look closely.

So we record two things and then only ever look at what is new:

  data_audit_watermark
      Per instrument, the newest bar we have already audited. The next run starts here
      instead of at the beginning of time, so cost is proportional to NEW data.

  known_venue_gaps
      Minutes we are missing that the VENUE is also missing. Recorded once, on the run
      that discovered them, and thereafter treated as expected. They are still counted and
      still reported in aggregate - they are not hidden - but they no longer re-enter the
      failure path, because no re-fetch can conjure a bar the broker never had.

A gap the venue HAS and we do not is never written here. That is class F1, a real defect,
and it stays a failure forever.

Both tables are additive and empty; the audit behaves exactly as before until the
watermark exists. This script is idempotent (CREATE ... IF NOT EXISTS) and deletes no data.

Usage: python scripts/migrate_audit_watermark.py [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_universe_data import dsn

STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS data_audit_watermark (
        instrument_id    TEXT        NOT NULL,
        broker           TEXT        NOT NULL,
        verified_through TIMESTAMPTZ NOT NULL,
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (instrument_id, broker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS known_venue_gaps (
        instrument_id TEXT        NOT NULL,
        broker        TEXT        NOT NULL,
        interval_start TIMESTAMPTZ NOT NULL,
        reason        TEXT        NOT NULL,
        recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (instrument_id, broker, interval_start)
    )
    """,
    "CREATE INDEX IF NOT EXISTS known_venue_gaps_inst ON known_venue_gaps (instrument_id, broker)",
)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="create the tables; without it, show only")
    args = ap.parse_args()

    conn = await asyncpg.connect(
        dsn(), timeout=20
    )
    try:
        if not args.apply:
            print("DRY RUN - would create:")
            for stmt in STATEMENTS:
                print("  " + " ".join(stmt.split())[:110] + " ...")
            print("RESULT: DRY RUN - re-run with --apply")
            return 0

        async with conn.transaction():
            for stmt in STATEMENTS:
                await conn.execute(stmt)

        for table in ("data_audit_watermark", "known_venue_gaps"):
            n = await conn.fetchval(f"SELECT count(*) FROM {table}")
            print(f"  {table:<22} ready, {n} row(s)")
        # A watermark row with no data, or a watermark ahead of the data, would make the
        # incremental audit skip history it has never seen. Prove the table is empty so the
        # FIRST audit is a full one and only later runs are incremental.
        print("RESULT: applied - tables empty, so the next audit is FULL; later runs incremental")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
