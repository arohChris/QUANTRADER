"""WHY: M3.1 Feature Engine (P16) — the conductor. One request in, one frozen
`AnalyticsResult` out: resolve timeframe (Calculation Timeframe Law) → capability
gate (M3.0) → profile params (bible §4) → Valkey cache (`feat:`, fail-open) →
Requirement Planner window (PIT-vs-Depth + Planner Dependency Laws) → provider port
(§6a.4) → wrong-series/completion guards → dependency-ordered pure computes → §8b
quality gates → stamped result.

M3.1 NEVER contains indicator math (D4: formulas live in `analytics/` files) and
NEVER imports lake/temporal internals — the injected `CandleProvider` port is the
only data seam; the future PIT service ships the real implementation behind it.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Protocol

import structlog
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
)

from modules.m3_analytics.engine.analytics_registry import AnalyticsRegistry
from modules.m3_analytics.engine.errors import FeatureEngineDataError
from modules.m3_analytics.engine.profiles import Profiles
from shared.contracts.analytics import AnalyticsResult
from shared.contracts.market_data import Candle, CapabilityStatus
from shared.timeframes import INTERVAL_MINUTES

logger = structlog.get_logger(__name__)

_CACHE_TTL_SECONDS = 60  # spec: hot-state only — never a historical source
_DURATION_UNITS = {"m": 1, "h": 60, "d": 1440}


def _duration_minutes(duration: str) -> int:
    """Historical Depth Law: '3d' → 4320 minutes (calendar duration → bars via ceil)."""
    match = re.fullmatch(r"(\d+)([mhd])", duration)
    if match is None or int(match.group(1)) == 0:
        raise ValueError(f"invalid history_duration {duration!r} — expected e.g. '3d'")
    return int(match.group(1)) * _DURATION_UNITS[match.group(2)]


def _require_known_timeframe(candidate: str) -> str:
    if candidate not in INTERVAL_MINUTES:
        raise ValueError(f"unknown timeframe {candidate!r}")
    return candidate


# Resolver output is DYNAMIC — validated here so None / "7m" / "1w" all reject as
# pydantic ValidationError (Calculation Timeframe Law: REJECT, never a hidden default).
_RESOLVED_TF = TypeAdapter(Annotated[str, AfterValidator(_require_known_timeframe)])


def _is_finite(value: Any) -> bool:
    try:
        return math.isfinite(value)
    except TypeError:
        return False


class TimeframeResolver(Protocol):
    """Option-A seam (human decision 2026-09-25): M4 builds Bot→Strategy→Analysis
    behind this; Day-1 ships none → omitted timeframe rejects (law holds literally)."""

    def resolve(self, request: FeatureRequest) -> str | None: ...


class CandleProvider(Protocol):
    """§6a.4 port verbatim (incl. `broker`) — returned candles are completed
    (`interval_end ≤ window_end`) and were available at `as_of`."""

    async def get_candles(
        self,
        instrument_id: str,
        timeframe: str,
        window_start: datetime,
        window_end: datetime,
        as_of: datetime,
        broker: str,
    ) -> list[Candle]: ...


class CachePort(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def setex(self, key: str, ttl: int, value: str) -> None: ...


class FeatureRequest(BaseModel):
    """In-module frozen request (NOT shared — first consumer is M3.1, §5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_id: str = Field(min_length=1, description="canonical id e.g. FX.EURUSD")
    broker: str = Field(min_length=1)
    timeframe: str | None = None  # None → resolver decides; no hidden default
    features: tuple[str, ...] = Field(min_length=1)
    as_of: datetime  # PIT boundary — when am I evaluating the market?
    asset_class: Literal["forex", "crypto", "commodity"]
    param_overrides: dict[str, dict[str, float]] | None = None

    @field_validator("timeframe")
    @classmethod
    def _timeframe_in_registry(cls, value: str | None) -> str | None:
        # Calendar buckets (1w/1mo/1y) are NOT in the 9-string registry → reject.
        if value is not None and value not in INTERVAL_MINUTES:
            raise ValueError(f"unknown timeframe {value!r}")
        return value

    @field_validator("as_of")
    @classmethod
    def _as_of_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("as_of must be tz-aware UTC (naive rejected)")
        return value


class FeatureEngine:
    """Stateless request→compute orchestrator (Day-1 honest: full compute per request)."""

    def __init__(
        self,
        *,
        provider: CandleProvider,
        capability_registry: Any,
        registry: AnalyticsRegistry | None = None,
        resolver: TimeframeResolver | None = None,
        cache: CachePort | None = None,
        profiles: Profiles,
    ) -> None:
        self._provider = provider
        self._capability_registry = capability_registry
        # registry=None → D4 discovery scan (empty package → empty, never crash)
        self._registry = registry if registry is not None else AnalyticsRegistry.from_package()
        self._resolver = resolver
        self._cache = cache
        self._profiles = profiles

    async def get_features(self, request: FeatureRequest) -> AnalyticsResult:
        resolved_tf = self._resolve_timeframe(request)

        # --- flow 2: capability gate (allow_proxy=True — strategy judges acceptability)
        warnings: list[str] = []
        survivors: list[str] = []
        proxy_names: set[str] = set()
        for name in request.features:
            entry = self._capability_registry.get(name, request.broker)
            if entry.status in (CapabilityStatus.FULLY_SUPPORTED, CapabilityStatus.DERIVED):
                survivors.append(name)
            elif entry.status is CapabilityStatus.PROXY_LIMITED:
                survivors.append(name)
                proxy_names.add(name)
                if entry.warning:
                    warnings.append(entry.warning)  # e.g. 'tick_count != volume'
            else:
                reason = entry.reason or entry.warning or "unsupported"
                warnings.append(f"{name} {entry.status.value} ({reason})")

        computable = [name for name in survivors if self._registry.has(name)]
        computable_set = set(computable)
        for name in survivors:
            if name not in computable_set:
                warnings.append(f"unknown_feature {name!r}")  # cap-ok but no D4 file yet

        # --- flow 3: nothing survived → UNAVAILABLE, never raise for capability misses
        if not computable:
            return self._unavailable(request, resolved_tf, warnings)

        # --- flow 4: profile params (precedence: overrides > profile > defaults)
        profile = self._profiles.get(request.asset_class)
        # --- flow 7 (order): dependency closure, deps compute first
        order = self._registry.execution_order(computable)
        params_by_name = {
            name: self._effective_params(name, request, profile) for name in order
        }
        key = self._cache_key(request, resolved_tf, computable, params_by_name)

        # --- flow 9 (read first): miss/hit decided before any fetch; fail-open
        if self._cache is not None:
            try:
                cached = await self._cache.get(key)
            except Exception as exc:  # noqa: BLE001 — fail-open: cache is never the truth
                logger.warning("feature_cache_read_failed", error=str(exc))
                cached = None
            if cached is not None:
                return AnalyticsResult.model_validate_json(cached)

        # --- flow 5: Requirement Planner — union over closure, callers never dates
        tf_minutes = INTERVAL_MINUTES[resolved_tf]
        needed_bars = 0
        for name in order:
            req = self._registry.get(name).REQUIREMENTS
            bars = req.minimum_history + req.warmup
            if req.history_duration:
                bars = max(bars, -(-_duration_minutes(req.history_duration) // tf_minutes))
            needed_bars = max(needed_bars, bars)
        window_end = request.as_of
        window_start = window_end - timedelta(minutes=needed_bars * tf_minutes)

        # --- flow 6: provider fetch (§6a.4) — typed error, never a silent empty
        try:
            candles = await self._provider.get_candles(
                request.instrument_id,
                resolved_tf,
                window_start,
                window_end,
                request.as_of,
                request.broker,
            )
        except Exception as exc:
            raise FeatureEngineDataError(
                f"provider.get_candles failed for {request.instrument_id} @ {resolved_tf}: {exc}"
            ) from exc

        for candle in candles:
            if candle.interval != resolved_tf:
                raise FeatureEngineDataError(
                    f"wrong-series guard: candle interval {candle.interval!r} "
                    f"!= resolved {resolved_tf!r}"
                )
            if candle.interval_end > window_end:
                raise FeatureEngineDataError(
                    f"completion guard: candle interval_end {candle.interval_end} "
                    f"> window_end {window_end} (forming-candle leak)"
                )

        # --- flow 7-8: dependency-ordered computes behind §8b quality gates
        features: dict[str, float] = {}
        for name in order:
            module = self._registry.get(name)
            requirements = module.REQUIREMENTS
            count = len(candles)
            if count < requirements.minimum_history:
                if name in computable_set:
                    warnings.append(f"INSUFFICIENT:{name}")  # never a silent partial
                continue
            if name in computable_set and count < (
                requirements.minimum_history + requirements.warmup
            ):
                warnings.append(f"WARMUP_INCOMPLETE:{name}")
            value = module.compute(candles, params_by_name[name])
            if name not in computable_set:
                continue  # dependency: computed for ordering/plan, value never returned
            if not _is_finite(value):
                warnings.append(f"invalid:{name}")  # JSON cannot carry NaN — never cache it
                continue
            features[name] = value

        regime = None
        for name in computable:
            module = self._registry.get(name)
            if module.REQUIREMENTS.provides_regime and name in features:
                regime = module.REGIME_LABEL(features[name])
                break

        result = AnalyticsResult(
            symbol=request.instrument_id,  # value pin: symbol = canonical instrument_id
            broker=request.broker,
            timeframe=resolved_tf,
            features=features,
            regime=regime,
            capability_status=(
                CapabilityStatus.PROXY_LIMITED
                if proxy_names & computable_set
                else CapabilityStatus.FULLY_SUPPORTED
            ),
            capability_warning="; ".join(warnings) or None,
            source_watermark=(
                int(max(c.interval_start for c in candles).timestamp() * 1000)
                if candles
                else None
            ),
        )

        if self._cache is not None:
            try:
                await self._cache.setex(
                    key, _CACHE_TTL_SECONDS, json.dumps(result.model_dump(mode="json"))
                )
            except Exception as exc:  # noqa: BLE001 — best-effort write, never fails the request
                logger.warning("feature_cache_write_failed", error=str(exc))
        return result

    def _resolve_timeframe(self, request: FeatureRequest) -> str:
        if request.timeframe is not None:
            return request.timeframe  # already registry-validated at the boundary
        candidate = self._resolver.resolve(request) if self._resolver is not None else None
        # Day-1 (no resolver) / unresolvable / unknown → ValidationError, no default
        return _RESOLVED_TF.validate_python(candidate)

    def _effective_params(
        self,
        name: str,
        request: FeatureRequest,
        profile: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        merged = dict(self._registry.get(name).REQUIREMENTS.defaults)
        merged.update(profile.get(name, {}))
        if request.param_overrides:
            merged.update(request.param_overrides.get(name, {}))
        return merged

    def _cache_key(
        self,
        request: FeatureRequest,
        resolved_tf: str,
        computable: list[str],
        params_by_name: dict[str, dict[str, float]],
    ) -> str:
        features_hash = hashlib.sha256(
            ",".join(sorted(computable)).encode()
        ).hexdigest()[:16]
        params_hash = hashlib.sha256(
            json.dumps(params_by_name, sort_keys=True).encode()
        ).hexdigest()[:16]
        # No user_id anywhere (global-candle tenant law §4.3); as_of pins the PIT window.
        return (
            f"feat:{request.broker}:{request.instrument_id}:{resolved_tf}:"
            f"{features_hash}:{params_hash}:{request.as_of.isoformat()}"
        )

    @staticmethod
    def _unavailable(
        request: FeatureRequest, resolved_tf: str, warnings: list[str]
    ) -> AnalyticsResult:
        return AnalyticsResult(
            symbol=request.instrument_id,
            broker=request.broker,
            timeframe=resolved_tf,
            features={},
            regime=None,
            capability_status=CapabilityStatus.UNAVAILABLE,
            capability_warning="; ".join(warnings) or None,
            source_watermark=None,
        )


class StaticCandleProvider:
    """Day-1 fixture/verify provider over an in-memory series (§6a.4 shape).

    Filters to the requested window here — the real PIT provider does the same in
    its WHERE clause (availability_time ≤ as_of, interval_end ≤ window_end).
    """

    def __init__(self, candles: Sequence[Candle]) -> None:
        self._candles = list(candles)

    async def get_candles(
        self,
        instrument_id: str,
        timeframe: str,
        window_start: datetime,
        window_end: datetime,
        as_of: datetime,
        broker: str,
    ) -> list[Candle]:
        del instrument_id, as_of, broker  # static single-series fixture — no PIT history
        return [
            candle
            for candle in self._candles
            if candle.interval == timeframe
            and window_start <= candle.interval_start
            and candle.interval_end <= window_end
        ]
