"""
M2.2 — WatermarkRegistry — process-wide market-data sequence counters.
WHY M2-owned module (not a shared contract): stateful monotonic counters scoped
by (instrument_id, broker), independent of tenant and of provider source_sequence.
Each key starts at 1; +1 only via next() after all M2.2 rejections passed.
"""
from __future__ import annotations


class WatermarkRegistry:
    """Per-(instrument_id, broker) processing sequence — starts at 1, +1 per next()."""

    def __init__(self) -> None:
        self._counters: dict[tuple[str, str], int] = {}

    def next(self, instrument_id: str, broker: str) -> int:
        key = (instrument_id, broker)
        self._counters[key] = self._counters.get(key, 0) + 1
        return self._counters[key]

    def peek(self, instrument_id: str, broker: str) -> int:
        return self._counters.get((instrument_id, broker), 0)


__all__ = ["WatermarkRegistry"]
