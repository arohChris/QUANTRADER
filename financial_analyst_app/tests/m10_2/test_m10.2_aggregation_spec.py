"""WHY: M10.2 Slice 1 - 1m -> coarser candle aggregation. Tests BEFORE implementation.

Written against `docs/specs/m10.2_market_data_api.spec.md` only (decisions D3, D4, D5, D9
and section 5 edges), never against the module that does not exist yet. RED on the current
tree is the honest state and is the evidence the tests-first order was respected.

The load-bearing decision these tests defend: only `candles_1m` is stored, so EVERY
timeframe the UI dropdown offers above 1m is computed. If aggregation is subtly wrong the
chart still renders - it is just quietly, professionally wrong. A price is not visibly
broken when it is 3 cents off, so correctness cannot be eyeballed; it has to be pinned.

Empirically verified before these tests were written, on TimescaleDB 2.30.1 against the real
`candles_1m` hypertable: a continuous aggregate using `first()`/`last()`/`max()`/`min()` is
accepted, and `materialized_only` defaults to TRUE - which is why D4 is load-bearing and
not a nicety. Without `materialized_only = false` the newest bucket is invisible until the
refresh policy runs, so a 1m chart lags by a full interval.
"""
from __future__ import annotations

import re

import pytest

from scripts.migrate_candle_aggregates import (
    AGGREGATABLE_INTERVALS,
    all_ddl,
    cagg_ddl,
    cagg_refresh_policy,
    is_aggregatable,
)
from shared.timeframes import INTERVAL_MINUTES

MINUTES_PER_BUCKET_OK = {"5m": 5, "15m": 15, "30m": 30, "40m": 40,
                         "1h": 60, "2h": 120, "4h": 240, "24h": 1440}


# --------------------------------------------------------------------------
# Vocabulary - the interval set must come from the ONE frozen registry
# --------------------------------------------------------------------------


def test_aggregatable_intervals_are_the_frozen_registry_minus_1m() -> None:
    """The set is derived, never re-declared.

    `shared/timeframes.py` exists specifically so there is "one registry, not four competing
    vocabularies" and is drift-locked to M2.4 by a parity test. A second list here would be
    the exact drift that registry was built to prevent.
    """
    assert set(AGGREGATABLE_INTERVALS) == set(INTERVAL_MINUTES) - {"1m"}


def test_1m_is_not_aggregatable_because_it_is_the_source() -> None:
    """1m is what we store. Aggregating it would be a pointless second copy."""
    assert "1m" not in AGGREGATABLE_INTERVALS
    assert is_aggregatable("1m") is False
    assert is_aggregatable("5m") is True


@pytest.mark.parametrize("tf", ["7m", "3m", "", "5M", "5 m", "1s", None, "1w", "1mo", "1y"])
def test_unknown_and_calendar_intervals_are_refused(tf: str | None) -> None:
    """Edges: unknown interval is rejected, never snapped to a neighbour.

    Calendar buckets are refused STRUCTURALLY, not by convention: `shared/timeframes.py`
    states they are "deliberately ABSENT: fixed-duration strings only until explicit
    calendar-bucket semantics are spec'd". A 1w bar is not a fixed-duration bucket and
    aggregating one as if it were would produce bars that disagree with the venue's.
    """
    assert is_aggregatable(tf) is False


# --------------------------------------------------------------------------
# D3 - the shape of the aggregate
# --------------------------------------------------------------------------


def test_ddl_creates_a_continuous_aggregate_over_the_1m_hypertable() -> None:
    """D3. A TimescaleDB continuous aggregate, not a Python loop over raw rows."""
    sql = cagg_ddl("5m")
    assert "timescaledb.continuous" in sql
    assert "CREATE MATERIALIZED VIEW" in sql
    assert "time_bucket(INTERVAL '5 minutes', interval_start)" in sql
    assert "FROM candles_1m" in sql


def test_ddl_groups_by_bucket_and_both_key_columns() -> None:
    """Grouping must include provider and instrument_id.

    Market data is stored once per `provider + instrument` (AGENTS.md 4.3). Bucketing
    without the key columns would silently merge BTCUSD and EURUSD into one bar.
    """
    sql = cagg_ddl("5m")
    group_by = re.search(r"GROUP BY(.+?)(?:;|$)", sql, re.DOTALL | re.IGNORECASE)
    assert group_by is not None, "continuous aggregates REQUIRE a GROUP BY"
    text = group_by.group(1)
    assert "bucket" in text
    assert "instrument_id" in text
    assert "provider" in text


@pytest.mark.parametrize("tf", sorted(MINUTES_PER_BUCKET_OK))
def test_ddl_bucket_width_matches_the_requested_interval(tf: str) -> None:
    """D3. The bucket width is the requested interval, not a fixed default.

    A copy-paste slip here is the classic aggregation bug: every timeframe renders, all of
    them show the same bars, and nothing errors.
    """
    minutes = MINUTES_PER_BUCKET_OK[tf]
    sql = cagg_ddl(tf)
    bucket = re.search(r"time_bucket\(INTERVAL '(\d+) (minute|hour)s?'", sql)
    assert bucket is not None, f"{tf}: no time_bucket call found"
    value, unit = int(bucket.group(1)), bucket.group(2)
    assert value * (60 if unit == "hour" else 1) == minutes


# --------------------------------------------------------------------------
# D5 - ordered OHLC aggregates. This is the correctness core.
# --------------------------------------------------------------------------


def _sql_only(text: str) -> str:
    """Strip `--` line comments before asserting on SQL.

    WHY: the DDL carries WHY comments that must name the exact expressions they forbid
    ("min(open)/max(close) would be wrong on any down bar"). A regex that scans the whole
    string then matches the prose describing the bug instead of the bug. Asserting on
    executable text only also makes these tests robust to rewording the comment.
    """
    return re.sub(r"--[^\n]*", "", text)


def test_ddl_uses_ordered_aggregates_for_open_and_close() -> None:
    """D5. `first(open)` and `last(close)`, ordered BY interval_start.

    THE bug this forbids: `min(open)` / `max(close)` look plausible and are wrong on any
    bar whose price fell. On a down bar `min(open)` returns the CLOSE, so the chart draws
    an open that never traded. It is silent - no error, plausible-looking output.
    """
    sql = _sql_only(cagg_ddl("5m"))
    assert re.search(r"first\(\s*open\s*,\s*interval_start\s*\)", sql, re.IGNORECASE), \
        "open must be first(open, interval_start) - an unordered aggregate is a wrong price"
    assert re.search(r"last\(\s*close\s*,\s*interval_start\s*\)", sql, re.IGNORECASE), \
        "close must be last(close, interval_start)"
    assert not re.search(r"min\(\s*open\s*\)", sql, re.IGNORECASE), "min(open) is never a real open"
    assert not re.search(r"max\(\s*close\s*\)", sql, re.IGNORECASE), "max(close) is never a real close"


def test_ddl_uses_max_for_high_and_min_for_low() -> None:
    """D5. high is the bucket maximum, low is the bucket minimum."""
    sql = cagg_ddl("5m")
    assert re.search(r"max\(\s*high\s*\)", sql, re.IGNORECASE)
    assert re.search(r"min\(\s*low\s*\)", sql, re.IGNORECASE)


def test_ddl_does_not_reorder_bars_within_a_bucket() -> None:
    """Ordering must be explicit on interval_start, never on the price columns."""
    sql = cagg_ddl("5m")
    for bad in ("first(open, high)", "last(close, low)", "first(open, close)"):
        assert bad not in sql.lower(), f"ordering by {bad} is not time order"


# --------------------------------------------------------------------------
# D4 - real-time aggregates. Without this the newest bucket does not exist.
# --------------------------------------------------------------------------


def test_ddl_enables_real_time_aggregation() -> None:
    """D4. `materialized_only = false`.

    Measured on this host: TimescaleDB 2.30.1 defaults it to TRUE and, from 2.13 onward,
    disables real-time aggregates by default. With it TRUE the current bucket is invisible
    until the refresh policy fires, so the chart's rightmost bar lags by a whole interval -
    which on a 1m chart is a permanently stale chart that looks fine.
    """
    sql = cagg_ddl("5m")
    assert re.search(r"materialized_only\s*=\s*false", sql, re.IGNORECASE), \
        "real-time aggregation must be on or the newest bucket is missing"


def test_refresh_policy_leaves_the_current_interval_unmaterialized() -> None:
    """The refresh policy must stop at `end_offset = interval`, not at now.

    Materialising the bucket that is still forming would publish a partial bar as if it
    were final, and `ON CONFLICT DO NOTHING` on the raw table would then fight it. This
    is the same FORMING/CANONICAL/FINAL discipline broker_candles.py already enforces.
    """
    policy = cagg_refresh_policy("5m")
    assert "add_continuous_aggregate_policy" in policy
    assert re.search(r"end_offset\s*=>\s*INTERVAL '5 minutes'", policy, re.IGNORECASE)
    assert "schedule_interval" in policy


# --------------------------------------------------------------------------
# D9 - a bucket with a missing minute must be visible as gapped
# --------------------------------------------------------------------------


@pytest.mark.parametrize("tf", ["5m", "1h"])
def test_ddl_exposes_the_minute_count_so_gaps_are_detectable(tf: str) -> None:
    """D9. The aggregate must carry `count(*)` so the API can derive `quality: gapped`.

    We have real venue gaps (metals 2026-09-07, the crypto 21:07Z hole). Aggregating them
    into a clean-looking bar would make real data loss invisible, and AGENTS.md 3.4
    forbids a backtest that validates data the system does not actually have. Comparing
    the returned count against the interval's minute count is how a gap stays visible
    instead of being averaged away.
    """
    sql = cagg_ddl(tf)
    assert re.search(r"count\(\*\)\s+AS\s+\w*n\w*", sql, re.IGNORECASE), \
        "the bucket's minute count must be exposed, or a gap cannot be reported"


def test_ddl_preserves_quality_and_lineage_columns() -> None:
    """Lineage and quality must survive aggregation.

    `reference_snapshot_id` and `quality_status` are the audit trail (AGENTS.md 4.4, 3.12).
    A coarser bar that silently dropped them could not be traced back to the reference
    snapshot that produced it.
    """
    sql = cagg_ddl("1h")
    assert "reference_snapshot_id" in sql.lower()
    assert "quality_status" in sql.lower()


# --------------------------------------------------------------------------
# Idempotence - a migration that runs twice must be safe
# --------------------------------------------------------------------------


def test_all_ddl_is_idempotent() -> None:
    """Migrations get re-run. A second application must not error.

    Without IF NOT EXISTS a re-run aborts mid-migration and leaves a half-applied schema,
    which is the worst state to discover later.

    `ALTER MATERIALIZED VIEW ... SET (...)` is exempt and deliberately so: it takes no
    IF NOT EXISTS because it is idempotent by construction - setting the same option twice
    is a no-op, not an error. Only statements that CREATE something need the guard.
    """
    statements = all_ddl()
    assert statements, "no DDL produced"
    creates = [s for s in statements if "CREATE MATERIALIZED VIEW" in s.upper()]
    assert len(creates) == len(AGGREGATABLE_INTERVALS), \
        "one continuous aggregate per aggregatable interval"
    for sql in creates:
        assert "IF NOT EXISTS" in sql.upper(), \
            f"not idempotent: {sql.splitlines()[0][:70]}"


def test_all_ddl_covers_every_aggregatable_interval() -> None:
    """One aggregate per interval above 1m - the dropdown must have no dead options."""
    statements = all_ddl()
    for tf in AGGREGATABLE_INTERVALS:
        assert any(f"candles_agg_{_slug(tf)}" in s for s in statements), \
            f"no aggregate produced for {tf}"


def test_unknown_interval_raises_rather_than_emitting_sql() -> None:
    """Edges: unknown interval is rejected. Emitting a guessed bucket would be worse."""
    with pytest.raises(ValueError):
        cagg_ddl("7m")
    with pytest.raises(ValueError):
        cagg_refresh_policy("7m")


def _slug(tf: str) -> str:
    """`24h` -> `24h`, `5m` -> `5m`; keeps the interval readable in the view name."""
    return tf