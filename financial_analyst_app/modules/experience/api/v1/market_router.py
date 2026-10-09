"""M10.2 Slice 2 - the market-data read endpoints.

Two routes, and only two:

    GET /market/instruments
    GET /market/candles?instrument=&interval=&start=&end=&limit=&cursor=

WHY SO THIN. This handler owns no arithmetic and no business rule. It validates input,
resolves time, delegates the read, and puts the answer in the FROZEN envelope
(`modules/experience/api/v1/envelope.py` - `envelope_success`, `paginate`,
`validate_limit`). It is deliberately thin because thin handlers are what stay correct when
the modules underneath them change, and because the codebase already learned this the
expensive way: `repair_detected_gaps.py` grew a dependency on another component's watermark
and silently lost all coverage behind it (M2.14).

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
* It does not read the store. No `candles_1m`, no `candles_agg_*`, no `asyncpg`, no `time_bucket`.
  Aggregation is SQL-side (Slice 1); the read is behind a port so `AGENTS.md` 6a holds and
  `scripts/check_pit_guard.py` stays green. `test_router_module_does_not_import_the_store_directly`
  pins that law for this file, because the guard only covers the alpha side.
* It does not declare its own limits. `DEFAULT_LIMIT=50`/`MAX_LIMIT=500` are frozen in the
  envelope and reused via `validate_limit`, so reject-not-clamp is inherited and cannot drift.
* It does not invent an error code. Unknown interval -> `capability_not_supported` (422),
  unknown instrument -> `unknown_symbol` (404), bad limit/range/cursor -> `unknown_state`.
  A `bad_request` code would read better but is not in the frozen `ErrorCode` enum, and
  adding one is a contract change under `AGENTS.md` 5 - flagged, not invented.

Spec: docs/specs/m10.2_market_data_api.spec.md (amendments 3a, 3b).
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from modules.experience.api.v1.envelope import (
    envelope_error,
    envelope_success,
    http_status_for,
    paginate,
    validate_limit,
)
from modules.experience.api.v1.market_reader import MarketReaderUnavailable
from shared.contracts.errors import ErrorCode
from shared.timeframes import INTERVAL_MINUTES

SCHEMA_VERSION = "1.0"
BROKER = "deriv"

#: D7 thresholds. With 1m bars, anything past ~2 minutes means the feed has stopped, not
#: that the market is quiet - so LIVE is deliberately tight. Generous here would let a
#: dashboard render a dead feed as healthy, which is the exact failure that let 28.8 hours
#: of staleness go unnoticed.
LIVE_MAX_SEC = 90
DELAYED_MAX_SEC = 15 * 60

#: FLOOR on the default lookback window, in minutes. 24 hours.
#:
#: WHY THIS EXISTS (measured defect, 2026-10-08). The default window was
#: `limit * interval`, which made the reachable history depend entirely on the timeframe:
#: `limit=300` returned 5 HOURS at 1m (300 min) but 300 DAYS at 24h (432,000 min). That is
#: a 7,200x swing driven by a parameter the caller never set, and it reproduced exactly the
#: mock-era `TIMEFRAME_WINDOW` mistake this slice was written to remove. A 1m chart could
#: see five hours of the 38 days sitting in the store.
#:
#: WHY A FLOOR RATHER THAN A FIXED WINDOW. A fixed window ignores `limit` entirely. A floor
#: keeps the caller in control for coarse timeframes - asking for 300 daily bars still gets
#: 300 daily bars - while guaranteeing short intervals can never be starved of history.
#: Scrollback past the window is a cursor question, not a window question.
MIN_WINDOW_MINUTES = 24 * 60


class CandleReader(Protocol):
    """The one seam. Implemented by the PIT-backed reader, faked in tests.

    WHY a protocol and not a concrete import: this is the dependency-inversion point that
    lets the gateway be tested without a database, and it is what makes it impossible for
    this module to quietly acquire a store connection later.
    """

    async def read(
        self,
        *,
        instrument: str,
        interval: str,
        start: datetime,
        end: datetime,
        limit: int,
        cursor: str | None,
    ) -> list[dict[str, Any]]: ...

    async def instruments(self) -> list[dict[str, Any]]:
        """The canonical universe. The router validates membership against THIS rather than
        holding its own list, so there is exactly one source of truth for what exists."""
        ...

    async def ping(self) -> None:
        """Raise `MarketReaderUnavailable` when the store cannot be served.

        Separate from the reads on purpose: "the database is down" and "you sent a bad
        parameter" demand opposite client behaviour - back off and retry, versus fix the call -
        and they map to a retryable 503 and a non-retryable 422 respectively. Folding them
        together would make a recovering database look permanently broken to the client.
        """
        ...


def _require_utc(value: str | None, name: str) -> datetime | None:
    """Parse an aware ISO-8601 timestamp, or raise.

    A naive datetime is rejected outright rather than assumed UTC. Assuming local time is a
    silent one-hour error on a chart, and that class of boundary bug is exactly what made
    audit class C1 report 25/25 FAIL before the fetch window was padded.
    """
    if value is None:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware (got {value!r})")
    return parsed.astimezone(UTC)


def _floor(value: datetime, minutes: int) -> datetime:
    """Round DOWN to the bucket boundary, in UTC.

    Bitget documents that an `end` past a boundary may return one interval earlier; rounding
    down is what makes our own arithmetic immune to the same effect.
    """
    return value.replace(
        minute=(value.minute // minutes) * minutes,
        second=0,
        microsecond=0,
    )


def _classify(staleness_sec: int) -> str:
    if staleness_sec <= LIVE_MAX_SEC:
        return "LIVE"
    if staleness_sec <= DELAYED_MAX_SEC:
        return "DELAYED"
    return "STALE"


def _serialise_bar(row: dict[str, Any], *, expected: int) -> dict[str, Any]:
    """One bar for the wire.

    Prices are STRINGS. `candles_1m` stores OHLC as `double precision`, so the value has
    already been through a float; serialising it as a JSON number would invite the browser
    to do arithmetic in double precision on top of that. The frozen `Candle` contract is
    Decimal end to end (`AGENTS.md` 3.1) and the wire must not undo that.
    """
    n = int(row.get("n") or 0)
    return {
        "t": row["t"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "o": str(row["o"]),
        "h": str(row["h"]),
        "l": str(row["l"]),
        "c": str(row["c"]),
        "n": n,
        # D9: a bucket with missing minutes is surfaced, never averaged into a clean bar.
        "quality": "complete" if n >= expected else "gapped",
        "reference_snapshot_id": row.get("reference_snapshot_id"),
    }


def _err(
    code: ErrorCode, message: str, *, cid: str, as_of: datetime,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """A typed failure, as the envelope, with the status the frozen code maps to.

    WHY a JSONResponse and not `HTTPException(detail=...)`: the account router still uses the
    FastAPI string-only `detail` shape, but the M10 contract says the frontend must NOT depend
    on it (proposal 7.2) - it must be able to match a stable `error.code`. The status code is
    still the frozen mapping from `http_status_for`, never a guess.

    WHY MALFORMED-INPUT FAILURES DO NOT USE `unknown_state`. That code maps to 503 AND is in
    the envelope's `_RETRYABLE` set, so returning it for a bad `limit` or a naive timestamp
    would tell the client to retry a permanent caller bug forever. The frozen `ErrorCode`
    enum has no "the request itself is malformed" member: every 422 code is domain-specific
    (margin, exposure, qty, rr, price). `capability_not_supported` is the least-wrong
    existing code - correct status, NOT retryable - and the precise reason travels in
    `details` so the client is never misled about what was wrong.
    A `bad_request` member is the correct fix and is raised as a contract change proposal
    (docs/contracts_proposals/m10.2_bad_request.proposal.md); it is NOT invented here,
    because AGENTS.md 5 requires the change process and a human signature first.
    """
    return JSONResponse(
        status_code=http_status_for(code),
        content=envelope_error(
            code=code, message=message,
            correlation_id=cid, as_of=as_of, details=details,
        ),
    )


def _bad_request(message: str, reason: str, *, cid: str, as_of: datetime) -> JSONResponse:
    return _err(
        ErrorCode.capability_not_supported, message, cid=cid, as_of=as_of,
        details={"reason": reason},
    )


def build_market_router(reader: CandleReader, *, now: Callable[[], datetime]) -> APIRouter:
    router = APIRouter()

    def _stamp() -> tuple[str, datetime]:
        stamp = now()
        if stamp.tzinfo is None:
            raise RuntimeError("clock must return an aware datetime")
        return f"mkt-{stamp.strftime('%Y%m%dT%H%M%S%f')}", stamp

    @router.get("/market/instruments")
    async def get_instruments(
        correlation_id: str | None = Query(default=None, alias="correlation_id"),
    ) -> Any:
        cid, as_of = _stamp()
        rows = await reader.instruments()  # type: ignore[attr-defined]
        payload = {
            "instruments": rows,
            # The dropdown's options come from the backend's own registry, so the UI can
            # never offer an interval the API would then reject with a 422.
            "intervals": list(INTERVAL_MINUTES),
            "broker": BROKER,
        }
        return envelope_success(
            payload,
            correlation_id=correlation_id or cid,
            as_of=as_of,
            schema_version=SCHEMA_VERSION,
        )

    @router.get("/market/candles")
    async def get_candles(
        instrument: str = Query(..., min_length=1),
        interval: str = Query(..., min_length=1),
        start: str | None = Query(default=None),
        end: str | None = Query(default=None),
        limit: int | None = Query(default=None),
        cursor: str | None = Query(default=None),
        correlation_id: str | None = Query(default=None),
    ) -> Any:
        cid, as_of = _stamp()
        cid = correlation_id or cid

        # The store being down is NOT the caller's fault and must not be reported as one.
        # `broker_unavailable` is 503 AND retryable; `capability_not_supported` is 422 and is
        # NOT. Folding them together would tell a client to stop retrying a database that is
        # coming back in ten seconds.
        try:
            await reader.ping()
        except MarketReaderUnavailable as exc:
            return _err(ErrorCode.broker_unavailable, str(exc), cid=cid, as_of=as_of)

        universe = {row["instrument"] for row in await reader.instruments()}
        if instrument not in universe:
            return _err(
                ErrorCode.unknown_symbol, f"unknown instrument {instrument!r}",
                cid=cid, as_of=as_of,
            )
        if interval not in INTERVAL_MINUTES:
            return _err(
                ErrorCode.capability_not_supported,
                f"interval {interval!r} is not supported; expected one of "
                f"{list(INTERVAL_MINUTES)}",
                cid=cid, as_of=as_of,
            )

        minutes = INTERVAL_MINUTES[interval]
        expected = minutes  # one stored bar per minute, so a full bucket has `minutes` rows

        try:
            page_size = validate_limit(limit)
        except ValueError as exc:
            return _bad_request(str(exc), "invalid_limit", cid=cid, as_of=as_of)

        try:
            lo = _require_utc(start, "start")
            hi = _require_utc(end, "end")
        except ValueError as exc:
            return _bad_request(str(exc), "invalid_timestamp", cid=cid, as_of=as_of)

        hi = _floor(hi, minutes) if hi else _floor(as_of, minutes)
        if lo is not None:
            lo = _floor(lo, minutes)
            if lo > hi:
                return _bad_request(
                    f"start {lo.isoformat()} is after end {hi.isoformat()}",
                    "inverted_range", cid=cid, as_of=as_of,
                )
        else:
            # Floor, do not replace: `limit * interval` is honoured when it is larger, so a
            # caller asking for 300 daily bars still gets them, but a 1m request can never be
            # starved down to five hours. See MIN_WINDOW_MINUTES for the measured defect.
            lo = hi - timedelta(minutes=max(minutes * page_size, MIN_WINDOW_MINUTES))

        try:
            raw = await reader.read(
                instrument=instrument, interval=interval,
                start=lo, end=hi, limit=page_size + 1, cursor=cursor,
            )
        except ValueError as exc:      # e.g. a tampered or cross-interval cursor
            return _bad_request(str(exc), "invalid_cursor", cid=cid, as_of=as_of)

        # `paginate` is the FROZEN helper and the single authority on paging: it validates
        # the cursor offset, slices the window, and mints the next token. It is deliberately
        # offset-based because the ordering is TOTAL - `(instrument, interval, bucket)` is
        # unique - so a page boundary can neither skip nor repeat a bar. Its own note records
        # the bug it exists to prevent: an earlier revision validated the cursor and then
        # ignored its offset, so every page returned the first page and paging never advanced.
        try:
            page = paginate(raw, limit=page_size, cursor=cursor)
        except ValueError as exc:
            return _bad_request(str(exc), "invalid_cursor", cid=cid, as_of=as_of)

        bars = [_serialise_bar(r, expected=expected) for r in page.rows]
        newest = page.rows[-1]["t"] if page.rows else None
        staleness = int((as_of - newest).total_seconds()) if newest else None

        payload = {
            "instrument": instrument,
            "interval": interval,
            "bars": bars,
            # F3/D8: response-level closure flag, not a per-bar field.
            "partial_bar_included": bool(newest) and newest + timedelta(minutes=minutes) > as_of,
            # Amendment 3a: payload properties, not transport properties.
            "data_state": _classify(staleness) if staleness is not None else "STALE",
            "staleness_sec": staleness,
            "watermark": newest.strftime("%Y-%m-%dT%H:%M:%SZ") if newest else None,
            "expected_bars": expected,
            "gapped_bars": sum(1 for b in bars if b["quality"] == "gapped"),
        }
        body = envelope_success(
            payload,
            correlation_id=cid,
            as_of=as_of,
            schema_version=SCHEMA_VERSION,
        )
        # WHY overwrite instead of adding a parameter: `envelope_success` hardcodes
        # `next_cursor: None, has_more: False`, and it is frozen. `Page` is the authority on
        # paging, so its two fields are copied in here. The envelope returns a fresh dict per
        # call, and `Page` itself is frozen, so nothing that already consumed it can be
        # affected - this sets the values at construction time, not after anyone sees them.
        body["meta"]["next_cursor"] = page.next_cursor
        body["meta"]["has_more"] = page.has_more
        return body

    return router