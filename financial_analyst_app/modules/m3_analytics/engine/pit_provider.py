"""WHY: bible §6a.4 PIT candle retrieval — the REAL `CandleProvider` behind M3.1's
already-frozen port (feature_engine.CandleProvider, §6a.4 verbatim — this slice
changes NO signature, see m3.1_provider_port spec premise correction).

Reads an injected `CandleRangePort` (structural match to m2.10
`Candles1mStore.read_1m` / m2.11 `ReadBackPort.read_1m` — zero M2 changes),
then enforces the PIT WHERE contract ABOVE WARM (A46 responsibility boundary):
completed bars only (`interval_end ≤ window_end`), available at `as_of`
(Day-1 availability proxy `interval_end ≤ as_of` — real availability_time
column = future M2 slice), chronological ascending. Fails closed on a window
that reaches past `as_of`. Never swallows store errors (engine flow 6 wraps).
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from shared.contracts.market_data import Candle


class CandleRangePort(Protocol):
    """Structural range-read port — duck-types m2.10 `Candles1mStore.read_1m`."""

    async def read_1m(
        self,
        provider: str,
        instrument_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[Candle]: ...


def _provider_for(broker: str) -> str:
    # WHY identity Day-1: M2 provider names coincide with broker names (deriv);
    # this single helper is the seam for an M2b reference mapping later (Open Q).
    return broker


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be tz-aware UTC (naive rejected)")


class PITCandleProvider:
    """§6a.4-verbatim `get_candles` over a WARM range read + PIT filters."""

    def __init__(self, store: CandleRangePort) -> None:
        self._store = store

    async def get_candles(
        self,
        instrument_id: str,
        timeframe: str,
        window_start: datetime,
        window_end: datetime,
        as_of: datetime,
        broker: str,
    ) -> list[Candle]:
        del timeframe  # WHY: wrong-series guard belongs to engine flow 6 (spec P9)
        for name, value in (
            ("window_start", window_start),
            ("window_end", window_end),
            ("as_of", as_of),
        ):
            _require_utc(value, name)
        if window_end > as_of:
            raise ValueError(
                f"window_end {window_end} > as_of {as_of} violates PIT "
                "(availability must be <= as_of) — fail closed, never clamped"
            )
        if window_start >= window_end:
            raise ValueError(
                f"invalid window: window_start {window_start} >= window_end {window_end}"
            )
        rows = await self._store.read_1m(
            _provider_for(broker), instrument_id, window_start, window_end
        )
        out = [
            candle
            for candle in rows
            if window_start <= candle.interval_start
            and candle.interval_end <= window_end  # completion — no forming candles
            and candle.interval_end <= as_of  # Day-1 availability proxy (spec Discrepancy 3)
        ]
        out.sort(key=lambda candle: candle.interval_start)  # chronological law, defensive
        return out
