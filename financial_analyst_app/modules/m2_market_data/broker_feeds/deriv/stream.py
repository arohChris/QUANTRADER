"""
M2.1 broker_feeds/deriv — subscribe builder + frame routing only.
WHY: Only this package may know Deriv wire JSON; connection/ingestion stay broker-agnostic
for transport loops; TickIngestor stays pure parse (m2.1_ingestion).
"""
from __future__ import annotations

import json
from typing import Any, Callable

from shared.contracts.drop import Drop, DropReason

__all__ = ["build_tick_subscribe", "route_frame"]

OnTick = Callable[[str], Any]
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

    msg_type = msg.get("msg_type")
    if msg_type == "tick":
        if on_tick:
            on_tick(raw)
        return True

    # ping / pong / subscription / error — not a tick; still inbound for heartbeat (caller).
    return None
