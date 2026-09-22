"""M2.1 tick ingest — raw broker WS frames to validated MarketData events.

WHY: Every downstream module (M3 analytics, M4 signal, M5 risk, M8 backtest)
consumes MarketData. A single malformed tick must never crash the pipeline or
be silently accepted. Drops are first-class: every rejected frame is logged,
counted, and dead-lettered for M2.7 gap recovery.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from shared.contracts.base import ErrorCode, utc_now
from shared.contracts.drop import Drop, DropReason
from shared.contracts.market_data import MarketData, MarketDataQuality


class TickIngestor:
    """Validates raw WS frames and emits MarketData or Drop."""

    def __init__(self, provider: str, symbol: str, clock) -> None:
        self.provider = provider
        self.symbol = symbol
        self.clock = clock
        self.drops: list[Drop] = []
        self.emitted: list[MarketData] = []
        self._seen: set[tuple[str, str, int]] = set()  # dedupe by (symbol, ts, seq)

    def ingest(self, raw: str | bytes, correlation_id: str) -> MarketData | None:
        """Process one raw WS frame. Returns MarketData if valid, else None (Drop recorded)."""
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            self._drop(correlation_id, DropReason.INVALID_SCHEMA, "empty", ErrorCode.DROP_INVALID_SCHEMA)
            return None
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload: dict[str, Any] = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._drop(correlation_id, DropReason.INVALID_SCHEMA, "invalid_json", ErrorCode.DROP_INVALID_SCHEMA)
            return None

        # Required fields
        required = ("symbol", "timestamp", "ask", "bid", "ask_size", "bid_size")
        missing = [f for f in required if f not in payload or payload[f] is None]
        if missing:
            self._drop(
                correlation_id,
                DropReason.INVALID_SCHEMA,
                f"missing:{','.join(missing)}",
                ErrorCode.DROP_MISSING_FIELD,
            )
            return None

        # Validate types
        try:
            ts_str = str(payload["timestamp"])
            ask = float(payload["ask"])
            bid = float(payload["bid"])
            ask_size = float(payload["ask_size"])
            bid_size = float(payload["bid_size"])
            seq = int(payload.get("sequence", 0))
        except (TypeError, ValueError):
            self._drop(
                correlation_id,
                DropReason.INVALID_SCHEMA,
                "type_error",
                ErrorCode.DROP_INVALID_PRICE,
            )
            return None

        if ask <= 0 or bid <= 0 or ask < bid:
            self._drop(
                correlation_id,
                DropReason.INVALID_PRICE,
                f"ask={ask} bid={bid}",
                ErrorCode.DROP_INVALID_PRICE,
            )
            return None

        # Timestamp checks
        now = self.clock.now()
        try:
            from datetime import datetime, timezone
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            self._drop(
                correlation_id,
                DropReason.TIMESTAMPED,
                f"bad_ts={ts_str}",
                ErrorCode.DROP_TIMESTAMPED,
            )
            return None

        # Future timestamp guard (>1s ahead)
        if (ts - now).total_seconds() > 1.0:
            self._drop(
                correlation_id,
                DropReason.TIMESTAMPED,
                f"ahead={(ts - now).total_seconds():.3f}s",
                ErrorCode.DROP_TIMESTAMPED,
            )
            return None

        # Dedupe
        dedupe_key = (self.symbol, ts.isoformat(), seq)
        if dedupe_key in self._seen:
            self._drop(
                correlation_id,
                DropReason.TIMESTAMPED,
                f"dup seq={seq}",
                ErrorCode.DROP_TIMESTAMPED,
            )
            return None
        self._seen.add(dedupe_key)

        # Sequence gap → gapped quality (M2.7 recovers)
        quality = MarketDataQuality.OK
        if self.emitted:
            last_seq = self.emitted[-1].sequence
            if seq > last_seq + 1:
                quality = MarketDataQuality.GAPPED

        md = MarketData(
            correlation_id=correlation_id,
            provider=self.provider,
            symbol=payload["symbol"],
            timestamp=ts,
            observed_at=now,
            ask=ask,
            bid=bid,
            ask_size=ask_size,
            bid_size=bid_size,
            sequence=seq,
            reference_snapshot_id="",
            quality=quality,
        )
        self.emitted.append(md)
        return md

    def _drop(
        self,
        correlation_id: str,
        reason: DropReason,
        detail: str,
        error_code: ErrorCode,
    ) -> None:
        d = Drop(
            correlation_id=correlation_id,
            provider=self.provider,
            symbol=self.symbol,
            reason=reason,
            detail=detail,
            raw_payload=detail[:200],
            error_code=error_code,
        )
        self.drops.append(d)

    @property
    def drop_count(self) -> int:
        return len(self.drops)

    @property
    def emit_count(self) -> int:
        return len(self.emitted)
