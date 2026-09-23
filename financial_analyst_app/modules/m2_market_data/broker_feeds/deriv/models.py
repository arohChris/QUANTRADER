"""
M2.1 broker_feeds/deriv/models — typed Deriv wire-frame helpers (subscribe/ack shapes).
WHY: Keeps Deriv JSON knowledge inside broker_feeds (AGENTS §4.3); connection/ingestion stay
broker-agnostic. No execution / vault / user_id here — market-data only.
Spec: docs/specs/m2.1_connection.spec.md (files: broker_feeds/deriv/{__init__,stream,models}).
"""
from __future__ import annotations

from typing import Any, TypedDict

__all__ = ["TickSubscribeFrame"]


class TickSubscribeFrame(TypedDict):
    """Outbound ticks subscribe payload (see stream.build_tick_subscribe)."""

    ticks: str
    subscribe: int
    app_id: str


def is_tick_msg(payload: Any) -> bool:
    """True iff decoded frame is a Deriv msg_type=tick object (routing only, no parse)."""
    return isinstance(payload, dict) and payload.get("msg_type") == "tick"
