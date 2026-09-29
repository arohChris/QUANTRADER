"""
M2.7 history fetcher - the two window-boundary contracts, pinned by test.

WRITTEN AGAINST MEASURED BEHAVIOUR, not against the code. Both defects below were found
by probing the LIVE Deriv API on 2026-09-29, and both are silent: the loader loses real
minutes and reports success. See BUILD_STATUS.md for the probe evidence.

DEFECT 1 - THE BROKER EXCLUDES THE CANDLE AT `start`.
  Proven 12/12 on live 1m FX: for a request of [S, E) with `count` = E-S, the broker
  returns candles S+1 .. E. It excludes S. So requesting [A, B) never yields the bar at
  A: exactly one minute lost per request, 90% coverage. FIX: request [A-1m, B-1m), whose
  (S, E] == [A, B-1m] is precisely the target window.

DEFECT 2 - `adjust_start_time` IS MANDATORY AND RE-ANCHORS THE WINDOW.
  Proven 7/7 on live:
    * with the flag at 0, `ticks_history` returns ZERO candles - even for a fully tradable
      window. It is not an optional refinement, it is required to get data at all.
    * when `end` lands in a closure the flag re-anchors the ENTIRE window to the last
      tradable bar and walks backward. Asked Sun 21:00->23:00, got Fri 18:56->20:55.
      The old comment described a local correction; it is a global re-anchoring.
  These tests pin both so a broker change surfaces as a FAILURE rather than as quietly
  degraded data.

Laws observed: exact assertions on the built request, no wall-clock, no network - the
transport is a fake that echoes the request back, because the CONTRACT under test is the
request we send.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from modules.m2_market_data.history import DerivHistoryFetcher

MIN = timedelta(minutes=1)
T0 = datetime(2026, 9, 28, 13, 0, tzinfo=UTC)


class EchoTransport:
    """Returns a real-shaped candles response and RECORDS the request verbatim.

    WHY echo rather than a stub: the property under test is the request we send, so the
    fake's only job is to hand the request back for inspection. A fake that invented a
    response would test nothing.
    """

    def __init__(self, candles: list[dict[str, Any]] | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self._candles = candles if candles is not None else []

    async def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(dict(request))
        return {
            "msg_type": "candles",
            "echo_req": request,
            "pip_size": 0.00001,
            "candles": self._candles,
        }


def _fetcher(transport: EchoTransport) -> DerivHistoryFetcher:
    return DerivHistoryFetcher(transport=transport)


# ------------------------------------------------- DEFECT 1: the `start` boundary


@pytest.mark.asyncio
async def test_request_is_shifted_back_one_minute_so_the_start_bar_is_covered() -> None:
    """The broker excludes the candle at `start`, so the REQUEST must be shifted back.

    Without the shift, the loader never receives the bar at A and silently loses one
    minute per request - 90% coverage measured live.
    """
    transport = EchoTransport()
    fetcher = _fetcher(transport)

    await fetcher.fetch("FX.EURUSD", "deriv", T0, T0 + timedelta(minutes=10))

    req = transport.requests[0]
    assert req["start"] == int((T0 - MIN).timestamp()), (
        "request start must be shifted back 1 minute: the broker excludes the candle "
        "at `start`, so asking from T0 loses the T0 bar"
    )
    assert req["end"] == str(int((T0 + timedelta(minutes=10) - MIN).timestamp())), (
        "request end must shift back with start, so the span - and therefore `count` - "
        "is unchanged"
    )


@pytest.mark.asyncio
async def test_shifting_preserves_the_requested_span_and_count() -> None:
    """The shift must not change how many bars we ask for. A span change would either
    over- or under-fetch, and both are silent."""
    transport = EchoTransport()
    fetcher = _fetcher(transport)

    await fetcher.fetch("FX.EURUSD", "deriv", T0, T0 + timedelta(minutes=10))

    req = transport.requests[0]
    span = int(req["end"]) - int(req["start"])
    assert span == 600, f"shifted span must stay 600s, got {span}"
    assert req["count"] == 10, f"count must stay 10, got {req['count']}"


@pytest.mark.asyncio
async def test_clip_still_uses_the_ORIGINAL_window_not_the_shifted_one() -> None:
    """WHY this is the subtle half: shifting the request must not shift what we ACCEPT.
    The broker returns bars covering [A, B-1m]; bars outside the caller's original
    [A, B) must still be rejected, or a shifted response would be written as if it were
    the requested period - which is the exact corruption DEFECT 2 can cause."""
    bars = [
        {"epoch": int((T0 - MIN).timestamp()), "open": 1, "high": 1, "low": 1, "close": 1},
        {"epoch": int(T0.timestamp()), "open": 1, "high": 1, "low": 1, "close": 1},
        {"epoch": int((T0 + 2 * MIN).timestamp()), "open": 1, "high": 1, "low": 1, "close": 1},
        {"epoch": int((T0 + 10 * MIN).timestamp()), "open": 1, "high": 1, "low": 1, "close": 1},
    ]
    transport = EchoTransport(candles=bars)
    fetcher = _fetcher(transport)

    got = await fetcher.fetch("FX.EURUSD", "deriv", T0, T0 + timedelta(minutes=10))

    starts = [b.interval_start for b in got]
    assert T0 in starts, "the bar at the caller's start must be kept"
    assert T0 + 2 * MIN in starts, "an interior bar must be kept"
    assert T0 - MIN not in starts, "a bar BEFORE the caller's window must be rejected"
    assert T0 + 10 * MIN not in starts, (
        "a bar at the caller's `end` is outside the half-open window and must be rejected"
    )


# ------------------------------------------- DEFECT 2: adjust_start_time contract


@pytest.mark.asyncio
async def test_adjust_start_time_is_always_sent_as_one() -> None:
    """MEASURED: with adjust_start_time=0 the broker returns ZERO candles, even for a
    fully tradable window. The flag is not an optimisation, it is required to get data,
    so it must never be made conditional or dropped."""
    transport = EchoTransport()
    fetcher = _fetcher(transport)

    await fetcher.fetch("FX.EURUSD", "deriv", T0, T0 + timedelta(minutes=10))

    assert transport.requests[0]["adjust_start_time"] == 1


@pytest.mark.asyncio
async def test_a_shifted_response_yields_nothing_rather_than_wrong_bars() -> None:
    """MEASURED: when `end` lands in a closure, adjust_start_time re-anchors the WHOLE
    window to the last tradable bar and walks back - asked Sun 21:00->23:00, got
    Fri 18:56->20:55. Those bars are valid OHLC for the WRONG period.

    The clip is what stops them being stored: every shifted bar falls outside the
    caller's window, so the result is empty and the caller records a CALENDAR_DISCREPANCY
    instead of writing wrong data. This test exists to pin that containment - it is the
    reason the re-anchoring was survivable."""
    friday = datetime(2026, 9, 25, 18, 56, tzinfo=UTC)
    shifted = [
        {"epoch": int(friday.timestamp()), "open": 1, "high": 1, "low": 1, "close": 1},
        {"epoch": int((friday + MIN).timestamp()), "open": 1, "high": 1, "low": 1, "close": 1},
    ]
    transport = EchoTransport(candles=shifted)
    fetcher = _fetcher(transport)

    asked = datetime(2026, 9, 27, 21, 0, tzinfo=UTC)  # Sunday evening
    got = await fetcher.fetch("FX.EURUSD", "deriv", asked, asked + timedelta(minutes=120))

    assert got == [], (
        f"a re-anchored response must yield NO bars, got {len(got)} - those bars are "
        "valid OHLC for the wrong period and must never be written"
    )


@pytest.mark.asyncio
async def test_request_keeps_the_documented_type_of_end_and_start() -> None:
    """`end` must stay the STRING form ^(latest|[0-9]{1,10})$ and `start` an integer.
    MEASURED earlier: sending an int for `end` is rejected by the broker."""
    transport = EchoTransport()
    fetcher = _fetcher(transport)

    await fetcher.fetch("FX.EURUSD", "deriv", T0, T0 + timedelta(minutes=10))

    req = transport.requests[0]
    assert isinstance(req["start"], int)
    assert isinstance(req["end"], str)
    assert json.loads(json.dumps(req))["style"] == "candles"
    assert req["granularity"] == 60
