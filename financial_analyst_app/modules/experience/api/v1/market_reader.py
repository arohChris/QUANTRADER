"""M10.2 Slice 2 - the concrete read implementation behind the `CandleReader` port.

WHY THIS FILE IS SO SMALL. `market_router.py` owns no arithmetic and no SQL. This adapter is
the only place that knows the aggregated views exist, which is the seam that keeps
`AGENTS.md` 6a true: the handler cannot bypass the read path even by accident, because it has
no idea the store exists.

Two responsibilities, and only two:
  1. read coarser candles from the Slice-1 continuous aggregates
  2. list the canonical universe

WHY IT READS THE AGGREGATES RATHER THAN RE-AGGREGATING. `scripts/migrate_candle_aggregates.py`
already materialises 1m -> every interval, with ordered OHLC aggregates and
`materialized_only = false` so the current bucket is visible. Re-deriving the same buckets in
Python per request would be a second implementation of the same arithmetic, and the M2.14
lesson applies: two producers of the same numbers is how a backtest ends up validating one
series while the dashboard draws another. One producer, read many times.

CURSOR. Opaque to the client and minted here, but it is an OFFSET into an oldest-to-newest
ordering. That is honest and stable *because the ordering is total* - `(instrument, interval,
bucket)` is unique, so a cursor cannot skip or repeat a row when new buckets land between
pages. See the "scrollback" note in the M10.2 spec: an unstable order would make the cursor
unusable for scrollback, which is the whole reason the endpoint is paginated this way.

Spec: docs/specs/m10.2_market_data_api.spec.md.
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from typing import Any

from shared.timeframes import INTERVAL_MINUTES

#: Columns common to every source. The time column and the row-count column are deliberately
#: NOT here: they are named `bucket`/`n` on the Slice-1 aggregates but `interval_start`/
#: `tick_count` on the frozen 1m hypertable, so both come from `_view_for` per view.
#: Hardcoding either was a real bug the first live run caught:
#: `column "bucket" does not exist`, then `column "n" does not exist`, on the 1m path only.
_SELECT = "open, high, low, close, quality_status, reference_snapshot_id"


def _encode_cursor(offset: int) -> str:
    raw = json.dumps({"o": offset}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    """Opaque token -> offset. A tampered token is caller error, never a 500."""
    if cursor is None:
        return 0
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        offset = payload["o"]
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeEncodeError) as exc:
        raise ValueError(f"cursor {cursor!r} is not a valid page token") from exc
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError(f"cursor {cursor!r} does not carry a usable offset")
    return offset


def _view_for(interval: str) -> tuple[str, str, str]:
    """(view, time-column, count-column) for an interval.

    WHY THE COLUMNS DIFFER. The Slice-1 continuous aggregates expose their bucket as `bucket`
    and their contributing-row count as `n` (a `count(*)`). The raw 1m hypertable is M2.10
    WARM's frozen schema and names the same two things `interval_start` and `tick_count`.
    Both source schemas are frozen, so the adapter here is what absorbs the difference;
    querying them as if they were the same shape produced
    `column "bucket" does not exist` and then `column "n" does not exist` on the 1m path only.
    """
    if interval == "1m":
        # `tick_count` is hardcoded to 1 by DerivHistoryFetcher (audit limitation D1), which
        # is harmless here: at 1m the expected count is 1, so any stored row is complete and
        # the D9 gapped check can never fire on this path. Coarser intervals read a real
        # `count(*)` and the check works properly.
        return "candles_1m", "interval_start", "tick_count"
    return f"candles_agg_{interval}", "bucket", "n"


class MarketReaderUnavailable(RuntimeError):
    """The store is not reachable, so market data cannot be served right now.

    A distinct type so the router can answer `broker_unavailable` (503, and in the envelope's
    `_RETRYABLE` set) rather than folding a missing database into a caller's bad-parameter
    error. Those demand opposite client behaviour - back off and retry, versus fix your input.
    """


class TimescaleCandleReader:
    """Reads the Slice-1 continuous aggregates. The only component that knows they exist."""

    def __init__(self, pool: Any, *, instruments: list[dict[str, Any]]) -> None:
        self._pool = pool
        self._instruments = instruments

    def _view_for(self, interval: str) -> tuple[str, str, str]:
        return _view_for(interval)

    async def instruments(self) -> list[dict[str, Any]]:
        return list(self._instruments)

    async def ping(self) -> None:
        """Cheapest possible liveness check - `SELECT 1`, no table access.

        Kept separate from `read` so an unreachable store is reported as `broker_unavailable`
        (503, retryable) instead of surfacing as a caller's bad-parameter 422, which would
        tell a client to stop retrying a database that is about to come back.
        """
        try:
            await self._pool.fetchval("SELECT 1")
        except Exception as exc:  # any driver failure means "not servable"
            raise MarketReaderUnavailable(
                f"market store is not reachable: {type(exc).__name__}"
            ) from exc

    async def read(
        self,
        *,
        instrument: str,
        interval: str,
        start: datetime,
        end: datetime,
        limit: int,
        cursor: str | None,
    ) -> list[dict[str, Any]]:
        if interval not in INTERVAL_MINUTES:
            raise ValueError(f"interval {interval!r} is not supported")

        view, bucket, count = self._view_for(interval)
        offset = _decode_cursor(cursor)

        # Oldest first, total order on the bucket within one (instrument, interval). `t` is
        # aliased in SQL so the handler sees the same key whichever view served it.
        sql = (
            f"SELECT {_SELECT}, {bucket} AS t, {count} AS n FROM {view} "
            "WHERE instrument_id = $1 AND " + bucket + " >= $2 AND " + bucket + " <= $3 "
            f"ORDER BY {bucket} ASC "
            f"LIMIT {limit + 1} OFFSET {offset}"
        )
        rows = await self._pool.fetch(
            sql,
            instrument,
            _aware(start),
            _aware(end),
        )

        # One extra row tells us whether another page exists without a COUNT(*), which on a
        # continuous aggregate would scan the whole window to answer a boolean. It is dropped
        # here and surfaced by `has_more`, so the caller never sees a short final page.
        return [
            {
                "t": r["t"],
                "o": r["open"], "h": r["high"], "l": r["low"], "c": r["close"],
                "n": r["n"],
                "quality_status": r["quality_status"],
                "reference_snapshot_id": r["reference_snapshot_id"],
            }
            for r in rows[:limit]
        ]

    async def has_more(
        self,
        *,
        instrument: str,
        interval: str,
        start: datetime,
        end: datetime,
        limit: int,
        cursor: str | None,
    ) -> bool:
        """Is a page after this one? One `EXISTS` probe, no COUNT over the window."""
        view, bucket, _count = _view_for(interval)
        offset = _decode_cursor(cursor)

        # Is a page after this one? One `EXISTS` probe, no COUNT over the window.
        return bool(
            await self._pool.fetchval(
                f"SELECT EXISTS (SELECT 1 FROM {view} WHERE instrument_id = $1 "
                f"AND {bucket} >= $2 AND {bucket} <= $3 ORDER BY {bucket} ASC "
                f"LIMIT 1 OFFSET {offset + limit})",
                instrument, _aware(start), _aware(end),
            )
        )

    def next_cursor(self, *, offset: int, returned: int, limit: int) -> str | None:
        """Mint the next page token, or None when the window is exhausted."""
        return _encode_cursor(offset + returned) if returned == limit else None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("PIT reads require aware datetimes; never a naive one")
    return value.astimezone(UTC)


def mint_cursor(offset: int) -> str:
    """Mint an opaque page token. The client must not parse it (proposal 7.2.2)."""
    return _encode_cursor(offset)


def cursor_offset(cursor: str | None) -> int:
    """Opaque token -> offset. A tampered token is caller error, never a 500."""
    return _decode_cursor(cursor)