"""WHY: M10.2 Slice 2 - GET /market/candles and GET /market/instruments. Tests BEFORE code.

Written against `docs/specs/m10.2_market_data_api.spec.md` (section 5 edges, amendments 3a/3b)
and never against a handler that does not exist yet. RED on the current tree is the honest state.

Two things this slice deliberately does NOT invent, because both are already frozen:

  * The response envelope. `modules/experience/api/v1/envelope.py` already provides
    `envelope_success`, `envelope_error`, `paginate`, `validate_limit`, `Page`,
    `DEFAULT_LIMIT=50`, `MAX_LIMIT=500`, and 56 tests prove it. This slice CONSUMES it.
  * The pagination limits. The spec draft proposed limit 500 / max 2000; the frozen envelope
    says 50 / 500. The frozen value wins and `validate_limit` enforces it, so the reject-not-clamp
    behaviour is inherited rather than reimplemented (and cannot drift).

The thing this slice DOES own is the honesty of the response. `data_state` and `staleness_sec`
exist because the store was 28.8 hours stale for two days and nothing was able to say so. A chart
that renders stale bars without saying they are stale is worse than no chart: it looks correct.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from modules.experience.api.v1.market_reader import MarketReaderUnavailable
from modules.experience.api.v1.market_router import build_market_router
from shared.contracts.errors import ErrorCode
from shared.timeframes import INTERVAL_MINUTES


class FakeCandleReader:
    """Stands in for the PIT-backed read. Records what it was asked, so the tests can
    assert the handler never reaches for `candles_1m` directly."""

    def __init__(self, rows: list[dict] | None = None, *, newest: datetime | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.newest = newest or datetime(2026, 10, 2, 2, 0, tzinfo=UTC)
        self.calls: list[dict] = []
        # Whether another page exists after this one. Drives meta.has_more/next_cursor.
        self.more = False
        self.down = False
        # Interval each minted cursor belongs to, so a token from one interval cannot page
        # another - the client must not splice 5m bars into a 1h series.
        self.cursor_interval: str | None = None

    async def read(
        self,
        *,
        instrument: str,
        interval: str,
        start: datetime,
        end: datetime,
        limit: int,
        cursor: str | None,
    ) -> list[dict]:
        if cursor is not None and cursor != "good-cursor":
            raise ValueError(f"cursor {cursor!r} is not valid")
        if cursor is not None and self.cursor_interval not in (None, interval):
            # A token minted for a different interval. Must be refused, never honoured.
            raise ValueError(f"cursor belongs to interval {self.cursor_interval!r}")
        self.calls.append(
            {
                "instrument": instrument, "interval": interval,
                "start": start, "end": end, "limit": limit, "cursor": cursor,
            }
        )
        rows = list(self.rows)
        # The real reader returns ONE extra row so `has_more` is answerable without a COUNT.
        # Model that here, otherwise `paginate` can never see a next page and the paging tests
        # would pass for the wrong reason on a fake that no reader behaves like.
        if self.more and rows:
            step = timedelta(minutes=INTERVAL_MINUTES.get(interval, 1))
            last = rows[-1]["t"]
            while len(rows) <= limit:
                rows.append(
                    {
                        "t": last + step * (len(rows)),
                        "o": "1", "h": "2", "l": "0.5", "c": "1.5", "n": step.total_seconds() // 60,
                        "quality_status": "complete",
                        "reference_snapshot_id": "REF-2026-09-25-001",
                    }
                )
        return rows

    async def instruments(self) -> list[dict]:
        """The canonical universe lives behind the port, so the router validates
        membership against ONE source of truth rather than a list of its own."""
        return [
            {"instrument": "CRYPTO.BTCUSD", "asset_class": "crypto", "decimals": 1},
            {"instrument": "FX.EURUSD", "asset_class": "forex", "decimals": 5},
            {"instrument": "COMMODITY.XAUUSD", "asset_class": "metal", "decimals": 2},
        ]

    async def ping(self) -> None:
        if self.down:
            raise MarketReaderUnavailable("market store is not reachable: fake")

    def mint(self, interval: str) -> str:
        """Mint a cursor tagged to an interval, as the real reader does."""
        self.cursor_interval = interval
        return f"cursor-for-{interval}"


def _bar(start: datetime, o, h, l, c, n=5) -> dict:
    return {
        "t": start, "o": o, "h": h, "l": l, "c": c, "n": n,
        "quality_status": "complete", "reference_snapshot_id": "REF-2026-09-25-001",
    }


def _app(reader: FakeCandleReader, *, now: datetime | None = None) -> FastAPI:
    app = FastAPI()
    stamp = now or datetime(2026, 10, 2, 2, 0, 30, tzinfo=UTC)
    app.include_router(build_market_router(reader, now=lambda: stamp), prefix="/api/v1")
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


CANDLES = "/api/v1/market/candles"


# --------------------------------------------------------------------------
# Happy path + the fields the UI depends on
# --------------------------------------------------------------------------


async def test_candles_returns_bars_in_the_envelope() -> None:
    """Edges: the response must be the frozen envelope, not an ad-hoc shape."""
    start = datetime(2026, 10, 2, 1, 0, tzinfo=UTC)
    reader = FakeCandleReader([_bar(start, "1.0842", "1.0850", "1.0839", "1.0847")])
    async with _client(_app(reader)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m"})

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"data", "meta", "error"}
    assert body["error"] is None
    assert body["data"]["bars"][0]["o"] == "1.0842"
    assert body["meta"]["correlation_id"]
    assert body["meta"]["schema_version"]


async def test_prices_are_strings_not_json_numbers() -> None:
    """A float64 cannot hold a 5dp FX price exactly, and JS would re-round it.

    Serving a JSON number invites the browser to do arithmetic in double precision. The
    frozen `Candle` contract is Decimal end to end (AGENTS.md 3.1); the wire must not undo
    that. This is asserted on the parsed JSON's Python type, not on a formatting string.
    """
    reader = FakeCandleReader(
        [_bar(datetime(2026, 10, 2, 1, tzinfo=UTC), "1.084205", "1.09", "1.08", "1.084210")]
    )
    async with _client(_app(reader)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m"})

    bar = r.json()["data"]["bars"][0]
    for field in ("o", "h", "l", "c"):
        assert isinstance(bar[field], str), f"{field} must be a string, got {type(bar[field])}"
    assert Decimal(bar["o"]) == Decimal("1.084205")


async def test_every_supported_interval_is_accepted() -> None:
    """The dropdown must have no dead options: all 9 registry intervals must serve."""
    for tf in INTERVAL_MINUTES:
        reader = FakeCandleReader([_bar(datetime(2026, 10, 2, 1, tzinfo=UTC), "1", "2", "0.5", "1.5")])
        async with _client(_app(reader)) as c:
            r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": tf})
        assert r.status_code == 200, f"{tf} was rejected: {r.status_code} {r.text}"
        assert r.json()["data"]["interval"] == tf


# --------------------------------------------------------------------------
# partial_bar_included - F3, the response-level closure flag
# --------------------------------------------------------------------------


async def test_partial_bar_included_true_when_newest_bar_is_unfinished() -> None:
    """F3/D8. The client must be able to tell the last bar will change.

    StratAlerts' rule: when this is true, the last bar's OHLC "will change as the period
    progresses" and must not be used as a confirmed candle. Without it a 5m chart shows a
    3-minute bar as if it were closed.
    """
    now = datetime(2026, 10, 2, 2, 3, tzinfo=UTC)          # inside the 02:00-02:05 bucket
    reader = FakeCandleReader(
        [_bar(datetime(2026, 10, 2, 2, 0, tzinfo=UTC), "1", "2", "0.5", "1.5", n=3)],
        newest=datetime(2026, 10, 2, 2, 0, tzinfo=UTC),
    )
    async with _client(_app(reader, now=now)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m"})

    assert r.json()["data"]["partial_bar_included"] is True


async def test_partial_bar_included_false_when_the_bucket_has_closed() -> None:
    """At 02:06 the 02:00-02:05 bucket is finished, so no bar may be flagged partial."""
    now = datetime(2026, 10, 2, 2, 6, tzinfo=UTC)
    reader = FakeCandleReader(
        [_bar(datetime(2026, 10, 2, 2, 0, tzinfo=UTC), "1", "2", "0.5", "1.5", n=5)],
        newest=datetime(2026, 10, 2, 2, 0, tzinfo=UTC),
    )
    async with _client(_app(reader, now=now)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m"})

    assert r.json()["data"]["partial_bar_included"] is False


# --------------------------------------------------------------------------
# D7 - data_state / staleness. The reason this slice exists.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lag_seconds", "expected"),
    [(5, "LIVE"), (80, "LIVE"), (600, "DELAYED"), (3600, "STALE"), (86400, "STALE")],
)
async def test_data_state_reflects_how_old_the_newest_bar_is(lag_seconds, expected) -> None:
    """D7. Thresholds: LIVE <= 90s, DELAYED <= 15min, STALE beyond.

    Enumerated on both sides of each boundary rather than sampled, because the whole value
    of this field is that it changes at a specific point. A dashboard that renders 28-hour-old
    bars without saying so looks identical to one showing live data.
    """
    now = datetime(2026, 10, 2, 2, 0, 30, tzinfo=UTC)
    newest = now - timedelta(seconds=lag_seconds)
    reader = FakeCandleReader(
        [_bar(newest, "1", "2", "0.5", "1.5")], newest=newest
    )
    async with _client(_app(reader, now=now)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m"})

    data = r.json()["data"]
    assert data["data_state"] == expected
    assert data["staleness_sec"] == lag_seconds


async def test_data_state_and_staleness_travel_in_data_not_meta() -> None:
    """Amendment 3a. These are payload properties, not transport properties.

    An account response has no `data_state`; a market response has no pagination watermark.
    Putting them in `meta` would force every future domain to carry fields it cannot answer,
    and editing the frozen envelope would need a Change/Why/Impact/Bump proposal.
    """
    reader = FakeCandleReader([_bar(datetime(2026, 10, 2, 1, tzinfo=UTC), "1", "2", "0.5", "1.5")])
    async with _client(_app(reader)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m"})

    assert "data_state" in r.json()["data"]
    assert "staleness_sec" not in r.json()["meta"]


async def test_watermark_is_returned_so_a_client_can_reseed_its_sequence() -> None:
    """Amendment 3a. `watermark` lets a client re-seed after a timeframe switch.

    `sequence` is per topic, so switching intervals enters a NEW sequence space. A client
    that keeps its old baseline discards every subsequent frame as a regression and the
    chart silently freezes while still looking plausible. The snapshot is the only thing
    that can safely re-seed it.
    """
    reader = FakeCandleReader([_bar(datetime(2026, 10, 2, 1, tzinfo=UTC), "1", "2", "0.5", "1.5")])
    async with _client(_app(reader)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m"})

    assert "watermark" in r.json()["data"]


# --------------------------------------------------------------------------
# D9 - gaps must stay visible, never averaged away
# --------------------------------------------------------------------------


async def test_a_bucket_with_missing_minutes_is_marked_gapped() -> None:
    """D9. A venue hole must survive aggregation as a visible downgrade.

    We have real venue gaps (metals 2026-09-07, the crypto 21:07Z hole). A clean-looking bar
    built over a hole is a backtest validating data the system does not have, which
    AGENTS.md 3.4 forbids.
    """
    start = datetime(2026, 10, 2, 1, 0, tzinfo=UTC)
    reader = FakeCandleReader([
        _bar(start, "1", "2", "0.5", "1.5", n=5),
        _bar(start + timedelta(minutes=5), "1.5", "3", "1", "2", n=2),   # 2 of 5 minutes
    ])
    async with _client(_app(reader)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m"})

    data = r.json()["data"]
    assert data["bars"][1]["quality"] == "gapped"
    assert data["gapped_bars"] == 1
    assert data["bars"][0]["quality"] == "complete"


async def test_expected_bars_is_reported_so_a_short_bucket_is_explicable() -> None:
    """A bar's own row count must be carried, so `n` is auditable from the wire."""
    reader = FakeCandleReader([_bar(datetime(2026, 10, 2, 1, tzinfo=UTC), "1", "2", "0.5", "1.5", n=4)])
    async with _client(_app(reader)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m"})

    bar = r.json()["data"]["bars"][0]
    assert bar["n"] == 4
    assert r.json()["data"]["expected_bars"] == 5


# --------------------------------------------------------------------------
# Rejections - edges. Every one must be a typed error, never a silent snap.
# --------------------------------------------------------------------------


async def test_unknown_interval_is_rejected_never_snapped() -> None:
    """`7m` is not a near-neighbour of anything. Snapping it to 5m would be a lie."""
    reader = FakeCandleReader([])
    async with _client(_app(reader)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "7m"})

    assert r.status_code == 422
    assert r.json()["error"]["code"] == ErrorCode.capability_not_supported.value


async def test_calendar_bucket_interval_is_refused() -> None:
    """`shared/timeframes.py` keeps 1w/1mo/1y ABSENT until calendar semantics are spec'd.

    A weekly bar opens at a venue-defined boundary we do not model, so serving one as if it
    were a fixed duration would produce bars that disagree with the broker's own.
    """
    reader = FakeCandleReader([])
    async with _client(_app(reader)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "1w"})

    assert r.status_code == 422
    assert r.json()["error"]["code"] == ErrorCode.capability_not_supported.value


async def test_unknown_instrument_raises_unknown_symbol_404() -> None:
    """Edges: unknown instrument is a typed 404, not an empty 200 that reads as 'no data'."""
    reader = FakeCandleReader([])
    async with _client(_app(reader)) as c:
        r = await c.get(CANDLES, params={"instrument": "NOPE.NOTHING", "interval": "5m"})

    assert r.status_code == 404
    assert r.json()["error"]["code"] == ErrorCode.unknown_symbol.value


async def test_empty_window_is_200_with_empty_bars_never_404() -> None:
    """Edges: a weekend for FX, or a holiday, is a VALID empty answer.

    404 here would make a legitimate market closure look like a broken endpoint, and a
    client retrying a 404 in a loop is how a dashboard turns a quiet weekend into an alarm.
    """
    reader = FakeCandleReader([])
    async with _client(_app(reader)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m"})

    assert r.status_code == 200
    body = r.json()
    assert body["data"]["bars"] == []
    assert body["error"] is None


@pytest.mark.parametrize("limit", [0, -1, 501, 100000])
async def test_out_of_range_limit_is_rejected_not_clamped(limit: int) -> None:
    """Amendment 3b. Inherited `validate_limit`: 1..500.

    A client asking for 100,000 rows is a bug or an attack. Silently returning 500 rows
    hides it from them; an explicit 422 tells them. Both sides of the boundary, because
    clamping is exactly the failure this forbids.
    """
    reader = FakeCandleReader([])
    async with _client(_app(reader)) as c:
        r = await c.get(
            CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m", "limit": limit}
        )

    assert r.status_code == 422, f"limit={limit} was not rejected"


async def test_limit_at_the_frozen_maximum_is_accepted() -> None:
    """MAX_LIMIT=500 must actually work, or the boundary is off by one.

    The reader is deliberately asked for ONE MORE row than `limit`. That extra row is how
    `has_more` is answered without a COUNT(*) over the window; `paginate` then slices it
    back off. So 501 rows reach the reader and 500 reach the client - asserting the reader
    received exactly 500 would be asserting an implementation detail and would forbid the
    only cheap way to answer "is there a next page".
    """
    reader = FakeCandleReader([])
    async with _client(_app(reader)) as c:
        r = await c.get(
            CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m", "limit": 500}
        )

    assert r.status_code == 200
    assert reader.calls[0]["limit"] == 501


async def test_start_after_end_is_rejected() -> None:
    """Edges: an inverted range is a caller bug, not an empty result."""
    reader = FakeCandleReader([])
    async with _client(_app(reader)) as c:
        r = await c.get(
            CANDLES,
            params={
                "instrument": "FX.EURUSD", "interval": "5m",
                "start": "2026-10-02T10:00:00Z", "end": "2026-10-02T01:00:00Z",
            },
        )

    assert r.status_code == 422


async def test_tampered_cursor_is_rejected_not_500() -> None:
    """ADV: a hand-edited cursor is caller error (4xx), never an unhandled exception."""
    reader = FakeCandleReader([])
    async with _client(_app(reader)) as c:
        r = await c.get(
            CANDLES,
            params={"instrument": "FX.EURUSD", "interval": "5m", "cursor": "not-a-real-cursor"},
        )

    assert 400 <= r.status_code < 500


# --------------------------------------------------------------------------
# Timezone / boundary discipline - the C1 scar tissue
# --------------------------------------------------------------------------


async def test_naive_timestamps_are_rejected() -> None:
    """All internal time is UTC and aware. A naive datetime is a caller bug.

    A naive timestamp interpreted as local time is a silent one-hour error on a chart,
    which is precisely how the C1 audit compared every bar against its predecessor.
    """
    reader = FakeCandleReader([])
    async with _client(_app(reader)) as c:
        r = await c.get(
            CANDLES,
            params={"instrument": "FX.EURUSD", "interval": "5m", "start": "2026-10-02T01:00:00"},
        )

    assert r.status_code == 422


async def test_start_and_end_are_rounded_down_to_the_bucket_boundary() -> None:
    """Bitget documents this behaviour; it is the boundary rule, made explicit.

    An unrounded `end` silently shifts which bars fall in the response, which is the exact
    off-by-one that made audit class C1 report 25/25 FAIL.
    """
    reader = FakeCandleReader([])
    async with _client(_app(reader)) as c:
        r = await c.get(
            CANDLES,
            params={
                "instrument": "FX.EURUSD", "interval": "15m",
                "start": "2026-10-02T01:07:30Z", "end": "2026-10-02T02:41:12Z",
            },
        )

    assert r.status_code == 200
    call = reader.calls[0]
    assert call["start"] == datetime(2026, 10, 2, 1, 0, tzinfo=UTC)
    assert call["end"] == datetime(2026, 10, 2, 2, 30, tzinfo=UTC)


# --------------------------------------------------------------------------
# Instruments endpoint
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Pagination. This is the mechanism the CHART'S SCROLLBACK depends on: the user drags the
# left edge back in time, the client asks for the next older page, and the requirement is that
# page 2 continues exactly where page 1 stopped. A cursor whose ordering is not total cannot
# do that - new buckets landing between requests would shift the window and silently skip or
# repeat a bar. These tests are why `ORDER BY bucket ASC` is not optional.
# --------------------------------------------------------------------------


async def test_meta_reports_a_next_cursor_when_more_pages_exist() -> None:
    reader = FakeCandleReader(
        [_bar(datetime(2026, 10, 2, 1, 0, tzinfo=UTC), "1", "2", "0.5", "1.5")]
    )
    reader.more = True
    async with _client(_app(reader)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m"})

    assert r.json()["meta"]["has_more"] is True
    assert r.json()["meta"]["next_cursor"]


async def test_meta_reports_no_next_cursor_on_the_last_page() -> None:
    reader = FakeCandleReader(
        [_bar(datetime(2026, 10, 2, 1, 0, tzinfo=UTC), "1", "2", "0.5", "1.5")]
    )
    reader.more = False
    async with _client(_app(reader)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m"})

    assert r.json()["meta"]["has_more"] is False
    assert r.json()["meta"]["next_cursor"] is None


async def test_cursor_is_opaque_and_carries_no_readable_offset() -> None:
    """The token must not be the plain-text offset the frozen `paginate` once handed out.

    `envelope._mint_cursor` base64url-encodes `{"o": <offset>}`, and its own docstring is
    explicit about what that does and does not buy: it "hides the payload from casual
    inspection" but is "NOT a security boundary — a determined client can decode base64".

    So the guarantee asserted here is the one actually made - the raw token is not the
    literal `{"o":50}` that an earlier revision advertised, which is what let a client forge
    its own page. It is deliberately NOT asserted that the offset survives base64, because
    the frozen helper does not promise that and pretending otherwise would pin the contract
    to a stronger guarantee than it makes.
    """
    import base64
    import json as _json

    reader = FakeCandleReader([_bar(datetime(2026, 10, 2, 1, tzinfo=UTC), "1", "2", "0.5", "1.5")])
    reader.more = True
    async with _client(_app(reader)) as c:
        r = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m"})

    token = r.json()["meta"]["next_cursor"]
    assert '{"o"' not in token, "the token must not advertise its offset as plain text"
    padded = token + "=" * (-len(token) % 4)
    decoded = _json.loads(base64.urlsafe_b64decode(padded))
    assert isinstance(decoded.get("o"), int), "a decodable integer offset is the documented shape"


async def test_a_cursor_from_a_different_interval_is_rejected() -> None:
    """ADV: a token minted for 5m must not silently page a 1h query.

    If it were accepted, the client would splice 1h bars into a 5m series - which is exactly
    the "silently mix forming and closed analytics" failure the M10 proposal forbids.
    """
    reader = FakeCandleReader([_bar(datetime(2026, 10, 2, 1, tzinfo=UTC), "1", "2", "0.5", "1.5")])
    reader.more = True
    async with _client(_app(reader)) as c:
        first = await c.get(CANDLES, params={"instrument": "FX.EURUSD", "interval": "5m"})
        token = first.json()["meta"]["next_cursor"]
        reader.mint("5m")           # the token now belongs to 5m

        second = await c.get(
            CANDLES,
            params={"instrument": "FX.EURUSD", "interval": "1h", "cursor": token},
        )

    assert second.status_code == 422


async def test_instruments_lists_the_canonical_universe() -> None:
    """The asset selector needs the canonical universe, not a hand-maintained frontend list."""
    reader = FakeCandleReader()
    async with _client(_app(reader)) as c:
        r = await c.get("/api/v1/market/instruments")

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["instruments"]
    ids = [i["instrument"] for i in data["instruments"]]
    assert "CRYPTO.BTCUSD" in ids
    assert len(ids) == len(set(ids)), "instrument list must not contain duplicates"


async def test_instruments_expose_the_supported_intervals() -> None:
    """The dropdown's options must come from the backend's own registry.

    Otherwise the UI offers a timeframe the API would reject, and the user finds out by
    clicking it. The frozen registry is the single vocabulary (shared/timeframes.py).
    """
    reader = FakeCandleReader()
    async with _client(_app(reader)) as c:
        r = await c.get("/api/v1/market/instruments")

    intervals = r.json()["data"]["intervals"]
    assert intervals == list(INTERVAL_MINUTES)
    assert "1w" not in intervals


@pytest.mark.parametrize("path", ["/api/v1/market/candles", "/api/v1/market/instruments"])
async def test_responses_carry_a_correlation_id(path: str) -> None:
    """`AGENTS.md` 3.12: one correlation_id traces a request through the system."""
    reader = FakeCandleReader([])
    async with _client(_app(reader)) as c:
        r = await c.get(
            path,
            params={"instrument": "FX.EURUSD", "interval": "5m"} if "candles" in path else None,
        )

    assert r.json()["meta"]["correlation_id"]


def test_router_module_does_not_import_the_store_directly() -> None:
    """AGENTS.md 6a. Reads must not go around the PIT read path.

    `scripts/check_pit_guard.py` enforces the alpha-side rule; this pins the same law for
    the gateway itself, which the guard does not cover.

    Parsed with `ast` rather than grepped, because the module's WHY docstrings must NAME the
    things they forbid ("no `candles_1m`, no `candles_agg_*`, no `asyncpg`"). A text scan
    matches that prose and fails on a comment that is doing its job; only executable code
    can be held to the rule.
    """
    import ast
    from pathlib import Path

    src = Path("modules/experience/api/v1/market_router.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Drop every docstring, then unparse: what is left is executable code only.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    code = ast.unparse(ast.fix_missing_locations(tree))

    for banned in ("candles_1m", "time_bucket", "candles_agg_", "asyncpg", "lake."):
        assert banned not in code, (
            f"market_router.py references {banned!r} in executable code; the gateway must "
            f"read through the PIT read path, not the store (AGENTS.md 6a)"
        )


@pytest.mark.parametrize("bad", [pytest.param(None), pytest.param("")])
async def test_missing_required_params_are_rejected(bad) -> None:
    """Edges: a missing instrument is a 422 from the framework, not a default."""
    reader = FakeCandleReader([])
    async with _client(_app(reader)) as c:
        r = await c.get(CANDLES, params={"interval": "5m", "instrument": bad})

    assert r.status_code == 422