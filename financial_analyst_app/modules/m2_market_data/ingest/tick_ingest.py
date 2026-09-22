"""
M2.1 — TickIngestor — Deriv WS frame → InternalTick | Drop.
WHY: Transport + schema validation + dedup only — canonicalization is M2.2.
Input is the real Deriv msg_type=tick frame (nested tick{ask,bid,epoch,id?,quote,symbol};
pip_size optional; no ask_size/bid_size). Every valid frame → one InternalTick;
every reject → typed Drop (dead-letter ready).

Dedup (spec processing order): build source identity first —
tick.id when present (primary); otherwise deterministic fingerprint over
provider, symbol, epoch, quote, bid if present, ask if present, source_sequence
if present (canonical serialization, never bare (symbol, epoch, seq) alone).
Dedup runs BEFORE future-epoch / other business rules so reason is stable.

Spec: docs/specs/m2.tick_ingest.md (REV1 2026-09-22 — id optional, invalid_timestamp)
Consumes: shared.contracts.drop, shared.contracts.internal_tick, shared.core.clock
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from shared.contracts.drop import Drop, DropReason
from shared.contracts.internal_tick import InternalTick
from shared.core.clock import Clock

# Fixed namespace so event_id is deterministic across processes/instances (spec).
_EVENT_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

# QUANTRADER2-required inside tick (Deriv docs also require symbol/epoch/quote;
# bid/ask optional on Deriv but required for InternalTick — Option A, spec field table).
# tick.id is OPTIONAL (REV1) — absent → fingerprint identity/dedup fallback.
_REQUIRED_TICK_FIELDS = ("symbol", "epoch", "quote", "ask", "bid")
_FUTURE_TOLERANCE = timedelta(seconds=1)


def _to_decimal(value: Any, field: str) -> Decimal:
    """Coerce JSON number/string to Decimal. Raises ValueError on garbage."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{field} not numeric")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # Decimal(str(...)) avoids binary-float artifacts (AGENTS.md §3.1 money path)
        return Decimal(str(value))
    if isinstance(value, str):
        return Decimal(value)
    raise ValueError(f"{field} not numeric")


def _epoch_to_utc(value: Any) -> datetime:
    if isinstance(value, bool) or value is None:
        raise ValueError("epoch not int")
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError as exc:
            raise ValueError("epoch not int") from exc
    if not isinstance(value, (int, float)) or isinstance(value, float) and not float(value).is_integer():
        if isinstance(value, float) and float(value).is_integer():
            value = int(value)
        else:
            raise ValueError("epoch not int")
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


class TickIngestor:
    """Per-process ingestor for one provider's tick feed."""

    def __init__(self, provider: str, clock: Clock) -> None:
        self.provider = provider
        self.clock = clock
        self._processing_sequence = 0
        self._seen_source_event_ids: set[str] = set()
        self._seen_fingerprints: set[str] = set()
        # Per-symbol last seq — provider sequences are not comparable across instruments.
        self._last_source_sequence: dict[str, int] = {}

    def ingest(self, raw_message: str | bytes) -> InternalTick | Drop:
        # --- step 1-2: empty / decode / parse JSON (spec processing order) ---
        if isinstance(raw_message, bytes):
            if not raw_message.strip():
                return self._drop(DropReason.empty, "empty bytes")
            try:
                text = raw_message.decode("utf-8")
            except UnicodeDecodeError:
                return self._drop(DropReason.invalid_json, "not utf-8")
        else:
            text = raw_message
            if not text or not text.strip():
                return self._drop(DropReason.empty, "empty payload")

        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return self._drop(DropReason.invalid_json, "malformed JSON")
        if not isinstance(payload, dict):
            return self._drop(DropReason.missing_field, "payload is not a JSON object")

        # --- step 3: outer structure ---
        if payload.get("msg_type") != "tick":
            return self._drop(DropReason.missing_field, "msg_type is not tick")
        tick = payload.get("tick")
        if not isinstance(tick, dict):
            return self._drop(DropReason.missing_field, "missing tick object")

        # --- step 4: required fields present and non-null (Deriv + Q2 bid/ask) ---
        for field in _REQUIRED_TICK_FIELDS:
            if field not in tick or tick[field] is None:
                return self._drop(DropReason.missing_field, f"missing tick.{field}")

        symbol = tick["symbol"]
        if not isinstance(symbol, str) or not symbol:
            return self._drop(DropReason.missing_field, "missing tick.symbol")

        try:
            ask = _to_decimal(tick["ask"], "ask")
            bid = _to_decimal(tick["bid"], "bid")
            quote = _to_decimal(tick["quote"], "quote")
        except (ValueError, InvalidOperation):
            return self._drop(DropReason.invalid_price, "non-numeric price")

        if ask <= 0 or bid <= 0 or quote <= 0:
            return self._drop(DropReason.invalid_price, "non-positive price")

        # epoch type check (non-numeric → invalid_timestamp, not invalid_price — REV1)
        try:
            ts = _epoch_to_utc(tick["epoch"])
        except (ValueError, TypeError, OverflowError, OSError):
            return self._drop(DropReason.invalid_timestamp, "bad epoch")

        # --- step 5: source identity — id when present, else fingerprint ---
        raw_sei = tick.get("id")
        source_event_id: str | None
        if raw_sei is None:
            source_event_id = None
        elif isinstance(raw_sei, str) and raw_sei:
            source_event_id = raw_sei
        else:
            # id present but unusable (empty / non-string) → treat as absent (fingerprint)
            source_event_id = None

        raw_seq = tick.get("seq", payload.get("source_sequence"))
        source_sequence: int | None
        if raw_seq is None:
            source_sequence = None
        elif isinstance(raw_seq, bool) or not isinstance(raw_seq, int):
            return self._drop(DropReason.missing_field, "bad seq")
        else:
            source_sequence = raw_seq

        # --- step 6: dedup BEFORE timestamp/business rules (spec binding order) ---
        # WHY fingerprint includes provider: same symbol/epoch on Deriv vs MT5 are
        # different feeds — bare (symbol, epoch, seq) would false-positive.
        if source_event_id is not None:
            if source_event_id in self._seen_source_event_ids:
                return self._drop(DropReason.duplicate_tick, f"dup tick.id={source_event_id}")
            self._seen_source_event_ids.add(source_event_id)
        else:
            fp = self._fingerprint(symbol, ts, quote, bid, ask, source_sequence)
            if fp in self._seen_fingerprints:
                return self._drop(DropReason.duplicate_tick, "dup fingerprint")
            self._seen_fingerprints.add(fp)

        # --- step 7: timestamp / business constraints (after dedup) ---
        # Future > 1s → drop (exactly +1s accepted per spec "> 1s")
        now = self.clock.now()
        if ts > now + _FUTURE_TOLERANCE:
            return self._drop(DropReason.timestamp_ahead, "epoch ahead of clock")

        # optional sizes (not on Deriv — stay None)
        ask_size = self._optional_decimal(tick.get("ask_size"), "ask_size")
        if ask_size is _INVALID:
            return self._drop(DropReason.invalid_price, "bad ask_size")
        bid_size = self._optional_decimal(tick.get("bid_size"), "bid_size")
        if bid_size is _INVALID:
            return self._drop(DropReason.invalid_price, "bad bid_size")

        # optional pip_size
        raw_pip = tick.get("pip_size")
        pip_size: int | None
        if raw_pip is None:
            pip_size = None
        elif isinstance(raw_pip, bool) or not isinstance(raw_pip, int):
            return self._drop(DropReason.missing_field, "bad pip_size")
        else:
            pip_size = raw_pip

        # --- sequence gap marker (still emit) — per symbol ---
        quality = "ok"
        if source_sequence is not None:
            last = self._last_source_sequence.get(symbol)
            if last is not None and source_sequence != last + 1:
                quality = "gapped"
            self._last_source_sequence[symbol] = source_sequence

        # --- step 8-9: accept — processing_sequence increments only here ---
        self._processing_sequence += 1
        sei_part = source_event_id or ""
        event_id = uuid.uuid5(
            _EVENT_NS,
            f"{self.provider}|{symbol}|{ts.isoformat()}|{ask}|{bid}|{quote}|{sei_part}|{source_sequence}",
        )
        return InternalTick(
            event_id=event_id,
            source_event_id=source_event_id,
            source_sequence=source_sequence,
            processing_sequence=self._processing_sequence,
            observed_at=now,
            provider=self.provider,
            symbol=symbol,
            timestamp=ts,
            ask=ask,
            bid=bid,
            ask_size=ask_size,
            bid_size=bid_size,
            quote=quote,
            pip_size=pip_size,
            quality=quality,
        )

    @staticmethod
    def _optional_decimal(value: Any, field: str) -> Decimal | None:
        if value is None:
            return None
        try:
            return _to_decimal(value, field)
        except (ValueError, InvalidOperation):
            return _INVALID  # type: ignore[return-value]

    def _fingerprint(
        self,
        symbol: str,
        ts: datetime,
        quote: Decimal,
        bid: Decimal,
        ask: Decimal,
        source_sequence: int | None,
    ) -> str:
        # Spec identity fingerprint (fixed field order): provider, symbol, epoch, quote,
        # bid if present, ask if present, source_sequence if present — canonical encoding
        # (stable Decimal/None via str), never Python object repr.
        # WHY not bare (symbol, epoch, seq): collides across providers / misses quote-only dups.
        material = "|".join(
            (
                self.provider,
                symbol,
                ts.isoformat(),
                str(quote),
                str(bid) if bid is not None else "",
                str(ask) if ask is not None else "",
                str(source_sequence) if source_sequence is not None else "",
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _drop(self, reason: DropReason, detail: str) -> Drop:
        return Drop(reason=reason, provider=self.provider, detail=detail)


# Sentinel for optional decimal parse failure (distinct from None = absent)
_INVALID = object()

__all__ = ["TickIngestor"]
