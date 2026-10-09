"""Spec: M2.7 request/response correlation in WebSocketTransport.

WHY THIS EXISTS. `WebSocketTransport.__call__` accepted any frame whose `msg_type` was
actionable, and never checked that the frame answered the request that was actually sent.
The request already carries `"req_id": self._req_id`, and the venue demonstrably echoes it
(measured live 2026-09-30: sending req_id 901 for frxGBPCHF and 902 for frxGBPAUD returned
`{"req_id": 901, "msg_type": "candles"}` with open 1.10812 and `{"req_id": 902, ...}` with
open 1.9101 - correctly matched). So the correlation was available and unused.

The consequence is silent and severe: a late or out-of-order reply is handed back as if it
answered the current request, so the caller stores or compares ANOTHER INSTRUMENT'S BARS.
This was caught in the audit as class C1 "mismatches" that were not corrupt data at all but
crossed values:

    FX.GBPAUD stored=1.89154  ==  FX.GBPCHF broker=1.89154
    FX.GBPJPY stored=208.749  ==  FX.GBPAUD  broker=208.772

Every live cross-check is downstream of this, so a wrong reply corrupts the audit verdict,
the auto-repair, and anything the backfill writes. It is a correctness defect in a shared
frozen module, fixed under AGENTS.md 5 (patch bump: no interface change, request and
response shapes are identical - only which frame is accepted changes).

Inputs: a transport, a fake socket, and a request frame carrying a `req_id`.
Outputs: the reply whose `req_id` matches the request; frames for other requests are skipped.
Invariants:
  * a frame with a DIFFERENT req_id is never returned, no matter how actionable its msg_type
  * a frame with NO req_id at all is still accepted (the venue may omit it on some frames)
  * an error frame is returned regardless of req_id - an error IS an answer
  * skipping a foreign frame does not re-send the request, and the socket is read only once
    per frame

Correct: a reply is correlated to its request by `req_id`, never by arrival order.
Adversarial: a stream where the FIRST frame is another request's perfectly valid candle
reply, and the real reply arrives second.
"""
from __future__ import annotations

import json

import pytest

from modules.m2_market_data.history import WebSocketTransport

# ============================================================ harness


class FakeSocket:
    def __init__(self, *frames: str) -> None:
        self.sent: list[str] = []
        self.frames = list(frames)
        self.closed = False
        self.recv_calls = 0

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    async def recv(self) -> str:
        self.recv_calls += 1
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


def _req(req_id: int = 42, symbol: str = "frxEURUSD") -> dict:
    return {
        "ticks_history": symbol,
        "style": "candles",
        "granularity": 60,
        "count": 1,
        "end": "latest",
        "req_id": req_id,
    }


# ============================================================ the defect


@pytest.mark.asyncio
async def test_a_foreign_req_id_is_never_returned_as_our_reply() -> None:
    """The core defect: another request's valid-looking reply must not be accepted.

    Hand calc of intent: we ask for req_id 42. The socket first hands us a perfectly valid
    `candles` frame that answers req_id 7 - a DIFFERENT instrument's bars. Returning that
    would make us store frxGBPAUD's prices against frxEURUSD. The only acceptable result is
    the frame carrying req_id 42.
    """
    foreign = json.dumps({"req_id": 7, "msg_type": "candles",
                          "candles": [{"epoch": 1759245900, "open": "1.89154"}]})
    ours = json.dumps({"req_id": 42, "msg_type": "candles",
                       "candles": [{"epoch": 1759245900, "open": "1.08430"}]})
    t, _ = _transport(foreign, ours)

    async with t as tr:
        reply = await tr(_req())

    assert reply.get("req_id") == 42, (
        f"returned the wrong request's bars: {reply!r} - req_id 7 answered req_id 42"
    )
    assert reply["candles"][0]["open"] == "1.08430", "must be OUR instrument's data"


@pytest.mark.asyncio
async def test_two_replies_out_of_order_still_land_on_the_right_request() -> None:
    """Both replies present, reversed order. Each caller must get its own.

    This is why the transport BUFFERS a foreign reply instead of discarding it. If
    request 2's reply is thrown away while we wait on request 1, then request 2 blocks
    forever waiting for a reply that was already consumed - a hang, not a mismatch. With
    buffering, request 2 collects its answer from the buffer.
    """
    # req 2's reply arrives FIRST, req 1's second.
    second = json.dumps({"req_id": 2, "msg_type": "candles",
                         "candles": [{"epoch": 1, "open": "9.99999"}]})
    first = json.dumps({"req_id": 1, "msg_type": "candles",
                        "candles": [{"epoch": 1, "open": "1.00001"}]})
    t, sock = _transport(second, first)

    async with t as tr:
        got1 = await tr(_req(req_id=1, symbol="frxEURUSD"))
        got2 = await tr(_req(req_id=2, symbol="frxGBPAUD"))

    assert got1["candles"][0]["open"] == "1.00001", "req 1 got the wrong bars"
    assert got2["candles"][0]["open"] == "9.99999", "req 2 got the wrong bars"
    # req 1 skips the foreign frame and keeps reading - two frames consumed for one answer.
    assert sock.recv_calls == 2, (
        f"expected 2 reads for req 1, made {sock.recv_calls} - a foreign frame must be "
        "read past, not returned"
    )
    # req 2's reply was already buffered, so it is claimed WITHOUT re-sending. For an
    # idempotent history read that is strictly better: a duplicate request would be a
    # wasted venue round trip, and the answer is already in hand.
    assert len(sock.sent) == 1, (
        f"expected exactly 1 send (req 2 satisfied from the buffer), made {len(sock.sent)}"
    )


# ============================================================ what must NOT regress


@pytest.mark.asyncio
async def test_a_frame_with_no_req_id_is_still_accepted() -> None:
    """Some venue frames carry no req_id. They must not be discarded forever.

    If a frame omits req_id we cannot prove it is foreign, so it is accepted. Rejecting it
    would turn a cosmetic omission into an infinite hang waiting for a reply that will
    never come.
    """
    t, _ = _transport('{"msg_type":"candles","candles":[{"epoch":1,"open":"1.0"}]}')

    async with t as tr:
        reply = await tr(_req())

    assert "candles" in reply, "a reply with no req_id must still be accepted"


@pytest.mark.asyncio
async def test_an_error_frame_is_returned_even_without_a_matching_req_id() -> None:
    """An error IS an answer. It must not be swallowed as a 'foreign' frame.

    Swallowing it would make a rate-limit or an invalid-symbol refusal look like a hang.
    """
    t, _ = _transport(json.dumps({"req_id": 999, "error": {"code": "RateLimit", "message": "slow"}}))

    async with t as tr:
        reply = await tr(_req(req_id=42))

    assert "error" in reply, f"error frame was discarded as foreign: {reply!r}"


@pytest.mark.asyncio
async def test_ping_is_still_skipped_while_waiting() -> None:
    """Keepalive traffic must not be mistaken for a reply."""
    t, _ = _transport(
        '{"msg_type":"ping"}',
        '{"req_id":42,"msg_type":"candles","candles":[]}',
    )

    async with t as tr:
        reply = await tr(_req())

    assert "candles" in reply


@pytest.mark.asyncio
async def test_the_request_still_carries_a_req_id_to_correlate_on() -> None:
    """Guard the OTHER half of the fix: correlation is impossible without a req_id.

    A future edit that drops req_id from the request would make every frame 'foreign' or
    unmatchable. This asserts the request still identifies itself.
    """
    t, sock = _transport('{"msg_type":"candles","candles":[]}')

    async with t as tr:
        await tr(_req(req_id=1234))

    sent = json.loads(sock.sent[0])
    assert sent.get("req_id") == 1234, f"request did not identify itself: {sent!r}"
