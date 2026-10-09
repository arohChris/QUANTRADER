"""
Load history for the whole derived universe using the M2.7 backfill driver.

Operational script, not a verify: it RUNS the completed, approved driver across every
instrument in config/reference/universe_screen.json. Per-instrument outcome is printed
and the exit code is loud on any failure - a partial load must never look like a success.

WHY per-instrument and not one big loop: the driver is calendar-aware, and each
instrument has its own session (metals close Fri 20:45, FX Fri 20:55, crypto 24/7). A
single shared window would request shut time and manufacture phantom gaps - the exact
defect the driver's I1 invariant exists to prevent.

Run:  python scripts/load_universe.py [--hours 120] [--top 25] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg

from modules.m2_market_data.backfill_driver import BackfillDriver, PermanentGuardLedger
from modules.m2_market_data.coverage import GapLedger
from modules.m2_market_data.history import DerivHistoryFetcher, WebSocketTransport
from modules.m2_market_data.warm import build_ddl_statements
from modules.reference import calendar as cal
from modules.reference.reference import current_snapshot_id
from scripts.verify_universe_data import dsn

MIN = timedelta(minutes=1)
DERIV = "deriv"
ROOT = Path(__file__).resolve().parents[1]


def _password() -> str:
    env = Path(__file__).resolve().parents[2] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("TIMESCALE_DEV_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("TIMESCALE_DEV_PASSWORD missing from .env")


class PgCursor:
    _DDL = """
        CREATE TABLE IF NOT EXISTS backfill_cursors (
            instrument_id text NOT NULL,
            broker        text NOT NULL,
            reached       timestamptz NOT NULL,
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (instrument_id, broker)
        )
    """

    def __init__(self, pool: object, dry: bool) -> None:
        self._pool, self._dry = pool, dry

    async def setup(self) -> None:
        await self._pool.execute(self._DDL)  # type: ignore[attr-defined]

    async def load(self, instrument_id: str, broker: str) -> datetime | None:
        row = await self._pool.fetchrow(  # type: ignore[attr-defined]
            "SELECT reached FROM backfill_cursors WHERE instrument_id=$1 AND broker=$2",
            instrument_id, broker,
        )
        return row["reached"] if row else None

    async def save(self, instrument_id: str, broker: str, reached: datetime) -> None:
        if self._dry:
            return
        await self._pool.execute(  # type: ignore[attr-defined]
            "INSERT INTO backfill_cursors (instrument_id, broker, reached) "
            "VALUES ($1,$2,$3) ON CONFLICT (instrument_id, broker) "
            "DO UPDATE SET reached = EXCLUDED.reached, updated_at = now()",
            instrument_id, broker, reached,
        )


class PgWriter:
    """Bulk-insert venue bars into `candles_1m`.

    LINEAGE IS MANDATORY AND FAIL-CLOSED. Measured 2026-09-30: this writer left
    `reference_snapshot_id` NULL on 796,331 of 796,333 rows, because the INSERT simply did
    not name the column. Every other writer in the system stamps lineage - `normalize.py:98`
    DROPS a tick whose `reference_snapshot_id` is empty ("lineage mandatory"), and the WARM
    path carries it through `pipeline.py` - so the bulk path was the single writer silently
    producing rows that cannot be attributed to an instrument spec. `AGENTS.md` 3.4 requires
    the snapshot id on venue data for reproducibility and 4.4 requires it on every row for
    replay; without it, a change to `config/assets/*.yaml` makes a replay of this history
    unreproducible, and this project has already been bitten by a live `tick_size`
    0.01-vs-0.001 defect that truncated prices to 2dp.

    So a missing snapshot is REFUSED here too, rather than written as NULL. A 796k-row table
    with no lineage is a far worse artefact than a load that stops and says why.
    """

    def __init__(self, pool: object, dry: bool, reference_snapshot_id: str | None) -> None:
        self._pool, self._dry = pool, dry
        self._ref = reference_snapshot_id
        if not dry and not reference_snapshot_id:
            raise RuntimeError(
                "reference_snapshot_id is mandatory: current_snapshot_id() returned "
                f"{reference_snapshot_id!r}. Refusing to write bars with no lineage. Check "
                "config/reference/snapshot.yaml (snapshot_id must match REF-YYYY-MM-DD-NNN)."
            )

    async def __call__(self, bars: object) -> None:
        if self._dry:
            return
        await self._pool.executemany(  # type: ignore[attr-defined]
            # WHY this INSERT no longer names `tick_count` (Candle 2.0.0, human §5 2026-09-29):
            # THIS RAW SQL WAS THE ACTUAL SOURCE of the fabricated value. It bypassed the
            # frozen `Candle` contract and `warm.encode_candle_row` entirely, so changing the
            # contract alone would NOT have fixed a single stored row - 103,867 of the 104,069
            # rows then carried a `tick_count` of 1 that this statement invented from a
            # HistoryBar default. The venue sends no such field, so it is not written here.
            # Second writer, same lesson, and this is the fix for it: a store fed by a
            # hand-written INSERT has no schema gate, so "the contract forbids it" is not the
            # same as "nothing writes it" - which is how both the fabricated tick_count AND
            # the missing lineage got in. Hence the lineage column below, and the fail-closed
            # constructor above rather than a best-effort NULL.
            "INSERT INTO candles_1m (provider, instrument_id, interval_start, "
            "interval_end, open, high, low, close, expected_duration_ms, "
            "quality_status, aggregation_version, reference_snapshot_id) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) "
            "ON CONFLICT (provider, instrument_id, interval_start) DO NOTHING",
            [
                (
                    DERIV, b.instrument_id, b.interval_start, b.interval_end,
                    float(b.open), float(b.high), float(b.low), float(b.close),
                    int(MIN.total_seconds() * 1000), "backfill", "backfill-v1",
                    self._ref,
                )
                for b in bars  # type: ignore[union-attr]
            ],
        )


class GapLedgerQuiet(GapLedger):
    """The driver's guard already refuses permanence; this keeps the inner honest."""

    def record_permanent(self, runs: object, *, reason: str, source: str = "config") -> int:
        return 0


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=120)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    selection = json.loads(
        (ROOT / "config" / "reference" / "universe_screen.json").read_text(encoding="utf-8")
    )
    chosen = selection["selected"][: args.top]
    # Lineage for every bar this run writes. Read ONCE here rather than per chunk so a
    # snapshot rotation mid-run cannot split one load across two reference ids.
    snapshot_id = current_snapshot_id()
    print("=" * 78)
    print(f"UNIVERSE LOAD - {len(chosen)} instruments x {args.hours}h")
    print(f"lineage        {snapshot_id or 'MISSING - PgWriter will refuse to write'}")
    print(f"screened_at {selection['screened_at']} (criterion: {selection['criterion']})")
    print("=" * 78)
    if args.dry_run:
        print("DRY RUN: nothing written.\n")

    end = (datetime.now(UTC) - MIN).replace(second=0, microsecond=0)

    pool = await asyncpg.create_pool(
        dsn(), timeout=20
    )
    failures: list[str] = []
    totals = {"requests": 0, "bars": 0, "discrepancies": 0, "unresolved": 0}
    try:
        for stmt in build_ddl_statements():
            await pool.execute(stmt)
        cursor = PgCursor(pool, args.dry_run)
        await cursor.setup()

        # WHY THE WINDOW MUST HONOUR THE CURSOR (measured defect, 2026-10-07).
        #
        # The window used to be `now - hours`, and the driver then applied
        # `start = max(start, cursor)` (backfill_driver.py:427). `max` keeps the LATER
        # bound, so any cursor rewound further back than `now - hours` was SILENTLY
        # DISCARDED. Measured consequence: a 1039-minute hole on 2026-10-02 (identical
        # across all 25 instruments) survived every repair attempt while each run reported
        # `discrepancies=0 unresolved=0` - because the window that contained the hole was
        # never requested. `rewind_backfill_cursor.py` was therefore a no-op for any hole
        # older than `--hours`, which is precisely the case you care about.
        #
        # `min` is the correct operator: the window must begin at WHICHEVER is earlier, the
        # recent horizon or the stored cursor. A normal run is unaffected (the cursor trails
        # `now - hours`, so `min` yields `now - hours`); a rewound cursor now actually pulls
        # the window back to it. Taking the GLOBAL min over all instruments is deliberate:
        # one instrument's rewind must not be skipped because the other 24 cursors are ahead.
        oldest_cursor = await pool.fetchval(
            "SELECT min(reached) FROM backfill_cursors WHERE broker = $1", DERIV
        )
        if oldest_cursor is None:
            start = end - timedelta(hours=args.hours)
        else:
            start = min(end - timedelta(hours=args.hours), oldest_cursor)
        if start < end:
            print(
                f"window        {start.isoformat()} .. {end.isoformat()}  "
                f"(= min(now-{args.hours}h, oldest cursor {oldest_cursor.isoformat()}))"
            )

        async with WebSocketTransport() as transport:
            fetcher = DerivHistoryFetcher(transport=transport)
            for row in chosen:
                native = row["native"]
                try:
                    # broker-native -> canonical, the only direction that may be assumed.
                    # to_native is the REVERSE (canonical -> native) and would raise here.
                    from modules.reference.reference import to_canonical

                    instrument = to_canonical(native, DERIV)
                except Exception as exc:  # noqa: BLE001 - per-instrument isolation: one bad symbol must not abort the other 24
                    print(f"  {native:<12} SKIP  not in reference master: {exc}")
                    failures.append(f"{native}: unresolved")
                    continue

                ledger = GapLedgerQuiet()
                driver = BackfillDriver(
                    fetcher=fetcher,
                    calendar=cal._default(),
                    ledger=PermanentGuardLedger(ledger),
                    cursor_store=cursor,
                    clock=lambda: datetime.now(UTC),
                    sleeper=lambda s: asyncio.sleep(s),
                    max_span=fetcher.max_span,
                    min_interval_ms=250.0,
                    write_bars=PgWriter(pool, args.dry_run, snapshot_id),
                )
                try:
                    rep = await driver.run(
                        instrument_id=instrument, broker=DERIV,
                        start=start, end=end, resume=True,
                    )
                except Exception as exc:  # noqa: BLE001 - per-instrument isolation: one bad symbol must not abort the other 24
                    print(f"  {native:<12} {instrument:<18} ERROR {type(exc).__name__}: {exc}")
                    failures.append(f"{native}: {type(exc).__name__}")
                    continue

                totals["requests"] += rep.requests
                totals["bars"] += rep.bars_written
                totals["discrepancies"] += rep.discrepancies
                totals["unresolved"] += rep.unresolved
                flag = "" if rep.status == "complete" else f"  <-- {rep.status}"
                print(
                    f"  {native:<12} {instrument:<18} {rep.status:<14} "
                    f"req={rep.requests:<4} bars={rep.bars_written:<6} "
                    f"closed_skipped={rep.closed_skipped:<5} "
                    f"disc={rep.discrepancies} unres={rep.unresolved}{flag}"
                )
                if rep.status != "complete":
                    failures.append(f"{native}: {rep.status}")

        rows = await pool.fetch(
            "SELECT instrument_id, count(*) AS n, min(interval_start)::date AS lo, "
            "max(interval_start)::date AS hi FROM candles_1m "
            "GROUP BY 1 ORDER BY 2 DESC"
        )
        print("\n" + "=" * 78)
        print(f"{'instrument':<20} {'bars':>7}  from        to")
        print("-" * 78)
        grand = 0
        for r in rows:
            grand += r["n"]
            print(f"{r['instrument_id']:<20} {r['n']:>7,}  {r['lo']}  {r['hi']}")
        print("-" * 78)
        print(f"{'TOTAL':<20} {grand:>7,}")
        print(f"requests={totals['requests']} bars_written={totals['bars']} "
              f"discrepancies={totals['discrepancies']} unresolved={totals['unresolved']}")
    finally:
        await pool.close()

    print("=" * 78)
    if failures:
        print(f"RESULT: PARTIAL - {len(failures)} instrument(s) not clean:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print(f"RESULT: PASS - all {len(chosen)} instruments complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
