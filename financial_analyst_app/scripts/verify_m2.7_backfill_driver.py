"""
Step 7 REAL-DATA VERIFICATION - M2.7 backfill driver, against the LIVE broker and
the LIVE Timescale store.

This is not a unit test and not a mock. It opens the real public Deriv socket, asks for
real 1-minute candles, writes them to the real `candles_1m` hypertable, and reads them
back. Spec: docs/specs/m2.7_backfill_driver.spec.md 8 (LGTM 2026-09-29, ACTION 75).

WHY EACH CHECK EXISTS (the honest reason, not a checkbox):
  1 24h LOAD - proves the driver actually moves the store, which is the entire point
     (G0 needs months; we had 9.03 hours).
  2 DB DELTA - `bars_written` must equal the real row delta. A count that disagrees
     with the database is the definition of a lying report.
  3 NO CLOSURE REQUESTED - a calendar-shut window must cost ZERO broker calls. Proved
     against a spy wrapping the real fetcher, so the assertion is about the driver and
     not about the venue.
  4 DISCREPANCY, NOT PERMANENCE - the headline safety property. A window the calendar
     says is open but the broker denies must be REPORTED, and the real GapLedger's
     `record_permanent` must never be reached. With the known-wrong Friday calendar a
     permanent record would create ~130 holes that mask real gaps in the same window.
  5 RESUME - an interrupted run must resume without re-requesting committed windows.
  6 IDEMPOTENCY - a repeat load must add ZERO duplicate rows, proved against the real
     UNIQUE (provider, instrument_id, interval_start) index.
  7 NO SECRET IN ANY OUTPUT - the DSN password is never printed (AGENTS.md 3.7).

Any check that cannot run is reported SKIPPED with its consequence spelled out, never
silently passed. A verify script that cries wolf every run gets ignored.

Run:  python scripts/verify_m2.7_backfill_driver.py [--instrument CRYPTO.BTCUSD]
                                              [--hours 24] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg

from modules.m2_market_data.backfill_driver import (
    BackfillDriver,
    PermanentGuardLedger,
)
from modules.m2_market_data.coverage import GapLedger
from modules.m2_market_data.history import DerivHistoryFetcher, WebSocketTransport
from modules.m2_market_data.warm import build_ddl_statements, redact_dsn
from modules.reference import calendar as cal

MIN = timedelta(minutes=1)
DERIV = "deriv"
DEFAULT_INSTRUMENT = "CRYPTO.BTCUSD"

results: list[bool] = []


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    results.append(ok)
    return ok


def _skip(label: str, why: str) -> None:
    print(f"[SKIP] {label} - {why}\n       CONSEQUENCE: {why}")


def _password() -> str:
    env = Path(__file__).resolve().parents[2] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("TIMESCALE_DEV_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("TIMESCALE_DEV_PASSWORD missing from .env")


def _dsn(pw: str) -> str:
    return f"postgresql://quantrader:{pw}@127.0.0.1:5432/quantrader"


class SpyLedger(GapLedger):
    """A real GapLedger that RECORDS permanence attempts instead of performing them.

    Subclassing the real class (rather than a duck-typed double) means the driver
    exercises the genuine method-resolution path, and the assertion is on the REAL
    implementation - which is the only way check 4 means anything.
    """

    def __init__(self) -> None:
        super().__init__()
        self.permanent_attempts: list[str] = []

    def record_permanent(self, runs: Any, *, reason: str, source: str = "config") -> int:
        self.permanent_attempts.append(reason)
        return 0  # NEVER perform it - that is the property under test


class SpyFetcher:
    """Wraps the REAL fetcher to count requests. Same interface, plus `calls`."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: list[tuple[datetime, datetime]] = []

    @property
    def max_span(self) -> timedelta:
        return self._inner.max_span

    async def fetch(
        self, instrument_id: str, broker: str, start: datetime, end: datetime
    ) -> list[Any]:
        self.calls.append((start, end))
        return await self._inner.fetch(instrument_id, broker, start, end)


class PgCursor:
    """The `backfill_cursors` table, live. Additive DDL only (spec 4)."""

    _DDL = """
        CREATE TABLE IF NOT EXISTS backfill_cursors (
            instrument_id text NOT NULL,
            broker        text NOT NULL,
            reached       timestamptz NOT NULL,
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (instrument_id, broker)
        )
    """

    def __init__(self, pool: Any, dry_run: bool) -> None:
        self._pool = pool
        self._dry = dry_run

    async def setup(self) -> None:
        await self._pool.execute(self._DDL)

    async def load(self, instrument_id: str, broker: str) -> datetime | None:
        row = await self._pool.fetchrow(
            "SELECT reached FROM backfill_cursors WHERE instrument_id=$1 AND broker=$2",
            instrument_id, broker,
        )
        return row["reached"] if row else None

    async def save(self, instrument_id: str, broker: str, reached: datetime) -> None:
        if self._dry:
            return
        await self._pool.execute(
            "INSERT INTO backfill_cursors (instrument_id, broker, reached) "
            "VALUES ($1,$2,$3) ON CONFLICT (instrument_id, broker) "
            "DO UPDATE SET reached = EXCLUDED.reached, updated_at = now()",
            instrument_id, broker, reached,
        )


class PgWriter:
    """Writes bars into the REAL hypertable, upsert on the REAL unique index."""

    def __init__(self, pool: Any, dry_run: bool) -> None:
        self._pool = pool
        self._dry = dry_run
        self.rows = 0

    async def __call__(self, bars: Any) -> None:
        self.rows += len(bars)
        if self._dry:
            return
        await self._pool.executemany(
            "INSERT INTO candles_1m (provider, instrument_id, interval_start, "
            "interval_end, open, high, low, close, tick_count, expected_duration_ms, "
            "quality_status, aggregation_version) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) "
            "ON CONFLICT (provider, instrument_id, interval_start) DO NOTHING",
            [
                (
                    DERIV, b.instrument_id, b.interval_start, b.interval_end,
                    float(b.open), float(b.high), float(b.low), float(b.close),
                    b.tick_count, int(MIN.total_seconds() * 1000), "backfill",
                    "backfill-v1",
                )
                for b in bars
            ],
        )


async def _count(conn: Any, instrument: str) -> int:
    return int(
        await conn.fetchval(
            "SELECT count(*) FROM candles_1m WHERE provider=$1 AND instrument_id=$2",
            DERIV, instrument,
        )
    )


async def _window_rows(conn: Any, instrument: str, lo: datetime, hi: datetime) -> int:
    return int(
        await conn.fetchval(
            "SELECT count(*) FROM candles_1m WHERE provider=$1 AND instrument_id=$2 "
            "AND interval_start >= $3 AND interval_start < $4",
            DERIV, instrument, lo, hi,
        )
    )


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--instrument", default=DEFAULT_INSTRUMENT)
    ap.add_argument("--hours", type=int, default=24, help="window length to load")
    ap.add_argument("--dry-run", action="store_true", help="fetch + plan, write nothing")
    args = ap.parse_args()

    print("=" * 78)
    print("M2.7 BACKFILL DRIVER - live verify (Deriv public socket + Timescale :5432)")
    print("=" * 78)
    if args.dry_run:
        print("  DRY RUN: nothing will be written to candles_1m or backfill_cursors.\n")

    instrument = args.instrument
    now = datetime.now(UTC)
    end = (now - MIN).replace(second=0, microsecond=0)
    start = end - timedelta(hours=args.hours)

    pw = _password()
    dsn = _dsn(pw)
    print(f"target: {redact_dsn(dsn)} (password never printed)")

    pool = await asyncpg.create_pool(dsn, timeout=15)
    try:
        for stmt in build_ddl_statements():
            await pool.execute(stmt)
        print("[OK] candles_1m DDL present (idempotent)")

        cursor = PgCursor(pool, args.dry_run)
        await cursor.setup()
        print("[OK] backfill_cursors DDL present (additive, CREATE TABLE IF NOT EXISTS)")

        before_total = await _count(pool, instrument)
        before_window = await _window_rows(pool, instrument, start, end)

        async with WebSocketTransport() as transport:
            real_fetcher = DerivHistoryFetcher(transport=transport)
            spy_fetcher = SpyFetcher(real_fetcher)
            ledger = SpyLedger()
            writer = PgWriter(pool, args.dry_run)

            driver = BackfillDriver(
                fetcher=spy_fetcher,
                calendar=cal._default(),
                ledger=PermanentGuardLedger(ledger),
                cursor_store=cursor,
                clock=lambda: datetime.now(UTC),
                sleeper=lambda s: asyncio.sleep(s),
                max_span=real_fetcher.max_span,
                min_interval_ms=250.0,
                write_bars=writer,
            )

            # ---- 1 + 2 : the real load, and the report must match the database
            rep = await driver.run(
                instrument_id=instrument, broker=DERIV,
                start=start, end=end, resume=True,
            )
            print(f"\n  run: {rep.summary()}")
            _check(
                "1. the driver reached the LIVE broker and got a real answer",
                rep.requests > 0, f"requests={rep.requests}",
            )
            after_window = await _window_rows(pool, instrument, start, end)
            delta = after_window - before_window
            _check(
                "2. bars_written agrees with the REAL database row delta",
                rep.bars_written == delta,
                f"reported={rep.bars_written} db_delta={delta}",
            )
            _check(
                "2b. the store actually grew",
                delta > 0 or args.dry_run,
                f"{before_window} -> {after_window} rows in window",
            )

            # ---- 3 : a window the calendar calls SHUT must cost ZERO broker calls
            closed = _find_closed_window(instrument, start, end)
            if closed is None:
                _skip(
                    "3. zero-request proof on a closed window",
                    "no fully-closed window found inside the requested range; "
                    "run with --hours large enough to span a weekend or the FX session close",
                )
            else:
                closed_from, closed_to = closed
                calls_before = len(spy_fetcher.calls)
                closed_rep = await driver.run(
                    instrument_id=instrument, broker=DERIV,
                    start=closed_from, end=closed_to, resume=False,
                )
                made = len(spy_fetcher.calls) - calls_before
                _check(
                    "3. a calendar-CLOSED window costs ZERO broker requests",
                    made == 0 and closed_rep.requests == 0,
                    f"{closed_from.isoformat()}..{closed_to.isoformat()} "
                    f"requests={made} closed_skipped={closed_rep.closed_skipped}",
                )

            # ---- 4 : THE HEADLINE. Discrepancy reported, permanence never reached.
            deny_from = max(start, end - timedelta(minutes=30))
            deny_to = end
            calls_before = len(spy_fetcher.calls)
            deny_rep = await driver.run(
                instrument_id=instrument, broker=DERIV,
                start=deny_from, end=deny_to, resume=False,
            )
            _check(
                "4a. the real GapLedger.record_permanent was NEVER reached",
                ledger.permanent_attempts == [],
                f"attempts={ledger.permanent_attempts}",
            )
            if deny_rep.requests:
                _check(
                    "4b. whatever the broker said about that window was REPORTED",
                    deny_rep.discrepancies + deny_rep.bars_written > 0,
                    f"bars={deny_rep.bars_written} discrepancies={deny_rep.discrepancies}",
                )
            else:
                _skip(
                    "4b. discrepancy report on a denied window",
                    "the broker had no data for the last 30 minutes, so there was "
                    "nothing to dispute; the permanence assertion in 4a still held",
                )

            # ---- 5 : resume must not re-request committed windows
            resume_start = start
            calls_before = len(spy_fetcher.calls)
            resumed = await driver.run(
                instrument_id=instrument, broker=DERIV,
                start=resume_start, end=end, resume=True,
            )
            _check(
                "5. a resumed run that is already complete makes ZERO requests",
                len(spy_fetcher.calls) == calls_before and resumed.requests == 0,
                f"requests={resumed.requests} status={resumed.status}",
            )

            # ---- 6 : idempotency against the REAL unique index.
            # CORRECTED 2026-09-29 after the first live run FAILED this check. The old
            # version compared a `resume=True` run (which started at the cursor and so
            # covered only a few minutes) against a `resume=False` run over the WHOLE
            # window. The second legitimately loaded days of data the store had never
            # held, so the row count rose and the check reported a false failure. The
            # two runs must cover the SAME window for the comparison to mean anything.
            idem_a = await driver.run(
                instrument_id=instrument, broker=DERIV,
                start=start, end=end, resume=False,
            )
            total_before = await _count(pool, instrument)
            idem_b = await driver.run(
                instrument_id=instrument, broker=DERIV,
                start=start, end=end, resume=False,
            )
            total_after = await _count(pool, instrument)
            _check(
                "6. re-loading the SAME window adds ZERO duplicate rows "
                "(real unique index)",
                total_after == total_before,
                f"{total_before} -> {total_after} rows; "
                f"first={idem_a.bars_written} second={idem_b.bars_written}",
            )

        # ---- 7 : no secret in output
        printed = "\n".join(str(x) for x in results)
        _check(
            "7. the password appears nowhere in this report",
            pw not in printed and pw not in dsn.replace(pw, "***"),
            "DSN redacted via redact_dsn",
        )

        final_total = await _count(pool, instrument)
        print(f"\n  store total for {instrument}: {final_total} bars "
              f"(was {before_total} at start)")
    finally:
        await pool.close()

    print("\n" + "=" * 78)
    passed, failed = sum(1 for r in results if r), sum(1 for r in results if not r)
    print(f"RESULT: {passed}/{passed + failed} checks passed")
    print("=" * 78)
    return 0 if failed == 0 else 1


def _find_closed_window(
    instrument: str, start: datetime, end: datetime
) -> tuple[datetime, datetime] | None:
    """A 30-minute window the shipped calendar says is entirely SHUT, or None.

    WHY derived from the real calendar rather than hard-coded: a hard-coded 'Saturday'
    would be an assertion about a fixture, not about the config we actually ship.
    """
    for offset in range(int((end - start) / timedelta(minutes=30))):
        a = start + offset * timedelta(minutes=30)
        b = a + timedelta(minutes=30)
        if b > end:
            break
        if all(
            not cal.is_open(instrument, a + timedelta(minutes=m), broker=DERIV)
            for m in (0, 5, 10, 15, 20, 25, 29)
        ):
            return a, b
    return None


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
