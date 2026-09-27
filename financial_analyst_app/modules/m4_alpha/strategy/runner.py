"""
M4.1 — runner: the ONLY Signal constructor (gates -> I/O -> contract -> Signal).
WHY this exact order (all tested): validate + lifecycle + capability fire BEFORE
  any feature I/O so a blocked strategy never costs an engine call; evaluate
  output is contract-checked (never clamped); shadow applied last, fail-closed.
Pure calc helpers stay sync; the single await is the FeatureEngine call (4.2).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Literal, Protocol
from uuid import uuid4

from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.feature_engine import FeatureRequest
from shared.contracts.analytics import AnalyticsResult
from shared.contracts.base import Provenance
from shared.contracts.market_data import MarketData
from shared.contracts.signal import Signal

from .lifecycle import Lifecycle
from .parameter_registry import ParameterRegistry
from .shadow_mode import apply_shadow
from .strategy_registry import StrategyContractError, StrategyRegistry

logger = logging.getLogger(__name__)

_ASSET_CLASSES = frozenset({"forex", "crypto", "commodity"})
_ENVS = frozenset({"prod", "paper"})  # live goes through M5/M6, never here
_WINDOW_MIN, _WINDOW_MAX, _WINDOW_DEFAULT = 1, 3600, 60

Slots = dict[str, tuple[str, dict[str, float]]]


class FeatureSource(Protocol):
    """Structural engine port — real FeatureEngine and test fakes both fit."""

    async def get_features(self, request: FeatureRequest) -> AnalyticsResult: ...


class StrategyRunner:
    def __init__(self, *, registry: StrategyRegistry, params: ParameterRegistry,
                 lifecycle: Lifecycle, capability: CapabilityRegistry,
                 engine: FeatureSource, allowlist: frozenset[str] = frozenset()) -> None:
        self.registry = registry
        self.params = params
        self.lifecycle = lifecycle
        self.capability = capability
        self.engine = engine
        self.allowlist = frozenset(allowlist)

    async def run(self, name: str, *, market: MarketData, instrument_id: str,
                  broker: str, timeframe: str,
                  asset_class: Literal["forex", "crypto", "commodity"],
                  as_of: datetime, now: datetime, env: str,
                  user_id: str | None = None,
                  strategy_params: dict[str, float] | None = None) -> Signal | None:
        entry = self.registry.get(name)  # typed UnknownStrategyError
        _validate(instrument_id, broker, timeframe, asset_class, as_of, now, env)
        if not self.lifecycle.is_enabled(name, user_id=user_id):
            logger.info("lifecycle blocked strategy=%s user_id=%s", name, user_id)
            return None
        eligible, reason = self.capability.is_strategy_eligible(name, broker)
        if not eligible:  # capability BEFORE I/O (tested: engine.requests == [])
            logger.info("capability blocked strategy=%s broker=%s reason=%s",
                        name, broker, reason)
            return None

        merged = self.params.effective(name, defaults=entry.defaults,
                                       request=strategy_params)
        window = merged.get("valid_until_seconds", _WINDOW_DEFAULT)
        if (isinstance(window, bool) or not isinstance(window, (int, float))
                or not _WINDOW_MIN <= window <= _WINDOW_MAX):
            raise ValueError(
                f"valid_until_seconds must be {_WINDOW_MIN}..{_WINDOW_MAX}, got {window!r}")

        features, overrides = _slot_union(entry.slots)
        request = FeatureRequest(
            instrument_id=instrument_id, broker=broker, timeframe=timeframe,
            features=tuple(features), as_of=as_of, asset_class=asset_class,
            param_overrides=overrides)
        result = await self.engine.get_features(request)  # the only await (I/O)
        value = entry.evaluate(_slot_map(entry.slots, result), merged)
        if isinstance(value, bool) or not isinstance(value, float) or not math.isfinite(value):
            raise StrategyContractError(
                f"{name}.evaluate must return a finite float, got {value!r}")
        if abs(value) > 1.0:  # never clamp — a ramp bug must fail loud (B1)
            raise StrategyContractError(
                f"{name}.evaluate returned {value} outside [-1, 1]")
        if value == 0.0:  # no conviction -> no Signal (E11)
            return None

        sid = self.params.strategy_id(name)
        ver = self.params.version(name)
        signal = Signal(
            signal_id=uuid4(),
            strategy_id=sid,
            version=ver,
            symbol=instrument_id,  # LAW (A64/A65): trading identity = canonical
            # instrument_id (FX.EURUSD / CRYPTO.BTCUSD), never the display symbol
            # — M4.3 reference lookup rejects display names (spec Discrepancy 1)
            broker=broker,
            direction="long" if value > 0 else "short",
            strength=abs(value),  # sign = direction, magnitude = conviction
            timeframe=timeframe,
            valid_until=now + timedelta(seconds=window),  # alpha decays
            analytics_refs=[result.correlation_id],
            user_id=user_id,
            provenance=Provenance(
                producer=f"m4.alpha/{sid}@{ver}",  # M7 audit replay
                analytics_ids=[result.correlation_id],
                market_watermark=market.watermark,
                signal_strength=value),
        )
        logger.debug("signal strategy=%s direction=%s strength=%s",
                     signal.strategy_id, signal.direction, signal.strength)
        return apply_shadow(signal, env=env, allowlist=self.allowlist)


def _validate(instrument_id: str, broker: str, timeframe: str, asset_class: str,
              as_of: datetime, now: datetime, env: str) -> None:
    """Every input gate BEFORE any I/O (tested via engine.requests == [])."""
    for field, value in (("instrument_id", instrument_id), ("broker", broker),
                         ("timeframe", timeframe)):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} must be a non-empty str, got {value!r}")
    if asset_class not in _ASSET_CLASSES:
        raise ValueError(
            f"asset_class must be one of {sorted(_ASSET_CLASSES)}, got {asset_class!r}")
    if env not in _ENVS:
        raise ValueError(f"env must be one of {sorted(_ENVS)}, got {env!r}")
    _require_utc(as_of, "as_of")
    _require_utc(now, "now")


def _require_utc(value: datetime, field: str) -> None:
    """Naive or non-UTC timestamps rejected loud — DST bugs live here (3.8)."""
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() != timedelta(0)):
        raise ValueError(f"{field} must be tz-aware UTC, got {value!r}")


def _slot_union(slots: Slots) -> tuple[list[str], dict[str, dict[str, float]]]:
    """Distinct analytics in slot order; only non-empty overrides included."""
    seen: list[str] = []
    overrides: dict[str, dict[str, float]] = {}
    for analytic, extra in slots.values():
        if analytic not in seen:
            seen.append(analytic)
        if extra:
            overrides[analytic] = dict(extra)
    return seen, overrides


def _slot_map(slots: Slots, result: AnalyticsResult) -> dict[str, float]:
    """slot <- analytics value; missing/None/non-finite = loud ValueError (B3)."""
    raw = result.features
    if not isinstance(raw, dict):
        raise ValueError(  # noqa: TRY004 — spec/test-pinned ValueError (B3)
            "AnalyticsResult.features missing (None) — cannot evaluate")
    values: dict[str, float] = {}
    for slot, (analytic, _extra) in slots.items():
        if analytic not in raw:
            raise ValueError(f"missing analytic {analytic!r} for slot {slot!r}")
        value = raw[analytic]
        if (value is None or isinstance(value, bool)
                or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError(f"slot {slot!r} analytic {analytic!r} not finite: {value!r}")
        values[slot] = float(value)  # ints tolerated at the boundary only
    return values
