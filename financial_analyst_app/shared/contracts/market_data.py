"""
M1.1 — MarketData — The ONLY output of M2 Data.
INSTITUTIONAL HEDGE FUND GRADE

WHY: M2 is the ONLY writer of MarketData. M3/M4 trust its quality field to decide
     "compute or skip". Watermark is monotonic per symbol+broker+user — gap detection.
     Uses canonical symbol (EURUSD) not broker-native (frxEURUSD/R_75) so M3 calc is shared.
     P95 tick→parquet <50ms — this contract must be tiny and frozen.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .base import BaseContract


class DataQuality(str, Enum):
    """
    WHY not bool? 'pristine' vs 'gapped' vs 'suspect' have different downstream actions:
      pristine → M3 computes
      gapped   → M3 skips unless strategy allows degraded
      suspect  → M3 may compute but tags warning (tick spike)
    Maps to DataQuality + COMPLETE/LOW_ACTIVITY/GAP_AFFECTED in Lake candles.
    """

    pristine = "pristine"
    delayed = "delayed"
    gapped = "gapped"
    suspect = "suspect"
    degraded = "degraded"
    # Candle aggregation quality (M2.4 post-aggregation)
    COMPLETE = "COMPLETE"
    LOW_ACTIVITY = "LOW_ACTIVITY"
    GAP_AFFECTED = "GAP_AFFECTED"


class CapabilityStatus(str, Enum):
    """M3.0 — gates families. WHY: Deriv has no depth → order_book_imbalance UNAVAILABLE."""

    FULLY_SUPPORTED = "FULLY_SUPPORTED"
    DERIVED = "DERIVED"  # e.g., ATR from tick range proxy — honest
    PROXY_LIMITED = "PROXY_LIMITED"  # tick_count as volume proxy — must warn UI
    UNAVAILABLE = "UNAVAILABLE"  # order_book_imbalance on Deriv
    REQUIRES_EXTERNAL_DATA = "REQUIRES_EXTERNAL_DATA"


# ---- Payloads — the actual data inside MarketData (frozen, tiny) ----


class Tick(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    price: float = Field(description="Canonical mid or quote price")
    bid: float | None = None
    ask: float | None = None
    spread: float | None = None  # derived mid spread


class Candle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    open: float
    high: float
    low: float
    close: float
    tick_count: int = 0
    average_spread: float | None = None
    min_spread: float | None = None
    max_spread: float | None = None
    interval: str = Field(description="1m, 5m, 1h, 1d")
    interval_start: datetime
    interval_end: datetime
    quality_status: str = "COMPLETE"
    aggregation_version: str = "agg_v2"
    expected_duration_ms: int = 60000
    observed_duration_ms: int | None = None


class OrderBookSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    bids: list[tuple[float, float]]  # [(price, qty), ...]
    asks: list[tuple[float, float]]
    sequence: int | None = None


class TradePrint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    price: float
    qty: float
    side: Literal["buy", "sell"] | None = None


class MarketData(BaseContract):
    """
    The canonical tick/candle/book contract — v1.4 CORRECTED [18-point audit].
    WHY frozen? Tick must not be mutated after normalization — audit needs exact tick.
    WHY watermark? Gap detection for M2's processing_sequence: 1001,1002,1003 — if 1002 missing, M2.7 backfills.
        Distinct from source_sequence (broker-provided, may be None).
    WHY quality? So M3 doesn't compute RSI on gapped interval and fire wrong Signal.
    v1.4: instrument_id is primary canonical key (FX.EURUSD), symbol is display alias, reference_snapshot_id stamps lineage.
    Tenant: storage is GLOBAL (provider/instrument, no user_id), connections are per-tenant. M2b is shared.
    """

    # v1.4 canonical identity — primary key is instrument_id, symbol is display
    instrument_id: str | None = Field(
        default=None,
        description="Canonical instrument id — FX.EURUSD, SYNTHETIC.VOLATILITY_75, INDEX.DE40, EQUITY.AAPL — primary key (v1.4)",
    )
    symbol: str = Field(description="Display/canonical: EURUSD, BTCUSD, Volatility 75 Index — alias for instrument_id suffix (backwards compat)")
    broker: str = Field(description="deriv | mt5 | headway | canonical")
    # Per-tenant — deprecated for market-data storage (now global), kept for private signals/orders
    user_id: str | None = Field(default=None, description="Tenant user_id — for private data only, NOT for global market-data storage")
    kind: Literal["tick", "quote", "trade", "candle", "order_book"] = Field(description="What payload holds")
    payload: Tick | Candle | OrderBookSnapshot | TradePrint = Field(description="The actual data")
    quality: DataQuality = Field(default=DataQuality.pristine)
    # Watermark = M2's processing_sequence (per instrument_id+broker, global) — distinct from source_sequence
    watermark: int = Field(description="M2 processing_sequence per instrument_id+broker — gap detection (NOT broker source_sequence)")
    # Three timestamps — WHY: measure latency p95 budgets
    exchange_timestamp: datetime | None = Field(default=None, description="Broker event time — UTC")
    received_at: datetime | None = Field(default=None, description="When M2.1 received it — UTC")
    # Lineage — v1.4: which reference snapshot was effective at ingestion (reproducibility)
    reference_snapshot_id: str | None = Field(default=None, description="M2b snapshot REF-YYYY-MM-DD-NNN effective at ingestion — audit lineage (v1.4)")
    # Source identity — distinct from watermark (M2b knows nothing about these)
    source_sequence: int | None = Field(default=None, description="Broker-provided sequence if any (e.g., 845921) — may be None for Deriv (v1.4)")
    source_event_id: str | None = Field(default=None, description="Broker-provided event id tick-id-123 — for dedup (v1.4)")
    # For lake partitioning & replay — provider-native passthrough (never overwritten)
    provider: str | None = Field(default=None, description="Native provider: deriv")
    provider_symbol: str | None = Field(default=None, description="Native symbol: frxEURUSD / R_75")
    provider_event_id: str | None = Field(default=None, description="Broker tick id for dedup (legacy alias of source_event_id)")
    schema_version: str = Field(default="canonical_quote_v1")

# Rebuild forward refs — WHY: inherits BaseContract with Provenance forward ref
MarketData.model_rebuild()
Candle.model_rebuild()
Tick.model_rebuild()
OrderBookSnapshot.model_rebuild()
