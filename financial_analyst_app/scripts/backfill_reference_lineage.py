"""Backfill `candles_1m.reference_snapshot_id` for rows the bulk loader left NULL.

WHY THIS EXISTS (measured 2026-09-30)
-----------------------------------
`scripts/load_universe.py`'s `PgWriter` INSERT did not name the column, so 796,331 of
796,333 rows carried `reference_snapshot_id = NULL`. Every OTHER writer stamps lineage -
`normalize.py:98` DROPS a tick whose snapshot id is empty ("lineage mandatory") and the
WARM path carries it through `pipeline.py` - so the bulk backfill was the one writer
producing rows that cannot be attributed to an instrument spec.

`AGENTS.md` 3.4 requires the snapshot id on venue data for reproducibility and 4.4 requires
it on every row for replay. Without it, editing `config/assets/*.yaml` makes a replay of this
history unreproducible - and this project has already been bitten by a live `tick_size`
0.01-vs-0.001 defect that silently truncated live prices to 2dp. Knowing WHICH spec was in
force is the only way to bound that class of bug after the fact.

WHY THE ATTRIBUTION IS HONEST, AND WAS CHECKED RATHER THAN ASSUMED
-------------------------------------------------------------------
Stamping today's snapshot onto old rows is only defensible if that snapshot was the one in
force when the rows were FETCHED, because the spec describes how we interpreted the venue's
prices, not when they traded. Verified before running:

  * `config/reference/snapshot.yaml` has exactly ONE version in git history (commit
    5309c8a, 2026-09-25) - `REF-2026-09-25-001`, effective_from 2026-09-25T00:00:00Z.
  * Every row in the table was WRITTEN on or after 2026-09-26, i.e. after effective_from.
    The write-date histogram is printed by `--dry-run` so this stays checkable.

So every row was interpreted under `REF-2026-09-25-001` and saying so is a fact, not a guess.

SAFETY
------
* Only `NULL` rows are touched; a row that already carries a snapshot is never overwritten,
  so this is idempotent and cannot destroy a more specific attribution.
* `--dry-run` prints the plan and counts and writes nothing.
* The DDL is untouched - this is a data repair, not a schema change.

Usage: python scripts/backfill_reference_lineage.py [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from modules.reference.reference import current_snapshot_id


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="report the plan, write nothing")
    args = ap.parse_args()

    snapshot_id = current_snapshot_id()
    if not snapshot_id:
        print(
            "REFUSING: current_snapshot_id() returned None. config/reference/snapshot.yaml "
            "must carry a valid REF-YYYY-MM-DD-NNN id. Writing rows with no lineage is "
            "exactly the defect this script exists to remove.",
            file=sys.stderr,
        )
        return 2
    print(f"snapshot in force: {snapshot_id}")

    conn = await asyncpg.connect(
        f"postgresql://quantrader:{_password()}@127.0.0.1:5432/quantrader", timeout=30
    )
    try:
        total = await conn.fetchval("SELECT count(*) FROM candles_1m")
        nulls = await conn.fetchval(
            "SELECT count(*) FROM candles_1m WHERE reference_snapshot_id IS NULL"
        )
        stamped = Counter(
            {
                (r["reference_snapshot_id"] or "<NULL>"): r["n"]
                for r in await conn.fetch(
                    "SELECT reference_snapshot_id, count(*) n FROM candles_1m GROUP BY 1"
                )
            }
        )
        print(f"rows total {total}, NULL lineage {nulls}, already stamped {total - nulls}")
        for k, v in sorted(stamped.items(), key=lambda kv: -kv[1]):
            print(f"    {k:<24} {v}")

        print("\nwrite-date histogram (every row must be >= effective_from 2026-09-25):")
        for r in await conn.fetch(
            "SELECT created_at::date d, count(*) n FROM candles_1m GROUP BY 1 ORDER BY 1"
        ):
            flag = "  <-- BEFORE effective_from, attribution questionable" if (
                str(r["d"]) < "2026-09-25"
            ) else ""
            print(f"    {r['d']}  {r['n']:>8}{flag}")

        if not nulls:
            print("\nnothing to do: no NULL lineage remains")
            return 0
        if args.dry_run:
            print(f"\nDRY RUN: would stamp {nulls} row(s) with {snapshot_id}")
            return 0

        # One statement, one transaction: either every NULL row is stamped or none is.
        async with conn.transaction():
            res = await conn.execute(
                "UPDATE candles_1m SET reference_snapshot_id = $1 "
                "WHERE reference_snapshot_id IS NULL",
                snapshot_id,
            )
        print(f"\n{res}")
        after = await conn.fetchval(
            "SELECT count(*) FROM candles_1m WHERE reference_snapshot_id IS NULL"
        )
        print(f"NULL lineage after: {after}")
        if after:
            print("FAILED: lineage is still missing - investigate before trusting this data")
            return 1
        print("OK: every row now carries a reference snapshot")
        return 0
    finally:
        await conn.close()


def _password() -> str:
    from scripts.verify_universe_data import _password as pw

    return pw()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
