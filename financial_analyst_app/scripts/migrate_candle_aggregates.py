"""M10.2 Slice 1 - continuous aggregates for 1m -> coarser candles.

WHY THIS EXISTS. `candles_1m` is the only thing we store. The UI dropdown offers 1m, 5m,
15m, 30m, 40m, 1h, 2h, 4h and 24h, so eight of the nine options have to be COMPUTED. Doing
that in a Python loop means scanning raw 1m rows per dashboard request; doing it in the
database is the documented TimescaleDB pattern for exactly this shape and keeps the
arithmetic in one place that both the API and M8's walk-forward can trust.

Spec: docs/specs/m10.2_market_data_api.spec.md (decisions D3, D4, D5, D9).
Usage: python scripts/migrate_candle_aggregates.py [--dry-run] [--backfill-days N]

FROZEN INPUT, NOT EDITED: `candles_1m` (M2.10 WARM) and `shared/timeframes.py` (A35). This
module only adds derived views on top of them.

D5 IS THE WHOLE POINT. `first(open, interval_start)` and `last(close, interval_start)` are
ordered aggregates. `min(open)` looks reasonable and is wrong on every bar whose price fell -
it returns the close, so the chart would draw an open that never traded. That is the whole
reason this is a spec'd slice rather than a five-line view: the failure is silent, the output
looks plausible, and a backtest trained on it is quietly wrong.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.timeframes import INTERVAL_MINUTES

#: Every interval above 1m. DERIVED from the one frozen registry, never re-declared -
#: `shared/timeframes.py` exists so there is "one registry, not four competing
#: vocabularies" and is drift-locked to M2.4 by a parity test. A second list here would be
#: precisely the drift that registry was created to prevent.
AGGREGATABLE_INTERVALS: tuple[str, ...] = tuple(
    tf for tf in INTERVAL_MINUTES if tf != "1m"
)


def _slug(timeframe: str) -> str:
    return timeframe


def _interval_literal(minutes: int) -> str:
    """`5` -> `INTERVAL '5 minutes'`, `60` -> `INTERVAL '1 hour'`.

    Whole hours use the hour unit so `end_offset => INTERVAL '1 hour'` reads correctly in
    the refresh policy rather than as 60 minutes. Postgres accepts either; a human
    debugging a stale chart at 3am cannot.
    """
    if minutes % 60 == 0:
        hours = minutes // 60
        unit = "hour" if hours == 1 else "hours"
        return f"INTERVAL '{hours} {unit}'"
    unit = "minute" if minutes == 1 else "minutes"
    return f"INTERVAL '{minutes} {unit}'"


def is_aggregatable(timeframe: Any) -> bool:
    """True only for a fixed-duration interval we store a coarser bar for.

    Calendar buckets (`1w`, `1mo`, `1y`) are refused STRUCTURALLY, not by convention:
    `shared/timeframes.py` states they are "deliberately ABSENT: fixed-duration strings
    only until explicit calendar-bucket semantics are spec'd". A weekly bar opens at a
    venue-defined boundary we do not model, so aggregating one as if it were a fixed
    duration produces bars that disagree with the broker's own.
    """
    return isinstance(timeframe, str) and timeframe in AGGREGATABLE_INTERVALS


def _require(timeframe: str) -> int:
    if not is_aggregatable(timeframe):
        raise ValueError(
            f"{timeframe!r} is not aggregatable; expected one of "
            f"{list(AGGREGATABLE_INTERVALS)}"
        )
    return INTERVAL_MINUTES[timeframe]


def cagg_ddl(timeframe: str) -> str:
    """CREATE the continuous aggregate for one interval, plus its real-time setting.

    D4 is load-bearing. Measured on this host (TimescaleDB 2.30.1): `materialized_only`
    defaults to TRUE, and from 2.13 onward real-time aggregates are DISABLED by default.
    With it TRUE the newest bucket is invisible until the refresh policy fires, so the
    rightmost bar on a 1m chart lags by a full interval - a permanently stale chart that
    looks entirely healthy.
    """
    minutes = _require(timeframe)
    bucket = _interval_literal(minutes)
    view = f"candles_agg_{_slug(timeframe)}"
    return f"""\
-- WHY this view exists: {timeframe} candles are DERIVED, never stored twice.
-- D5: ordered aggregates. min(open)/max(close) would be wrong on any down bar.
-- D9: `n` is the count of contributing 1m rows so the API can report
--      quality='gapped' when n < {minutes}, instead of averaging a hole away.
CREATE MATERIALIZED VIEW IF NOT EXISTS {view}
WITH (timescaledb.continuous) AS
SELECT
    provider,
    instrument_id,
    time_bucket({bucket}, interval_start) AS bucket,
    first(open, interval_start)          AS open,
    max(high)                            AS high,
    min(low)                             AS low,
    last(close, interval_start)          AS close,
    count(*)                             AS n,
    (array_agg(quality_status ORDER BY interval_start))[1]         AS quality_status,
    (array_agg(reference_snapshot_id ORDER BY interval_start))[1]  AS reference_snapshot_id,
    (array_agg(aggregation_version ORDER BY interval_start))[1]   AS aggregation_version
FROM candles_1m
GROUP BY provider, instrument_id, bucket
WITH NO DATA;

-- WHY false: without it the current bucket is missing from every query until the refresh
-- policy runs. Idempotent by construction - setting the same option twice is a no-op.
ALTER MATERIALIZED VIEW {view} SET (timescaledb.materialized_only = false);
"""


def cagg_refresh_policy(timeframe: str) -> str:
    """Refresh everything EXCEPT the bucket that is still forming.

    `end_offset = <interval>` is the important part. Materialising the in-progress bucket
    would publish a partial bar as final, and `ON CONFLICT DO NOTHING` on the raw table
    would then fight it. This is the same FORMING / CANONICAL / FINAL discipline
    `broker_candles.py` already enforces on the streaming path.
    """
    minutes = _require(timeframe)
    view = f"candles_agg_{_slug(timeframe)}"
    return f"""\
SELECT add_continuous_aggregate_policy(
    '{view}',
    start_offset => INTERVAL '30 days',
    end_offset   => {_interval_literal(minutes)},
    schedule_interval => INTERVAL '5 minutes'
);
"""


def all_ddl() -> list[str]:
    """Every statement needed to stand the aggregation layer up, in order."""
    out: list[str] = []
    for tf in AGGREGATABLE_INTERVALS:
        out.append(cagg_ddl(tf))
        out.append(cagg_refresh_policy(tf))
    return out


def _view_names() -> list[str]:
    return [f"candles_agg_{_slug(tf)}" for tf in AGGREGATABLE_INTERVALS]


def _split_statements(sql: str) -> list[str]:
    """One statement per element, comments stripped.

    The DDL is returned as readable multi-statement text because that is what lets the tests
    assert on the WHY comments sitting next to the SQL. Execution wants them separated.
    `WITH NO DATA` is what makes this safe here: TimescaleDB refuses to materialise a
    continuous aggregate inside a transaction block, and building 850k rows during the
    migration would hold locks for minutes besides. The refresh is a separate, explicit,
    interruptible step - which is also the documented pattern for a large backfill.
    """
    out: list[str] = []
    for chunk in sql.split(";"):
        body = "\n".join(
            line for line in chunk.splitlines() if not line.strip().startswith("--")
        ).strip()
        if body:
            out.append(body)
    return out


async def _policy_exists(conn: Any, view: str) -> bool:
    """True when this continuous aggregate already has a refresh policy.

    TimescaleDB has no `add_continuous_aggregate_policy(..., if_not_exists =>)`. Adding a
    second policy with the same offsets raises and aborts the whole migration, leaving a
    half-applied schema - so the check is explicit rather than an exception handler. An
    exception-swallowing version would hide a genuine failure behind a false success.
    """
    row = await conn.fetchval(
        "SELECT 1 FROM timescaledb_information.jobs "
        "WHERE proc_name = 'policy_refresh_continuous_aggregate' "
        "AND hypertable_name = $1",
        view,
    )
    return row is not None


async def _apply(conn: Any, dry: bool) -> int:
    """Stand up every aggregate. Safe to run repeatedly.

    CREATE uses IF NOT EXISTS and ALTER ... SET is idempotent by construction, so only the
    refresh policy needs guarding.
    """
    for tf in AGGREGATABLE_INTERVALS:
        view = f"candles_agg_{_slug(tf)}"
        statements = _split_statements(cagg_ddl(tf)) + _split_statements(
            cagg_refresh_policy(tf)
        )
        for statement in statements:
            if dry:
                print(statement + ";")
                continue
            if "add_continuous_aggregate_policy" in statement and await _policy_exists(
                conn, view
            ):
                print(f"  {view}: refresh policy already present, left alone")
                continue
            await conn.execute(statement + ";")
    return len(AGGREGATABLE_INTERVALS)


async def _backfill(conn: Any, days: int) -> None:
    for tf in AGGREGATABLE_INTERVALS:
        view = f"candles_agg_{_slug(tf)}"
        await conn.execute(
            f"CALL refresh_continuous_aggregate('{view}', "
            f"NOW() - INTERVAL '{days} days', NOW())"
        )
        print(f"  refreshed {view} over the last {days}d")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print DDL, write nothing")
    ap.add_argument("--backfill-days", type=int, default=0,
                    help="after creating, refresh this many days of history")
    ap.add_argument("--list", action="store_true", help="list views and exit")
    args = ap.parse_args()

    if args.list:
        for name in _view_names():
            print(f"  {name}")
        return 0

    import asyncpg

    from scripts.verify_universe_data import dsn

    conn = await asyncpg.connect(dsn(), timeout=20)
    try:
        print("=" * 78)
        print(f"M10.2 candle aggregation - {len(AGGREGATABLE_INTERVALS)} intervals "
              f"{list(AGGREGATABLE_INTERVALS)}")
        print("=" * 78)
        n = await _apply(conn, args.dry_run)
        if args.dry_run:
            print(f"RESULT: DRY RUN - {n} interval(s) described, nothing written")
            return 0
        if args.backfill_days:
            await _backfill(conn, args.backfill_days)
        print(f"RESULT: PASS - {n} continuous aggregate(s) in place")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))