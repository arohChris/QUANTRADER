"""
M2.4 — candles — Tick → Candle 1m builder (pure, stateful, no clock).
WHY: M2.3 emits validated ticks; M3/M8 need OHLC candles. Bucket from
md.exchange_timestamp only — no wall clock, deterministic, replayable.

Processing order (docs/specs/m2.4_transformation.spec.md):
  1 kind+Tick → 2 aware timestamps → 3 future defensive > received+5s →
  4 price gate → 5 floor to minute → 6 late / boundary →
  7 OHLC update → 8 quality stamp → 9 return None (open) | Candle (closed).

Spread stats all-or-none per bucket: any contributing tick missing bid/ask →
average/min/max_spread = None. Decimal mid-calc, store float per Candle contract.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from shared.contracts.drop import Drop, DropReason
from shared.contracts.market_data import Candle, DataQuality, MarketData, Tick

_FUTURE_TOLERANCE = timedelta(seconds=5)
_INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}


class CandleBuilder:
    """One builder per (instrument_id, broker, interval). Pure sync — no I/O."""

    def __init__(self, instrument_id: str, broker: str, interval: str = "1m") -> None:
        if interval not in _INTERVAL_MINUTES:
            raise ValueError(f"unsupported interval: {interval!r}")
        self.instrument_id = instrument_id
        self.broker = broker
        self.interval = interval
        self._minutes = _INTERVAL_MINUTES[interval]

        # open bucket state
        self._open_start: datetime | None = None
        self._open: dict | None = None
        self._gapped: bool = False
        # last flushed bucket start — late ticks vs closed buckets
        self._last_closed_start: datetime | None = None

    def on_tick(self, md: MarketData) -> Candle | Drop | None:
        # --- 1. Tick quotes only ---
        if md.kind != "tick" or not isinstance(md.payload, Tick):
            return self._drop(md, DropReason.missing_field, f"kind={md.kind!r}")

        exchange = md.exchange_timestamp
        received = md.received_at
        # --- 2. aware timestamps ---
        if exchange is None or received is None:
            return self._drop(md, DropReason.invalid_timestamp, "timestamp None")
        if exchange.tzinfo is None or exchange.utcoffset() is None:
            return self._drop(md, DropReason.invalid_timestamp, "exchange naive")
        if received.tzinfo is None or received.utcoffset() is None:
            return self._drop(md, DropReason.invalid_timestamp, "received naive")

        # --- 3. defensive future re-check (same as M2.3) ---
        if exchange > received + _FUTURE_TOLERANCE:
            return self._drop(md, DropReason.future_timestamp, "exchange > received+5s")

        # --- 4. price gate ---
        price = md.payload.price
        if not math.isfinite(price) or Decimal(str(price)) <= 0:
            return self._drop(md, DropReason.negative_price, f"price={price!r}")

        # --- 5. floor to minute UTC ---
        bucket_start = exchange.replace(second=0, microsecond=0, tzinfo=timezone.utc)

        # --- 6a. late: prior closed bucket (open or already flushed) ---
        if bucket_start < self._open_start if self._open_start else False:
            return self._drop(md, DropReason.invalid_timestamp, "late tick prior open bucket")
        if self._open_start is None and self._last_closed_start is not None:
            if bucket_start <= self._last_closed_start:
                return self._drop(md, DropReason.invalid_timestamp, "late tick prior closed bucket")

        closed: Candle | None = None
        # --- 6b. boundary cross: close prior open, open new ---
        if self._open_start is not None and bucket_start > self._open_start:
            closed = self._finalize()
            self._last_closed_start = self._open_start
            self._open_start = None
            self._open = None
            self._gapped = False

        # --- open or update ---
        if self._open_start is None:
            self._open_start = bucket_start
            self._gapped = md.quality == DataQuality.gapped
            self._open = {
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "tick_count": 1,
                "spreads": [],
            }
        else:
            assert self._open is not None
            self._open["high"] = max(self._open["high"], price)
            self._open["low"] = min(self._open["low"], price)
            self._open["close"] = price
            self._open["tick_count"] += 1
            if md.quality == DataQuality.gapped:
                self._gapped = True

        # spread stats — all-or-none
        tick = md.payload
        if tick.bid is not None and tick.ask is not None:
            self._open["spreads"].append(Decimal(str(tick.ask)) - Decimal(str(tick.bid)))
        else:
            self._open["spreads"].append(None)

        return closed

    def close(self) -> Candle | None:
        """Flush open bucket. Empty → None (not Drop)."""
        if self._open_start is None or self._open is None:
            return None
        c = self._finalize()
        self._last_closed_start = self._open_start
        self._open_start = None
        self._open = None
        self._gapped = False
        return c

    def _finalize(self) -> Candle:
        assert self._open is not None and self._open_start is not None
        o = self._open
        spreads = o["spreads"]
        if any(s is None for s in spreads):
            avg_spread = min_spread = max_spread = None
        else:
            dec_spreads = [s for s in spreads if s is not None]
            avg_spread = float(sum(dec_spreads) / len(dec_spreads))
            min_spread = float(min(dec_spreads))
            max_spread = float(max(dec_spreads))

        interval_end = self._open_start + timedelta(minutes=self._minutes)
        quality_status = "GAP_AFFECTED" if self._gapped else "COMPLETE"

        return Candle(
            open=o["open"],
            high=o["high"],
            low=o["low"],
            close=o["close"],
            tick_count=o["tick_count"],
            average_spread=avg_spread,
            min_spread=min_spread,
            max_spread=max_spread,
            interval=self.interval,
            interval_start=self._open_start,
            interval_end=interval_end,
            quality_status=quality_status,
            aggregation_version="agg_v2",
            expected_duration_ms=self._minutes * 60 * 1000,
        )

    def _drop(self, md: MarketData, reason: DropReason, detail: str) -> Drop:
        return Drop(
            reason=reason,
            provider=md.provider or md.broker,
            detail=detail,
            raw_payload="",
        )


__all__ = ["CandleBuilder"]
