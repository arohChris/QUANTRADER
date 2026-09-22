"""
M2.2 — Normalizer — InternalTick → MarketData | Drop.
WHY: M2.1 accepted raw frames; this slice canonicalizes identity (M2b), stamps
reference lineage, rounds price once to instrument tick_size, and allocates the
scoped processing watermark — all-or-drop, never partial MarketData.

Processing order is the spec contract (docs/specs/m2.2_normalization.spec.md):
  1 aware timestamp → 2 to_canonical → 3 exists(broker) → 4 get_instrument
  → 5 snapshot once → 6 price round once → 7 watermark → 8 frozen MarketData.

No clock dependency (purity). Watermark is the only intentional side effect
and MUST NOT run on any Drop path (no gap from failed attempts).
Consumes: shared.contracts.{internal_tick,market_data,drop,errors} — sync M2b facade.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from shared.contracts.drop import Drop, DropReason
from shared.contracts.errors import (
    ReferenceError,
    UnknownBrokerError,
    UnknownInstrumentError,
)
from shared.contracts.internal_tick import InternalTick
from shared.contracts.market_data import DataQuality, MarketData, Tick

# M2.1 quality vocabulary → MarketData DataQuality (M2.3 remains authoritative gate)
_QUALITY_MAP: dict[str, DataQuality] = {
    "ok": DataQuality.pristine,
    "gapped": DataQuality.gapped,
}
_DEFAULT_QUALITY = DataQuality.suspect  # unknown marker → fail safer, never pristine


def _round_to_tick(price: Decimal, tick_size: Decimal) -> Decimal:
    """Round once to arbitrary positive tick multiples (spec Option B, HALF_UP).

    Hand calc: 1.085405 / 0.00001 = 108540.5 → HALF_UP → 108541 → 1.08541
    Hand calc: 10.10 / 0.25 = 40.4 → 40 → 10.00
    """
    return (price / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_size


class Normalizer:
    """Sync M2.2 normalizer — injected M2b facade + M2-owned watermark registry."""

    def __init__(self, *, reference, watermark_registry) -> None:
        # WHY keyword-only + no clock: spec Inputs — unused deps forbidden; purity.
        self._reference = reference
        self._watermark = watermark_registry

    def normalize(self, tick: InternalTick) -> MarketData | Drop:
        # --- 1. Timestamp must be timezone-aware (naive → invalid_timestamp) ---
        if tick.timestamp.tzinfo is None or tick.timestamp.utcoffset() is None:
            return self._drop(tick, DropReason.invalid_timestamp, "tick.timestamp is naive (must be aware UTC)")

        broker = tick.provider

        # --- 2. Canonical identity via M2b (typed errors → Drop, never crash) ---
        try:
            instrument_id = self._reference.to_canonical(tick.symbol, broker)
        except UnknownBrokerError as exc:
            # Spec adversarial: corrupt provider (e.g. coinbase) → missing_field
            return self._drop(tick, DropReason.missing_field, f"unknown broker: {exc}")
        except UnknownInstrumentError as exc:
            return self._drop(tick, DropReason.unknown_symbol, str(exc))
        except ReferenceError as exc:
            return self._drop(tick, DropReason.missing_field, f"reference identity failure: {exc}")

        # --- 3. Broker-specific exists gate (not string-shape inference) ---
        try:
            mapped = bool(self._reference.exists(instrument_id, broker))
        except ReferenceError as exc:
            return self._drop(tick, DropReason.missing_field, f"exists check failed: {exc}")
        if not mapped:
            return self._drop(
                tick,
                DropReason.unknown_symbol,
                f"unresolved native {tick.symbol!r} — no mapping edge for broker {broker!r}",
            )

        # --- 4. Load instrument master facts (race/unknown → unknown_symbol) ---
        try:
            entry = self._reference.get_instrument(instrument_id)
        except UnknownInstrumentError as exc:
            return self._drop(tick, DropReason.unknown_symbol, str(exc))
        except ReferenceError as exc:
            return self._drop(tick, DropReason.unknown_symbol, f"instrument load failed: {exc}")

        canonical_symbol = entry.get("canonical_symbol")
        if not isinstance(canonical_symbol, str) or canonical_symbol == "":
            return self._drop(tick, DropReason.missing_field, f"instrument {instrument_id!r} missing canonical_symbol")

        # --- 5. Lineage snapshot — exactly once, stamp unchanged ---
        snapshot_id = self._reference.current_snapshot_id()
        if not snapshot_id:
            return self._drop(tick, DropReason.missing_field, "reference_snapshot_id empty/None (lineage mandatory)")

        # --- 6. Price: quote or Decimal mid; round exactly once to tick_size ---
        if "tick_size" not in entry:
            return self._drop(tick, DropReason.missing_field, f"instrument {instrument_id!r} missing tick_size")
        try:
            tick_size = Decimal(str(entry["tick_size"]))
        except Exception:
            return self._drop(tick, DropReason.missing_field, f"instrument {instrument_id!r} tick_size not numeric")
        if tick_size <= 0:
            # Spec adversarial: tick_size=0/neg → fail safe, no ZeroDivision
            return self._drop(tick, DropReason.missing_field, f"instrument {instrument_id!r} tick_size not positive: {tick_size}")

        raw_price = tick.quote if tick.quote is not None else (tick.bid + tick.ask) / 2
        rounded_price = _round_to_tick(raw_price, tick_size)

        # bid/ask unrounded; spread raw ask−bid in Decimal, then float at Tick boundary
        spread = tick.ask - tick.bid

        # --- 7. Watermark ONLY after all rejections passed (never on Drop) ---
        watermark = self._watermark.next(instrument_id, broker)

        # --- 8. Frozen canonical MarketData (all-or-drop complete) ---
        payload = Tick(
            price=float(rounded_price),
            bid=float(tick.bid),
            ask=float(tick.ask),
            spread=float(spread),
        )
        return MarketData(
            instrument_id=instrument_id,
            symbol=canonical_symbol,
            broker=broker,
            user_id=None,  # global market-data — not per-tenant
            kind="tick",
            payload=payload,
            quality=_QUALITY_MAP.get(tick.quality, _DEFAULT_QUALITY),
            watermark=watermark,
            exchange_timestamp=tick.timestamp,
            received_at=tick.observed_at,
            reference_snapshot_id=snapshot_id,
            source_sequence=tick.source_sequence,
            source_event_id=tick.source_event_id,
            provider=broker,
            provider_symbol=tick.symbol,  # exact native — never mutated
            provider_event_id=tick.source_event_id,
            schema_version="canonical_quote_v1",
        )

    @staticmethod
    def _drop(tick: InternalTick, reason: DropReason, detail: str) -> Drop:
        return Drop(reason=reason, provider=tick.provider, detail=detail, raw_payload="")


__all__ = ["Normalizer"]
