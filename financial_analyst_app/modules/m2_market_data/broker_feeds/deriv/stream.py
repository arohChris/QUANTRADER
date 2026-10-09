"""
M2.1 broker_feeds/deriv — subscribe builder + frame routing only.
WHY: Only this package may know Deriv wire JSON; connection/ingestion stay broker-agnostic
for transport loops; TickIngestor stays pure parse (m2.1_ingestion).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from shared.contracts.drop import Drop, DropReason

__all__ = ["build_tick_subscribe", "route_frame"]

OnTick = Callable[[str], Any]
#: M2.13 — venue OHLC frames (msg_type in {"ohlc", "candles"}). Same raw-payload
#: contract as OnTick: the callback decides what to make of it, so this module
#: stays a router and never grows broker-candle knowledge.
OnCandle = Callable[[str], Any]
OnDrop = Callable[[Drop], Any]


def build_tick_subscribe(symbol: str, app_id: str) -> str:
    """Deriv ticks subscribe frame. symbol is an opaque native string (e.g. frxEURUSD)."""
    return json.dumps(
        {
            "ticks": symbol,
            "subscribe": 1,
            "app_id": app_id,
        }
    )


def route_frame(
    raw: str,
    *,
    on_tick: OnTick | None = None,
    on_candle: OnCandle | None = None,
    on_drop: OnDrop | None = None,
    provider: str = "deriv",
) -> Drop | bool | None:
    """
    Route one inbound WS frame to the tick path.
    Returns: True if handed to on_tick; Drop if rejected at routing; None if ignored (ping etc.).
    Never raises on hostile payloads.
    """
    if not isinstance(raw, str):
        drop = Drop(
            reason=DropReason.invalid_json,
            provider=provider,
            detail="frame not str",
            raw_payload="",
        )
        if on_drop:
            on_drop(drop)
        return drop

    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        drop = Drop(
            reason=DropReason.invalid_json,
            provider=provider,
            detail="malformed JSON",
            raw_payload=raw[:500],
        )
        if on_drop:
            on_drop(drop)
        return drop

    if not isinstance(msg, dict):
        drop = Drop(
            reason=DropReason.missing_field,
            provider=provider,
            detail=f"frame not object type={type(msg).__name__}",
            raw_payload=raw[:500],
        )
        if on_drop:
            on_drop(drop)
        return drop

    # WHY the error check comes BEFORE the msg_type dispatch: a failed request keeps the
    # msg_type it was sent with, so `{"msg_type":"ohlc","error":{...}}` is an ERROR frame
    # wearing an ohlc label. Dispatching on msg_type first would route it to the candle
    # parser as if it carried a bar, and the real cause would be lost.
    if isinstance(msg.get("error"), dict) and msg.get("error"):
        error = msg["error"]
        drop = Drop(
            reason=DropReason.missing_field,
            provider=provider,
            detail=f"broker error {error.get('code')}: {error.get('message')}",
            raw_payload=raw[:500],
        )
        if on_drop:
            on_drop(drop)
        return drop

    msg_type = msg.get("msg_type")
    if msg_type == "tick":
        if on_tick:
            on_tick(raw)
        return True

    if msg_type in ("ohlc", "candles"):
        # MEASURED 2026-09-29: the venue streams its OWN 1-minute bar live on this socket —
        # `style:"candles"` + `subscribe:1` yields `msg_type == "ohlc"`, ~60 frames per
        # minute as the bar develops. Before this branch existed every one of those frames
        # fell through to the `return None` below and was DISCARDED, which is why the live
        # path had to digest ticks itself and why live and backfill could disagree.
        # Additive optional kwarg: a caller that passes no `on_candle` behaves exactly as
        # before, so no existing consumer changes behaviour.
        if on_candle:
            on_candle(raw)
            return True
        return None

    # ping / pong / subscription chatter — inbound for heartbeat (caller), nothing to route.
    return None
