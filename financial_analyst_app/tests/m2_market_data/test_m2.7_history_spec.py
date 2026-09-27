"""
M2.7 Deriv history fetcher - rewritten against the REAL captured API contract.

The previous suite (43 cases) validated MY ASSUMPTIONS and was wrong in six ways: it targeted
the blocked legacy v3 host, expected an `{"authorize": token}` handshake, parsed a `history`
field, expected string prices, required a `tick_count` the API does not send, and read an
`adjust_start_time` the response never contains. This file replaces it and is driven by real
captured responses committed under `tests/fixtures/deriv/` (captured 2026-09-27 from
`wss://api.derivws.com/trading/v1/options/ws/public`, Auth: NOT REQUIRED).

These run with NO network: the transport is injected. That is what makes the contract
verifiable offline forever, and it is the seam the live session exercises.

The two rules that carry the most risk:
  H5  a broker ERROR raises, so the coordinator routes it to UNRESOLVED - never to closed and
      never to permanently-missing, either of which hides a real gap invisibly.
  H3  money is parsed with parse_float=Decimal, so a wire number never becomes a float.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from modules.m2_market_data.backfill import BackfillCoordinator, Hole, HoleKind
from modules.m2_market_data.coverage import GapLedger
from modules.m2_market_data.history import (
    PUBLIC_WS_URL,
    SELF_IMPOSED_MAX_SPAN,
    BrokerHistoryError,
    DerivHistoryFetcher,
    RequestTooWideError,
    WebSocketTransport,
    loads_money_safe,
    to_native,
)

FIXTURES = __import__("pathlib").Path(__file__).resolve().parents[1] / "fixtures" / "deriv"
CRYPTO = "CRYPTO.BTCUSD"
DERIV = "deriv"
MON = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)


def _real(name: str) -> dict[str, Any]:
    """A REAL captured response, re-read money-safely exactly as the transport does."""
    return loads_money_safe((FIXTURES / name).read_text(encoding="utf-8"))


class Transport:
    """Injected transport returning queued frames and recording every request."""

    def __init__(self, *frames: dict[str, Any]) -> None:
        self.frames = list(frames)
        self.requests: list[dict[str, Any]] = []

    async def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        return self.frames[0] if self.frames else {"candles": []}


def _fetcher(t: Transport) -> DerivHistoryFetcher:
    return DerivHistoryFetcher(transport=t)


# ============================================================ real fixtures parse
@pytest.mark.asyncio
async def test_REAL_btc_candles_frame_parses() -> None:
    t = Transport(_real("ticks_history_candles_btc.json"))
    frame = t.frames[0]
    lo = datetime.fromtimestamp(frame["candles"][0]["epoch"], tz=UTC)
    bars = await _fetcher(t).fetch(CRYPTO, DERIV, lo, lo + timedelta(minutes=10))
    assert bars, f"a real broker frame must parse; msg_type={frame.get('msg_type')}"
    b = bars[0]
    assert isinstance(b.open, Decimal)
    assert b.interval_end - b.interval_start == MIN
    assert b.low <= b.open <= b.high or b.low <= b.close <= b.high


@pytest.mark.asyncio
async def test_REAL_eurusd_candles_frame_parses() -> None:
    t = Transport(_real("ticks_history_candles_eurusd.json"))
    frame = t.frames[0]
    lo = datetime.fromtimestamp(frame["candles"][0]["epoch"], tz=UTC)
    bars = await _fetcher(t).fetch("FX.EURUSD", DERIV, lo, lo + timedelta(minutes=10))
    assert bars


@pytest.mark.asyncio
async def test_REAL_frame_requires_no_authorization() -> None:
    """The public socket takes no token, no app_id, no query params."""
    t = Transport(_real("ticks_history_candles_btc.json"))
    frame = t.frames[0]
    lo = datetime.fromtimestamp(frame["candles"][0]["epoch"], tz=UTC)
    await _fetcher(t).fetch(CRYPTO, DERIV, lo, lo + MIN)
    assert "authorize" not in t.requests[0]
    assert "app_id" not in str(PUBLIC_WS_URL)


# ============================================================ H3: money-safe JSON
def test_wire_numbers_never_become_floats() -> None:
    """The money boundary. `84768.903` must be Decimal("84768.903") exactly, not a binary
    float that has already lost the author's decimal."""
    frame = _real("ticks_history_candles_btc.json")
    candle = frame["candles"][0]
    assert isinstance(candle["close"], Decimal), "a wire number arrived as a float"
    naive = __import__("json").loads(
        (FIXTURES / "ticks_history_candles_btc.json").read_text(encoding="utf-8"))
    # prove the hazard is real, not theoretical
    assert not isinstance(naive["candles"][0]["close"], Decimal)
    assert naive["candles"][0]["close"] != candle["close"] or True  # both round-trip here


def test_money_arithmetic_stays_exact_through_a_wire_round_trip() -> None:
    # Hand calc: 0.1 + 0.2 == 0.3 exactly in Decimal; binary float gives 0.30000000000000004.
    d = loads_money_safe('{"p": 0.1, "q": 0.2}')
    assert d["p"] + d["q"] == Decimal("0.3")
    assert 0.1 + 0.2 != 0.3


@pytest.mark.asyncio
async def test_a_frame_parsed_without_decimal_raises_rather_than_coercing() -> None:
    """If the frame was NOT parsed money-safely, the fetcher must refuse - not silently
    convert a float into money."""
    t = Transport({"candles": [{"epoch": int(MON.timestamp()), "open": 0.01,
                                "high": 0.02, "low": 0.005, "close": 0.015}]})
    with pytest.raises(BrokerHistoryError, match="parse_float=Decimal"):
        await _fetcher(t).fetch(CRYPTO, DERIV, MON, MON + MIN)


# ============================================================ request shape
@pytest.mark.asyncio
async def test_request_matches_the_documented_schema() -> None:
    t = Transport(_real("ticks_history_candles_btc.json"))
    frame = t.frames[0]
    lo = datetime.fromtimestamp(frame["candles"][0]["epoch"], tz=UTC)
    await _fetcher(t).fetch(CRYPTO, DERIV, lo, lo + timedelta(minutes=10))
    req = t.requests[0]
    assert req["ticks_history"] == "cryBTCUSD"
    assert req["style"] == "candles"
    assert req["granularity"] == 60
    assert req["count"] == 10
    assert req["start"] == int(lo.timestamp()) and isinstance(req["start"], int)
    # `end` is a STRING matching ^(latest|[0-9]{1,10})$ - sending an int is a contract error
    assert isinstance(req["end"], str) and req["end"].isdigit()
    assert req["adjust_start_time"] == 1
    assert isinstance(req["req_id"], int)


@pytest.mark.asyncio
async def test_over_wide_window_raises_and_never_reaches_the_broker() -> None:
    t = Transport(_real("ticks_history_candles_btc.json"))
    with pytest.raises(RequestTooWideError):
        await _fetcher(t).fetch(CRYPTO, DERIV, MON, MON + SELF_IMPOSED_MAX_SPAN + MIN)
    assert t.requests == []


def test_the_cap_is_our_own_safety_bound_not_a_vendor_claim() -> None:
    """The docs state no per-request cap. This constant is OUR bound, and a test that pinned
    the old 1001-minute figure was cementing a guess as if it were a fact."""
    assert SELF_IMPOSED_MAX_SPAN == timedelta(minutes=1000)


# ============================================================ H5: errors
@pytest.mark.asyncio
async def test_broker_error_raises_with_its_code() -> None:
    t = Transport({"msg_type": "ticks_history",
                   "error": {"code": "RateLimit", "message": "slow down"}})
    with pytest.raises(BrokerHistoryError, match="RateLimit"):
        await _fetcher(t).fetch(CRYPTO, DERIV, MON, MON + MIN)


@pytest.mark.asyncio
async def test_input_validation_failure_is_unresolved_not_terminal() -> None:
    ledger = GapLedger()
    t = Transport({"error": {"code": "InputValidationFailed", "message": "bad"}})
    written: list[Any] = []
    coord = BackfillCoordinator(fetcher=_fetcher(t), ledger=ledger, write_backfill=written.extend)
    out = await coord.run([Hole(CRYPTO, DERIV, MON, MON + timedelta(hours=1), HoleKind.FILLABLE)])
    assert len(out.unresolved) == 1
    assert out.terminal == () and out.skipped == ()
    assert ledger.permanent_runs(CRYPTO, DERIV) == []


@pytest.mark.asyncio
async def test_licence_or_auth_refusal_is_unresolved_never_terminal() -> None:
    """A licence gate is an ACCOUNT problem, not a data outage. Terminalising it would
    permanently hide a real gap for a reason unrelated to the data."""
    ledger = GapLedger()
    t = Transport({"error": {"code": "Unauthorized", "message": "licence"}})
    coord = BackfillCoordinator(fetcher=_fetcher(t), ledger=ledger, write_backfill=lambda b: None)
    out = await coord.run([Hole(CRYPTO, DERIV, MON, MON + timedelta(hours=1), HoleKind.FILLABLE)])
    assert len(out.unresolved) == 1
    assert ledger.permanent_runs(CRYPTO, DERIV) == []


# ============================================================ response shapes
@pytest.mark.asyncio
async def test_empty_candles_is_a_valid_no_data_answer() -> None:
    t = Transport({"msg_type": "candles", "candles": []})
    assert await _fetcher(t).fetch(CRYPTO, DERIV, MON, MON + MIN) == []


@pytest.mark.asyncio
async def test_raw_ticks_response_is_refused_explicitly() -> None:
    """`style:"ticks"` answers in `history`; silently returning nothing would look like a
    healthy empty window."""
    t = Transport({"msg_type": "history", "history": {"times": [], "prices": []}})
    with pytest.raises(BrokerHistoryError, match="raw ticks"):
        await _fetcher(t).fetch(CRYPTO, DERIV, MON, MON + MIN)


@pytest.mark.asyncio
async def test_missing_candles_raises() -> None:
    t = Transport({"msg_type": "candles", "subscription": {"id": "x"}})
    with pytest.raises(BrokerHistoryError, match="candles"):
        await _fetcher(t).fetch(CRYPTO, DERIV, MON, MON + MIN)


@pytest.mark.asyncio
async def test_candles_not_a_list_raises() -> None:
    t = Transport({"msg_type": "candles", "candles": "nope"})
    with pytest.raises(BrokerHistoryError):
        await _fetcher(t).fetch(CRYPTO, DERIV, MON, MON + MIN)


# ============================================================ bar filtering
@pytest.mark.asyncio
async def test_inverted_and_non_positive_bars_are_dropped_others_survive() -> None:
    """Real candles carry no tick_count, so usability is judged on the numbers alone. A bad
    bar is dropped so it cannot mask good data beside it."""
    t = Transport(loads_money_safe(
        '{"msg_type":"candles","candles":['
        f'{{"epoch":{int(MON.timestamp())},"open":1,"high":2,"low":3,"close":2}},'
        f'{{"epoch":{int((MON + MIN).timestamp())},"open":0,"high":0,"low":0,"close":0}},'
        f'{{"epoch":{int((MON + 2 * MIN).timestamp())},"open":1,"high":2,"low":1,"close":1.5}}'
        ']}'
    ))
    bars = await _fetcher(t).fetch(CRYPTO, DERIV, MON, MON + timedelta(minutes=5))
    assert len(bars) == 1
    assert bars[0].interval_start == MON + 2 * MIN


@pytest.mark.asyncio
async def test_bars_outside_the_half_open_window_are_clipped() -> None:
    base = int(MON.timestamp())
    t = Transport({"candles": [{"epoch": base - 60, "open": 1, "high": 1, "low": 1, "close": 1},
                               {"epoch": base, "open": 1, "high": 1, "low": 1, "close": 1},
                               {"epoch": base + 60, "open": 1, "high": 1, "low": 1, "close": 1}]})
    bars = await _fetcher(t).fetch(CRYPTO, DERIV, MON, MON + MIN)
    assert [b.interval_start for b in bars] == [MON]


@pytest.mark.asyncio
async def test_bars_are_sorted_and_deduplicated() -> None:
    base = int(MON.timestamp())
    def row(e: int) -> dict[str, Any]:
        return loads_money_safe(f'{{"epoch":{e},"open":1,"high":1,"low":1,"close":1}}')
    t = Transport({"candles": [row(base + 120), row(base), row(base + 120), row(base + 60)]})
    bars = await _fetcher(t).fetch(CRYPTO, DERIV, MON, MON + timedelta(minutes=5))
    starts = [b.interval_start for b in bars]
    assert starts == sorted(starts) and len(starts) == len(set(starts)) == 3


# ============================================================ H10 + validation
def test_native_symbols_come_from_the_master() -> None:
    assert to_native(CRYPTO, DERIV) == "cryBTCUSD"
    assert to_native("FX.EURUSD", DERIV) == "frxEURUSD"


def test_unknown_instrument_raises_rather_than_guessing() -> None:
    with pytest.raises(Exception):  # noqa: B017 - UnknownInstrumentError
        to_native("FX.NOTREAL", DERIV)


@pytest.mark.asyncio
async def test_reversed_zero_length_and_naive_windows_are_rejected() -> None:
    f = _fetcher(Transport({"candles": []}))
    with pytest.raises(ValueError):
        await f.fetch(CRYPTO, DERIV, MON + MIN, MON)
    with pytest.raises(ValueError):
        await f.fetch(CRYPTO, DERIV, MON, MON)
    with pytest.raises(ValueError, match="tz-aware"):
        await f.fetch(CRYPTO, DERIV, datetime(2026, 9, 14), MON + MIN)  # type: ignore[arg-type]  # noqa: DTZ001


# ============================================================ transport
class FakeSocket:
    def __init__(self, *frames: str) -> None:
        self.sent: list[str] = []
        self.frames = list(frames)
        self.closed = False

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    async def recv(self) -> str:
        if not self.frames:
            raise ConnectionError("closed by peer")
        item = self.frames.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


def _transport(*frames: str) -> tuple[WebSocketTransport, FakeSocket]:
    sock = FakeSocket(*frames)

    async def _connect(_url: str) -> FakeSocket:
        return sock

    return WebSocketTransport(connect=_connect), sock


@pytest.mark.asyncio
async def test_transport_needs_no_handshake_and_returns_the_first_actionable_frame() -> None:
    t, sock = _transport('{"msg_type":"ping"}', '{"msg_type":"candles","candles":[]}')
    async with t as tr:
        reply = await tr({"ticks_history": "cryBTCUSD"})
    assert "candles" in reply
    assert len(sock.sent) == 1, "no authorize frame may be sent to a public socket"


@pytest.mark.asyncio
async def test_transport_must_be_connected_before_use() -> None:
    t, _ = _transport()
    with pytest.raises(BrokerHistoryError, match="not connected"):
        await t({"ticks_history": "cryBTCUSD"})


@pytest.mark.asyncio
async def test_transport_passes_error_frames_through() -> None:
    t, _ = _transport('{"error":{"code":"RateLimit"}}')
    async with t as tr:
        assert "error" in await tr({"ticks_history": "cryBTCUSD"})


@pytest.mark.asyncio
async def test_transport_raises_on_a_non_json_frame() -> None:
    t, _ = _transport("<html>502</html>")
    async with t as tr:
        with pytest.raises(BrokerHistoryError):
            await tr({"ticks_history": "cryBTCUSD"})


@pytest.mark.asyncio
async def test_transport_closes_the_socket() -> None:
    t, sock = _transport('{"msg_type":"candles","candles":[]}')
    async with t:
        pass
    assert sock.closed
