"""
M2.1 — InternalTick — M2.1-internal raw tick/event emitted after validation + dedup.
WHY separate from MarketData: M2.1 does NOT canonicalize symbols or set final quality.
M2.2 Normalization consumes InternalTick and produces frozen MarketData.
Shape follows Deriv WS tick (human + developers.deriv.com 2026-09-22):
    new API requires epoch/quote/symbol; pip_size optional; no ask_size/bid_size on provider.
Consumers: M2.1 TickIngestor, M2.2 NormalizationEngine, tests.
Version: 1.1.0 — minor: optional sizes/pip_size; quote optional on contract (M2.1 requires it for Deriv).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InternalTick(BaseModel):
    """
    Frozen M2.1 internal tick/event — validated raw tick with identity fields.
    NOT a canonical MarketData (that is M2.2 output).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Identity (distinct, never conflated — per spec)
    event_id: UUID = Field(description="Deterministic ID M2.1 assigns to each accepted tick")
    source_event_id: str | None = Field(default=None, description="Provider tick.id — primary dedup key")
    source_sequence: int | None = Field(default=None, description="Provider seq if present (gap detection only)")
    processing_sequence: int = Field(description="M2.1's own counter of accepted ticks (recovery watermark uses this)")
    observed_at: datetime = Field(description="Ingestion timestamp from clock (UTC)")

    # Provider context
    provider: str = Field(description="Broker/provider that produced the raw frame (e.g., 'deriv')")

    # Raw price fields (broker-native symbol, not yet canonicalized)
    symbol: str = Field(description="Broker-native symbol (e.g., 'frxEURUSD')")
    timestamp: datetime = Field(description="Exchange time from tick.epoch (unix seconds → UTC)")

    # Price data as Decimal; sizes optional — Deriv tick has no ask_size/bid_size
    ask: Decimal = Field(description="Ask price")
    bid: Decimal = Field(description="Bid price")
    ask_size: Decimal | None = Field(default=None, description="Ask size if provider supplies it (Deriv: absent)")
    bid_size: Decimal | None = Field(default=None, description="Bid size if provider supplies it (Deriv: absent)")
    quote: Decimal | None = Field(default=None, description="Provider mid quote if present (Deriv: tick.quote)")
    pip_size: int | None = Field(default=None, description="Decimal places hint if present (Deriv: tick.pip_size)")

    # Quality marker (M2.3 is authoritative gate; M2.1 only marks sequence gaps)
    quality: str = Field(description="Quality: 'ok' | 'gapped'")


__all__ = ["InternalTick"]
