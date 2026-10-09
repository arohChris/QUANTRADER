"""
M1.5 - TradingPreferences - per (user, broker) strategy briefing

WHY: the strategy must know WHAT to analyze (timeframe), WHEN it starts running
(activation_at), and whether the user clicked auto-trade - one row per tenant scope
so M5 risk fetches the SAME (user, broker) key for balance/leverage when sizing
(m1.5 spec; storage law AGENTS.md 4.3).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import field_validator

from .base import AwareUtcDatetime, BaseContract

# WHY a Literal, not a runtime set: pydantic rejects unknown keys at the write
# boundary with a typed ValidationError; tests/test_m1.5_trading_prefs_spec.py
# parity-locks this tuple against shared/timeframes.py INTERVAL_MINUTES (drift = red).
TimeframeKey = Literal["1m", "5m", "15m", "30m", "40m", "1h", "2h", "4h", "24h"]


class TradingPreferences(BaseContract):
    user_id: str
    broker_id: str  # M5 risk fetch scope: balance/leverage for this broker
    timeframe: TimeframeKey = "1m"  # 9-key registry; calendar buckets rejected
    activation_at: AwareUtcDatetime  # when the strategy starts running for this user
    auto_trade: bool = False  # user master switch - nothing runs until clicked
    updated_at: AwareUtcDatetime  # UTC, system-managed (auto-updated)

    @field_validator("activation_at")
    @classmethod
    def _activation_sane_bound(cls, value: datetime) -> datetime:
        # WHY: sane hard bound at the write boundary (AGENTS.md 3.13) - refuse absurd
        # history instead of letting it become a schedule anchor.
        if value.year < 2000:
            raise ValueError(f"activation_at before 2000 rejected: {value.isoformat()}")
        return value


# Consumers: M4 (runner briefing + schedule), M5 (scope key for risk data fetch),
#            M10.2 (API), M10 (frontend via API only)
TradingPreferences.model_rebuild()
