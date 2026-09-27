"""
M4.1 — base_strategy: frozen StrategyEntry + documented BaseStrategy shape.
WHY: the registry stores ONE immutable entry per discovered strategy so every
  gate (lifecycle/capability/param/shadow) reads identical truth; the ABC only
  documents a generate() shape for future stateful strategies (m4.2 files are
  pure functions today — discrepancy flagged in BUILD_STATUS, not hidden).
"""
from __future__ import annotations

import types
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from shared.contracts.analytics import AnalyticsResult
from shared.contracts.market_data import MarketData
from shared.contracts.signal import Signal


class BaseStrategy(ABC):
    """Documented shape only — discovery/runner consume function modules (D4)."""

    name: str
    version: str

    @abstractmethod
    def generate(self, analytics: AnalyticsResult, market: MarketData,
                 now: datetime) -> Signal | None:
        """Pure sync calc: one finalized bar in, one Signal (or None) out."""


@dataclass(frozen=True)
class StrategyEntry:
    """Discovered truth for one strategy module (m4.2 contract, FROZEN here).
    WHY no strategy_id/version fields: those are yaml-owned by
      ParameterRegistry (contract-note 1, declared for A55) — code owns math,
      config owns identity."""

    name: str
    module: types.ModuleType
    slots: dict[str, tuple[str, dict[str, float]]]
    defaults: dict[str, float]
    evaluate: Callable[[dict[str, float], dict[str, float]], float]
