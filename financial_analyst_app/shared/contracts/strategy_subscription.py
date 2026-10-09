"""
M1.5 - StrategySubscription - per (user, broker, strategy) opt-out + trade scope

WHY: admin approval is GLOBAL (strategy_admin) - this row carries only the USER's
side: their per-strategy off switch (user_disabled), the assets selected for the
trade (instruments), and params passthrough. Empty instruments = no asset selected
= trade NOTHING (fail-closed, human 2026-09-30). Adding an instrument = adding a
trade; it must pass the M5 risk threshold before it runs (enforced at wiring, not here).
"""
from __future__ import annotations

from typing import Any

from pydantic import Field

from .base import AwareUtcDatetime, BaseContract


class StrategySubscription(BaseContract):
    user_id: str
    broker_id: str  # same scope M5 risk uses for sizing
    strategy_id: str  # registered name; registry-checked on write (injected set)
    user_disabled: bool = False  # user turned the strategy off; default = on
    params: dict[str, Any] = Field(default_factory=dict)  # JSON object, passed as-is
    instruments: tuple[str, ...] = Field(default_factory=tuple)  # () = trade nothing
    updated_at: AwareUtcDatetime  # UTC, system-managed


# Consumers: M4 (runner evaluation), M5 (user+broker scope), M10.2 (API)
StrategySubscription.model_rebuild()
