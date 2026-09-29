"""M2.7 — Deriv market-data adapter. The ONE place that knows broker-native protocol.

WHY this rewrite (2026-09-27): the first version was written from ASSUMPTION and was wrong in
six ways. It targeted `ws.derivws.com/websockets/v3` (blocked, HTTP 520) with an
`{"authorize": token}` handshake, and it parsed a `history` field. The real API is a
**no-auth public socket** returning a **`candles`** field, and the real responses are committed
under `tests/fixtures/deriv/` so this contract is verified against captured truth, not prose.

  Docs: https://developers.deriv.com/llms/ticks-history.md   (Auth: NOT REQUIRED)
  Gateway: wss://api.derivws.com/trading/v1/options/ws/public

THE MONEY-SAFE BOUNDARY, and the most important line in this file:

    json.loads(raw, parse_float=Decimal)

Deriv sends OHLC as JSON *numbers*. Parsed normally they become Python floats, and a float is
exactly what AGENTS.md 3.1 forbids on the money path — `Decimal(str(0.01))` only recovers the
author's literal because of a CPython repr accident, not a guarantee. Parsing the number
straight into `Decimal` keeps the wire LITERAL, so no float is ever constructed. The money law
is honoured at the true boundary rather than weakened.

TWO RULES CARRY THE MOST RISK:

  H5  A broker ERROR raises `BrokerHistoryError`, which the coordinator routes to UNRESOLVED.
      It must never become CLOSED or TERMINAL - either would permanently hide a real gap.
  H10 The broker-native symbol is resolved through the reference master, never assembled by
      string concatenation (AGENTS.md 3.2: `cryBTCUSD` is not `frxBTCUSD`).

Spec: docs/specs/m2.7_backfill.spec.md 9a
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Protocol, Self

from modules.m2_market_data.backfill import HistoryBar

__all__ = [
    "BrokerHistoryError",
    "DerivHistoryFetcher",
    "HistoryTransport",
    "RequestTooWideError",
    "WebSocketTransport",
    "loads_money_safe",
    "to_native",
]

#: The public market-data socket. NO token, NO app_id, NO query parameters.
#: Confirmed reachable 2026-09-27; the legacy v3 host returns HTTP 520.
PUBLIC_WS_URL: Final[str] = "wss://api.derivws.com/trading/v1/options/ws/public"

#: SELF-IMPOSED safety bound, NOT a vendor fact. The docs state no per-request cap; the only
#: documented limits are 100 requests/second per connection and 100 subscriptions. An earlier
#: 1001-minute figure was MEASURED against the now-superseded v3 API and is deliberately not
#: trusted here - it is a licence-dependent number that must never be hard-coded as truth.
#: This is our own conservative bound so one request cannot ask for an unbounded window.
SELF_IMPOSED_MAX_SPAN: Final[timedelta] = timedelta(minutes=1000)

#: 1-minute candles. `style: "candles"` makes the response OHLC instead of raw ticks.
GRANULARITY: Final[int] = 60


class BrokerHistoryError(RuntimeError):
    """The broker did not give us usable history.

    WHY a distinct type: the coordinator maps this to UNRESOLVED - "we could not find out" -
    which is different in kind from an empty answer (terminal closure) and from a market
    closure. Conflating them is the bug this type exists to prevent.
    """


class RequestTooWideError(BrokerHistoryError):
    """The caller asked for more than one request should carry.

    WHY raise rather than clamp: a silently truncated response is indistinguishable from a
    complete one, so the unrepaired tail would vanish from the queue with nothing to say so.
    """


def loads_money_safe(raw: str | bytes) -> Any:
    """`json.loads` that never produces a float.

    This is the single boundary where broker numbers become our money. Passing
    `parse_float=Decimal` means a wire literal like `84768.903` becomes `Decimal("84768.903")`
    exactly, instead of a binary float that has already lost the author's decimal.
    """
    return json.loads(raw, parse_float=Decimal)


class HistoryTransport(Protocol):
    """Sends one request object, returns the response object."""

    async def __call__(self, request: dict[str, Any]) -> dict[str, Any]: ...


def to_native(instrument_id: str, broker: str) -> str:
    """Canonical id -> broker-native symbol, via the reference master.

    WHY never `f"frx{symbol}"`: string-building a native symbol is what AGENTS.md 3.2 forbids
    outside the adapter, and it silently breaks for any symbol whose native form is not
    derivable - `cryBTCUSD` is not `frxBTCUSD`.
    """
    from modules.reference.reference import to_native as _to_native

    return _to_native(instrument_id, broker)


def _price(raw: Any, field: str) -> Decimal:
    """Exact money from an already-parsed value.

    WHY `int` is accepted and `float` is not - the subtlety that `parse_float=Decimal` hides:
    it converts JSON numbers that HAVE a decimal point or exponent, so a wire value of `1`
    still arrives as a Python `int`. An `int` is **exact** - there is no precision to lose -
    so rejecting it would break on legitimate whole-number prices. A `float` is a binary
    approximation and is the ONLY genuinely lossy case, so it is the only one refused. The
    money law forbids lossiness, not integers.
    """
    if isinstance(raw, bool):
        raise BrokerHistoryError(f"{field}: bool is not a price")
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, Decimal):
        if not raw.is_finite():
            raise BrokerHistoryError(f"{field}: {raw!r} is not a finite price")
        return raw
    if isinstance(raw, float):
        raise BrokerHistoryError(
            f"{field}: got a float ({raw!r}); the frame must be parsed with "
            "parse_float=Decimal so money is never built from a lossy binary float "
            "(AGENTS.md 3.1)"
        )
    if isinstance(raw, str):
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise BrokerHistoryError(f"{field}: {raw!r} is not a decimal") from exc
        if not value.is_finite():
            raise BrokerHistoryError(f"{field}: {raw!r} is not a finite price")
        return value
    raise BrokerHistoryError(f"{field}: expected a number, got {type(raw).__name__}")


class DerivHistoryFetcher:
    """Fetches 1-minute candles, one window per call. The coordinator owns chunking."""

    def __init__(self, transport: HistoryTransport, *, max_span: timedelta | None = None) -> None:
        self._transport = transport
        self._max_span = max_span or SELF_IMPOSED_MAX_SPAN
        self._req_id = 0

    @property
    def max_span(self) -> timedelta:
        return self._max_span

    async def fetch(self, instrument_id: str, broker: str,
                    start: datetime, end: datetime) -> list[HistoryBar]:
        start, end = self._validate(start, end)
        if end - start > self._max_span:
            cap = int(self._max_span.total_seconds() // 60)
            raise RequestTooWideError(
                f"{instrument_id}: window {end - start} exceeds our {cap}-minute request "
                "bound - refusing rather than silently truncating"
            )
        self._req_id += 1
        # WHY the request window is shifted back one minute, MEASURED live 2026-09-29:
        # with `count` set, `ticks_history` returns the `count` bars ENDING AT `end`
        # (i.e. (end - count*60, end]) - `start` does NOT select bars, verified by
        # changing `start` alone and getting an identical response. The caller's window
        # is [A, B) = N bars at A .. B-60, but asking end=B returns B-60N+60 .. B: the
        # bar at A is silently lost AND one bar outside the window comes back. Asking
        # end=B-60 returns exactly A .. B-60. `start` is also shifted back so the
        # adjustment anchor cannot pull the window earlier than the caller's own start.
        # The span - and therefore `count` - is unchanged, so this cannot over-fetch.
        # WHY the clip is NOT shifted: `start`/`end` above stay the CALLER's window, and
        # the response filter below enforces them. If a response is re-anchored (see
        # `adjust_start_time`) its bars fall outside the caller's window and are dropped
        # rather than stored as if they were the requested period.
        req_start = start - timedelta(minutes=1)
        req_end = end - timedelta(minutes=1)
        request = {
            "ticks_history": to_native(instrument_id, broker),
            "style": "candles",
            "granularity": GRANULARITY,
            "count": int((end - start).total_seconds() // 60),
            # `end` is a STRING matching ^(latest|[0-9]{1,10})$ per the docs; `start` is an
            # integer. The asymmetry is real and sending an int for `end` is rejected.
            "end": str(int(req_end.timestamp())),
            "start": int(req_start.timestamp()),
            # MEASURED 2026-09-29. Correction 2026-09-29: an earlier note here claimed
            # `adjust_start_time: 0` "returns ZERO candles" - that was WRONG. It returns
            # `InputValidationFailed`, and the probe recorded the error as an empty
            # result. The value 0 is simply not accepted, so 1 is the only usable setting.
            # The re-anchoring half of the original claim IS real and still measured:
            #   * when `end` lands in a closure the flag anchors to the last available
            #     candle and walks BACKWARD `count` bars: asked Sun 21:00->23:00 (120m),
            #     got Fri 18:56->20:55. Those bars are valid OHLC for the WRONG period.
            # The caller's-window clip below is what contains that; the driver also never
            # requests inside a closure, so it does not arise in practice.
            "adjust_start_time": 1,
            "req_id": self._req_id,
        }
        try:
            response = await self._transport(request)
        except BrokerHistoryError:
            raise
        except Exception as exc:
            raise BrokerHistoryError(f"{instrument_id}: transport failed: {exc}") from exc
        return self._parse(response, instrument_id, broker, start, end, request)

    # -- internals -----------------------------------------------------------------------
    @staticmethod
    def _validate(start: datetime, end: datetime) -> tuple[datetime, datetime]:
        for label, value in (("start", start), ("end", end)):
            if not isinstance(value, datetime):
                raise TypeError(f"{label} must be a datetime, got {type(value).__name__}")
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{label} must be tz-aware (UTC); naive datetimes are rejected")
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        if end == start:
            raise ValueError("window has zero length: end == start")
        if end < start:
            raise ValueError(f"end {end.isoformat()} precedes start {start.isoformat()}")
        return start, end

    def _parse(self, response: dict[str, Any], instrument_id: str, broker: str,
               start: datetime, end: datetime, request: dict[str, Any]) -> list[HistoryBar]:
        if not isinstance(response, dict):
            raise BrokerHistoryError(f"{instrument_id}: response was not an object")
        # H5: an explicit broker error is UNRESOLVED, never a closure.
        if "error" in response:
            err = response["error"]
            code = err.get("code", "unknown") if isinstance(err, dict) else "unknown"
            raise BrokerHistoryError(f"{instrument_id}: broker error {code}")
        # `style: "candles"` answers in `candles`; `style: "ticks"` answers in `history`
        # (an object of `times`/`prices` arrays). Only candles are supported here, and saying
        # so is better than silently returning nothing for a ticks response.
        rows = response.get("candles")
        if rows is None:
            if "history" in response:
                raise BrokerHistoryError(
                    f"{instrument_id}: broker returned raw ticks, not candles; "
                    "style='candles' was requested"
                )
            raise BrokerHistoryError(f"{instrument_id}: response has no 'candles' member")
        if not isinstance(rows, list):
            raise BrokerHistoryError(f"{instrument_id}: 'candles' was not a list")

        out: dict[datetime, HistoryBar] = {}
        for row in rows:
            bar = self._bar(row, instrument_id, broker, start, end)
            if bar is not None:
                out[bar.interval_start] = bar
        return [out[k] for k in sorted(out)]

    def _bar(self, row: Any, instrument_id: str, broker: str,
             start: datetime, end: datetime) -> HistoryBar | None:
        if not isinstance(row, dict):
            return None
        epoch = row.get("epoch")
        if not isinstance(epoch, int) or isinstance(epoch, bool):
            return None
        at = datetime.fromtimestamp(epoch, tz=UTC)
        # Half-open window: a bar starting exactly at `end` belongs to the next chunk.
        if not (start <= at < end):
            return None
        o = _price(row.get("open"), "open")
        h = _price(row.get("high"), "high")
        low = _price(row.get("low"), "low")
        c = _price(row.get("close"), "close")
        # Real `candles` carry NO tick_count, so usability is judged on the numbers alone.
        # An inverted range or a non-positive price is bad DATA (dropped); a malformed type
        # would already have raised in `_price`.
        if low > h or min(o, h, low, c) <= 0:
            return None
        return HistoryBar(
            instrument_id=instrument_id, broker=broker,
            interval_start=at, interval_end=at + timedelta(seconds=GRANULARITY),
            open=o, high=h, low=low, close=c, tick_count=1, source="backfill:deriv",
        )


class WebSocketTransport:
    """Live public-socket transport. No token, no OTP, no app_id.

    The connector is injectable so the transport's own failure handling is testable with no
    network - otherwise it would only ever be discovered broken during a trading session.
    """

    #: Response types we act on. Anything else (ping, echo_req, subscription chatter) is
    #: skipped rather than returned, so a keepalive cannot be mistaken for an answer.
    _ACTIONABLE = ("candles", "history", "error", "active_symbols", "trading_times")

    def __init__(self, *, url: str = PUBLIC_WS_URL,
                 connect: Callable[[str], Awaitable[Any]] | None = None,
                 timeout: float = 25.0) -> None:
        self._url = url
        self._connect = connect
        self._timeout = timeout
        self._ws: Any = None

    async def __aenter__(self) -> Self:
        if self._connect is not None:
            self._ws = await self._connect(self._url)
        else:
            self._ws = await websockets_connect(self._url)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._ws is None:
            raise BrokerHistoryError("transport is not connected; use `async with`")
        await self._ws.send(json.dumps(request))
        while True:
            raw = await asyncio_wait(self._ws, self._timeout)
            try:
                frame = loads_money_safe(raw)
            except ValueError as exc:
                # A non-JSON frame (an HTML gateway error, a proxy page) must become the TYPED
                # error the coordinator routes to UNRESOLVED. Letting a raw JSONDecodeError
                # escape would make a broker/proxy hiccup indistinguishable from a bug here.
                raise BrokerHistoryError(f"malformed broker response: {exc}") from exc
            if not isinstance(frame, dict):
                raise BrokerHistoryError(
                    f"broker response was {type(frame).__name__}, not an object"
                )
            if "error" in frame:
                return frame
            if frame.get("msg_type") in self._ACTIONABLE:
                return frame


async def websockets_connect(url: str) -> Any:
    import websockets

    return await websockets.connect(url, open_timeout=15, close_timeout=3)


async def asyncio_wait(ws: Any, timeout: float) -> Any:
    import asyncio

    return await asyncio.wait_for(ws.recv(), timeout=timeout)
