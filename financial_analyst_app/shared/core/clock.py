"""
M1.2 — Clock — Single Source of Time
INSTITUTIONAL HEDGE FUND GRADE — WHY clock not datetime.now() directly?

  M8 Backtesting needs to replay 2024-01-01 ticks AS IF it's 2024-01-01.
  Tests need deterministic time (freeze at 10:00:00 UTC, advance 1s).
  All modules do `self.clock.now()` — never `datetime.now(timezone.utc)` directly.

  Two implementations:
    SystemClock — real time.time() for live
    SimulatedClock — fast-forward for backtesting/ticking
"""
from __future__ import annotations

import abc
import asyncio
from datetime import datetime, timedelta, timezone


class Clock(abc.ABC):
    @abc.abstractmethod
    def now(self) -> datetime:
        """Return UTC aware datetime — never naive."""

    async def sleep(self, seconds: float) -> None:
        """
        WHY sleep on clock? SimulatedClock fast-forwards without wall time.
        Live: await asyncio.sleep(seconds)
        Simulated: advance internal time instantly (no wall wait) for backtest speed.
        """
        await asyncio.sleep(seconds)


class SystemClock(Clock):
    """Live clock — real wall time."""

    def now(self) -> datetime:
        # WHY timezone.utc? Naive datetime causes DST bugs when comparing
        # exchange_timestamp vs received_at across brokers in different zones.
        return datetime.now(timezone.utc)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class SimulatedClock(Clock):
    """
    Backtest/sim clock — deterministic, no wall wait.
    WHY not just mock datetime.now? Because market data has timestamps too —
    SimulatedClock.advance() moves BOTH engine time and virtual market time together.
    """

    def __init__(self, start: datetime):
        # Enforce UTC
        if start.tzinfo is None:
            raise ValueError("SimulatedClock start must be timezone-aware UTC")
        self._t: datetime = start

    def now(self) -> datetime:
        return self._t

    def advance(self, delta: timedelta | float) -> None:
        """Move time forward — no sleep. WHY: backtest 1 year in seconds."""
        if isinstance(delta, (int, float)):
            delta = timedelta(seconds=delta)
        self._t += delta

    async def sleep(self, seconds: float) -> None:
        # WHY no actual sleep? Backtest would take hours. Instant advance.
        self.advance(timedelta(seconds=seconds))
        # Yield to event loop so other coroutines run
        await asyncio.sleep(0)
