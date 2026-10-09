"""Sync the backfill cursor to where our data actually ends.

WHY this exists. `backfill_cursors.reached` is loader PROGRESS, and
`rewind_backfill_cursor.py` deliberately moves it backwards so a bulk load re-scans
history. That is correct for a re-scan and dangerous afterwards: a 250-day load was
rewound to 2026-01-23 and then killed, which left 25 cursors between 2026-01-23 and
2026-04-02 while our data still began at 2026-08-31 for 24 of 25 instruments. The next
incremental run would therefore have resumed from January and attempted the whole
250-day load again - the kill would have silently undone itself on the next tick.

So the cursor must be reconciled against the DATA FRONTIER, which is the newest bar we
actually hold. Doing that by hand is a 25-row UPDATE that a future session would have to
redo correctly by eye, so it is a script and it is auditable.

This NEVER moves a cursor forward past data we do not have, and never deletes a bar. It
only raises a cursor that is behind its own instrument's newest bar. Anything else is a
bug and is reported rather than silently changed.

Idempotent: running it twice changes nothing the second time.

Usage: python scripts/sync_backfill_cursor_to_data.py [--apply] [--dry-run]
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

DERIV = "deriv"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write; without it this is a dry run")
    args = ap.parse_args()

    conn = await asyncpg.connect(
        dsn(), timeout=20
    )
    try:
        rows = await conn.fetch(
            """
            SELECT c.instrument_id,
                   c.reached                       AS cursor_reached,
                   d.newest                        AS data_newest,
                   CASE WHEN c.reached < d.newest THEN 'behind' ELSE 'ok' END AS verdict
            FROM backfill_cursors c
            JOIN (
                SELECT instrument_id, max(interval_start) AS newest
                FROM candles_1m
                WHERE provider = $1
                GROUP BY instrument_id
            ) d ON d.instrument_id = c.instrument_id
            WHERE c.broker = $1
            ORDER BY c.instrument_id
            """,
            DERIV,
        )
        if not rows:
            print("no cursors joined to data - nothing to reconcile")
            return 0

        behind = [r for r in rows if r["verdict"] == "behind"]
        print(f"{len(rows)} cursor(s); {len(behind)} behind their own data frontier")
        for r in rows:
            if r["verdict"] == "ok":
                continue
            print(
                f"  {r['instrument_id']:<18} cursor {r['cursor_reached']:%Y-%m-%d %H:%M}Z"
                f"  ->  data frontier {r['data_newest']:%Y-%m-%d %H:%M}Z"
            )
        if not behind:
            print("RESULT: all cursors already at their data frontier - no change needed")
            return 0
        if not args.apply:
            print("RESULT: DRY RUN - re-run with --apply to raise these cursors")
            return 0

        n = 0
        for r in behind:
            res = await conn.execute(
                "UPDATE backfill_cursors SET reached = $3, updated_at = now() "
                "WHERE instrument_id = $1 AND broker = $2",
                r["instrument_id"], DERIV, r["data_newest"],
            )
            n += int(res.split()[-1])
        print(f"RESULT: raised {n} cursor(s) to their data frontier")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
