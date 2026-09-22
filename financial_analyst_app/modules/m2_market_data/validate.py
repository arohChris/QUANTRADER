"""
M2.3 — validate — MarketData quality bouncer (pure, no deps).
WHY: M2.2 may emit future/inverted/non-positive quotes; M3 must not compute on them.
All-or-drop identity pass-through — same md instance on success, exactly one Drop on reject.

Processing order (docs/specs/m2.3_validation.spec.md):
  1 kind+Tick payload → 2 aware timestamps → 3 future > received+5s →
  4 non-finite | Decimal(str(price))<=0 → 5 bid>ask → 6 spread info-only →
  7 quality pass-through (never clear gapped) → 8 return md unchanged.

No clock / reference / watermark_registry — purity. Watermark is M2.2's only.
"""
from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal

from shared.contracts.drop import Drop, DropReason
from shared.contracts.market_data import MarketData, Tick

# WHY fixed 5s not clock: pure, deterministic, testable; bible +timedelta(seconds=5)
_FUTURE_TOLERANCE = timedelta(seconds=5)


def validate(md: MarketData) -> MarketData | Drop:
    # --- 1. Tick quotes only this slice (candle/book deferred) ---
    if md.kind != "tick" or not isinstance(md.payload, Tick):
        return _drop(md, DropReason.missing_field, f"kind={md.kind!r} payload={type(md.payload).__name__} — M2.3 validates tick only")

    # --- 2. Both timestamps timezone-aware ---
    exchange = md.exchange_timestamp
    received = md.received_at
    if exchange is None or received is None:
        return _drop(md, DropReason.invalid_timestamp, "exchange_timestamp/received_at is None")
    if exchange.tzinfo is None or exchange.utcoffset() is None:
        return _drop(md, DropReason.invalid_timestamp, "exchange_timestamp is naive")
    if received.tzinfo is None or received.utcoffset() is None:
        return _drop(md, DropReason.invalid_timestamp, "received_at is naive")

    # --- 3. Future gate: strictly greater than received + 5s ---
    # WHY received_at not clock.now(): pure; no clock dep per spec Inputs
    if exchange > received + _FUTURE_TOLERANCE:
        return _drop(md, DropReason.future_timestamp, f"exchange {exchange.isoformat()} > received+5s {received.isoformat()}")

    # --- 4. Price gate — exact form from spec Outputs ---
    price = md.payload.price
    if not math.isfinite(price) or Decimal(str(price)) <= 0:
        # non-finite (nan/inf) → negative_price (avoids IEEE nan<=0 being False)
        return _drop(md, DropReason.negative_price, f"price={price!r} non-finite or <= 0")

    # --- 5. Inverted: strict bid > ask (equal/zero spread valid; None skips) ---
    bid = md.payload.bid
    ask = md.payload.ask
    if bid is not None and ask is not None:
        if not (math.isfinite(bid) and math.isfinite(ask)):
            return _drop(md, DropReason.negative_price, f"bid/ask non-finite bid={bid!r} ask={ask!r}")
        if Decimal(str(bid)) > Decimal(str(ask)):
            return _drop(md, DropReason.inverted_quote, f"bid {bid} > ask {ask}")

    # --- 6. Spread derived info-only (M5 owns spread_too_wide — no drop here) ---
    # Inverted already caught above; extreme spread is not M2.3's job.

    # --- 7. Quality stamp: pass through unchanged (never clear gapped) ---
    # M2.3 does not invent suspect/degraded this slice — M2.6 later.

    # --- 8. Identity pass-through — same frozen instance ---
    return md


def _drop(md: MarketData, reason: DropReason, detail: str) -> Drop:
    # provider fallback: md.provider may be None in tests; Drop.provider required str
    return Drop(reason=reason, provider=md.provider or md.broker, detail=detail, raw_payload="")


__all__ = ["validate"]
