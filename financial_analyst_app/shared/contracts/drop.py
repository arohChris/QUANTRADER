"""
M2.1 — Drop / DropReason — Rejected-tick vocabulary for market-data ingest.
WHY separate enum not ErrorCode: drop reasons are data-quality rejects, not free-text
system errors; M2.2/M2.3/M2.7 dead-letter and recovery key off these exact values.
Shared across M2.1, M2.2, and M2.3 — one vocabulary, no per-module reason names.

Consumers: M2.1 TickIngestor, M2.2 Normalization, M2.3 Validation, M2.7 recovery, tests.
Version: 1.5.0 additive (minor) 2026-09-22: + future_timestamp, negative_price,
inverted_quote (M2.3 bouncer — human LGTM with m2.3_validation.spec.md review).
Prior 1.4.0: + invalid_timestamp, + unknown_symbol.
Package stays 2.1.0 until next contracts-package cut.
No ErrorCode import — own enum per docs/specs/m2.1_ingestion.spec.md contracts note.
Full set (11): empty, invalid_json, missing_field, invalid_price, invalid_timestamp,
timestamp_ahead, duplicate_tick, unknown_symbol, future_timestamp, negative_price,
inverted_quote.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class DropReason(str, Enum):
    """Locked vocabulary for M2.1 + M2.2 + M2.3 rejection reasons (spec Edges)."""

    empty = "empty"
    invalid_json = "invalid_json"
    missing_field = "missing_field"
    invalid_price = "invalid_price"
    invalid_timestamp = "invalid_timestamp"  # non-numeric/invalid epoch (M2.1); naive InternalTick/MarketData timestamp (M2.2/M2.3)
    timestamp_ahead = "timestamp_ahead"  # M2.1 frame-level only — M2.3 uses future_timestamp
    duplicate_tick = "duplicate_tick"
    unknown_symbol = "unknown_symbol"  # unresolved native / unknown instrument (M2.2 exists() gate)
    # M2.3 bouncer (docs/specs/m2.3_validation.spec.md) — Drop 1.5.0 additive
    future_timestamp = "future_timestamp"  # exchange_timestamp > received_at + 5s
    negative_price = "negative_price"  # non-finite or Decimal(str(price)) <= 0
    inverted_quote = "inverted_quote"  # bid > ask


class Drop(BaseModel):
    """Frozen rejection record — reason + enough context for dead-letter."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: DropReason
    provider: str = Field(description="broker/provider that produced the raw frame")
    detail: str = Field(default="", description="human-readable diagnosis, not free-text reason")
    raw_payload: str = Field(default="", description="truncated raw frame for dead-letter replay")
