"""WHY: M3.1 Step 7 — real-data + live-infra verification of the Feature Engine
(spec docs/specs/m3.1_feature_engine.spec.md §Verify). NOT a unit test: builds real
1m candles from data/sample_ticks_realistic.jsonl through the actual M2.4 pipeline,
then drives the engine with fixture analytics (D4-shaped) against the REAL
config/capabilities.yaml, REAL forex profile params, and a LIVE Valkey (:6379).

Proves for the human: capability interlock (deriv drop), profile precedence
(defaults 10 → profile 14 → override 99), dep-chain planner window, resolver seam,
wrong-series guard, §8b INSUFFICIENT/WARMUP, dead-cache fail-open, and the P16 gate
measured cache hit <1ms. Pure-orchestration fixture values (42.0 / period echoes) —
indicator math belongs to M3.2, out of scope here.

Usage: python scripts/verify_m3.1_feature_engine.py
       [--input data/sample_ticks_realistic.jsonl] [--valkey redis://127.0.0.1:6379/0]
Exit: 0 = all checks OK (incl. P16 hit<1ms) · 1 = any check failed.
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import ValidationError

from modules.m2_market_data.candles import CandleBuilder
from modules.m2_market_data.ingest.ingest import TickIngestor
from modules.m2_market_data.normalize import Normalizer
from modules.m2_market_data.validate import validate
from modules.m2_market_data.watermark import WatermarkRegistry
from modules.m3_analytics.capability.capability_registry import (
    CapabilityRegistry,
)
from modules.m3_analytics.engine.analytics_registry import (
    AnalyticsRegistry,
    DataRequirement,
)
from modules.m3_analytics.engine.errors import FeatureEngineDataError
from modules.m3_analytics.engine.feature_engine import (
    FeatureEngine,
    FeatureRequest,
    StaticCandleProvider,
)
from modules.m3_analytics.engine.profiles import Profiles
from modules.reference import reference
from shared.contracts.internal_tick import InternalTick
from shared.contracts.market_data import (
    Candle,
    CapabilityStatus,
    MarketData,
)
from shared.core.clock import SimulatedClock

_CAP_YAML = ROOT / "config" / "capabilities.yaml"
_ASSETS_DIR = ROOT / "config" / "assets"
_DEFAULT_CLOCK = "2026-03-22T19:47:00+00:00"

_failures: list[str] = []


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(label)
    return ok


# --- fixture analytics (D4 shape: NAME / REQUIREMENTS / compute(candles, params)) ---


def _analytic(
    name: str,
    *,
    min_history: int = 1,
    warmup: int = 0,
    defaults: dict | None = None,
    deps: tuple[str, ...] = (),
    value: float = 42.0,
):
    requirements = DataRequirement(
        minimum_history=min_history,
        warmup=warmup,
        dependencies=deps,
        defaults=dict(defaults or {}),
    )
    return SimpleNamespace(
        NAME=name,
        REQUIREMENTS=requirements,
        compute=lambda candles, params: (
            float(params["period"]) if "period" in params else value
        ),
    )


def _registry(*analytics) -> AnalyticsRegistry:
    registry = AnalyticsRegistry()
    for analytic in analytics:
        registry.register(analytic)
    return registry


class _RecordingProvider:
    """Delegates to the real StaticCandleProvider but records §6a.4 call windows."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls: list[tuple] = []

    async def get_candles(self, *args, **kwargs) -> list[Candle]:
        self.calls.append(args)
        return await self._inner.get_candles(*args, **kwargs)


class _PassthroughProvider:
    """VIOLATES the port on purpose (ignores timeframe) — wrong-series guard must catch."""

    def __init__(self, candles: list[Candle]) -> None:
        self._candles = list(candles)

    async def get_candles(self, *args, **kwargs) -> list[Candle]:
        return list(self._candles)


class _Resolver:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def resolve(self, request: FeatureRequest) -> str | None:
        return self.value


class _LiveCache:
    """CachePort over a real redis-py client + key/TTL capture for assertions."""

    def __init__(self, url: str) -> None:
        from redis import asyncio as redis  # lazy — same pattern as M2.9 HotStore

        self._client = redis.from_url(url, decode_responses=True)
        self.get_keys: list[str] = []
        self.set_ttls: list[int] = []

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception:  # noqa: BLE001 — probe only: report down as a failed check
            return False

    async def get(self, key: str) -> str | None:
        self.get_keys.append(key)
        return await self._client.get(key)  # type: ignore[return-value] — decode_responses=True

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.set_ttls.append(ttl)
        await self._client.setex(key, ttl, value)

    async def forget(self, key: str) -> None:
        await self._client.delete(key)

    async def close(self) -> None:
        await self._client.aclose()


class _ReferenceFacade:
    def current_snapshot_id(self) -> str | None:
        return reference.current_snapshot_id()

    def to_canonical(self, native: str, broker: str) -> str:
        return reference.to_canonical(native, broker)

    def exists(self, instrument_id: str, broker: str) -> bool:
        return reference.exists(instrument_id, broker)

    def get_instrument(self, instrument_id: str) -> dict:
        return reference.get_instrument(instrument_id)


def _build_candles(input_path: Path) -> tuple[list[Candle], int, int]:
    """Real ticks → M2.1→M2.2→M2.3→M2.4 (same pipeline as verify_m2.4_candles.py)."""
    clock = SimulatedClock(datetime.fromisoformat(_DEFAULT_CLOCK))
    ingestor = TickIngestor(provider="deriv", clock=clock)
    normalizer = Normalizer(reference=_ReferenceFacade(), watermark_registry=WatermarkRegistry())

    accepted = dropped = 0
    candles: list[Candle] = []
    builders: dict[str, CandleBuilder] = {}
    for line in input_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        tick = ingestor.ingest(line)
        if not isinstance(tick, InternalTick):
            continue
        market = normalizer.normalize(tick)
        if not isinstance(market, MarketData):
            continue
        checked = validate(market)
        if not isinstance(checked, MarketData):
            dropped += 1
            continue
        accepted += 1
        key = f"{checked.instrument_id}|{checked.broker}"
        if key not in builders:
            builders[key] = CandleBuilder(
                instrument_id=checked.instrument_id or "unknown",
                broker=checked.broker,
                interval="1m",
            )
        closed = builders[key].on_tick(checked)
        # Candle has no instrument_id — filter by builder key (same as verify_m2.4)
        if isinstance(closed, Candle) and key.startswith("FX.EURUSD|"):
            candles.append(closed)
    for key, builder in builders.items():
        flushed = builder.close()
        if isinstance(flushed, Candle) and key.startswith("FX.EURUSD|"):
            candles.append(flushed)
    candles.sort(key=lambda c: c.interval_start)
    return candles, accepted, dropped


def _request(
    as_of: datetime,
    features: tuple[str, ...],
    *,
    timeframe: str | None = "1m",
    overrides: dict | None = None,
) -> FeatureRequest:
    return FeatureRequest(
        instrument_id="FX.EURUSD",
        broker="deriv",
        timeframe=timeframe,
        features=features,
        as_of=as_of,
        asset_class="forex",
        param_overrides=overrides,
    )


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="Verify M3.1 Feature Engine (Step 7)")
    parser.add_argument("--input", default="data/sample_ticks_realistic.jsonl")
    parser.add_argument("--valkey", default="redis://127.0.0.1:6379/0")
    parser.add_argument("--dead", default="redis://127.0.0.1:1/0")
    args = parser.parse_args()

    # --- 1. real candles -------------------------------------------------------
    candles, accepted, dropped = _build_candles(Path(args.input))
    print(f"input={args.input} validate_accepts={accepted} validate_drops={dropped}")
    for c in candles:
        print(
            f"  candle {c.interval} {c.interval_start.isoformat()}→"
            f"{c.interval_end.isoformat()} O={c.open} C={c.close} n={c.tick_count}"
        )
    if not _check("real 1m candles built from sample ticks (M2.4)", len(candles) >= 1,
                  f"count={len(candles)}"):
        print("\nRESULT: FAILED")
        return 1
    as_of = max(c.interval_end for c in candles)
    print(f"as_of (= max interval_end, completion-safe) = {as_of.isoformat()}")

    cap_registry = CapabilityRegistry.from_yaml(_CAP_YAML)
    profiles = Profiles.from_dir(_ASSETS_DIR)

    def engine(registry, *, provider, cache=None, resolver=None) -> FeatureEngine:
        return FeatureEngine(
            provider=provider,
            capability_registry=cap_registry,
            registry=registry,
            resolver=resolver,
            cache=cache,
            profiles=profiles,
        )

    # --- 2. capability interlock (M3.0 P15) -----------------------------------
    reg_full = _registry(
        _analytic("rsi_14", min_history=1, defaults={"period": 10}),
        _analytic("tick_rate", min_history=1),
        _analytic("ema_20", min_history=1, warmup=3, deps=("rsi_14",)),
    )
    res = await engine(
        reg_full,
        provider=StaticCandleProvider(candles),
    ).get_features(_request(as_of, ("rsi_14", "order_book_imbalance")))
    print(f"\ninterlock: features={res.features} status={res.capability_status.value} "
          f"warn={res.capability_warning!r}")
    _check(
        "capability interlock: order_book_imbalance@deriv dropped, rsi_14 kept",
        "rsi_14" in res.features
        and "order_book_imbalance" not in res.features
        and "order_book_imbalance UNAVAILABLE" in (res.capability_warning or "")
        and "Deriv has_depth:false" in (res.capability_warning or "")
        and res.capability_status is CapabilityStatus.FULLY_SUPPORTED,
    )

    # --- 3. proxy row ----------------------------------------------------------
    res = await engine(
        reg_full, provider=StaticCandleProvider(candles)
    ).get_features(_request(as_of, ("tick_rate",)))
    print(f"proxy: features={res.features} status={res.capability_status.value} "
          f"warn={res.capability_warning!r}")
    _check(
        "proxy: tick_rate included with PROXY_LIMITED + 'tick_count != volume'",
        res.capability_status is CapabilityStatus.PROXY_LIMITED
        and "tick_count \u2260 volume" in (res.capability_warning or ""),
    )

    # --- 4. profile precedence from REAL forex.yaml ---------------------------
    res = await engine(
        reg_full, provider=StaticCandleProvider(candles)
    ).get_features(_request(as_of, ("rsi_14",)))
    _check(
        "profile precedence: forex.yaml rsi_14 period 14.0 beats default 10",
        res.features.get("rsi_14") == 14.0,
        f"features={res.features}",
    )
    res = await engine(
        reg_full, provider=StaticCandleProvider(candles)
    ).get_features(_request(as_of, ("rsi_14",), overrides={"rsi_14": {"period": 99}}))
    _check(
        "override beats profile: param_overrides period 99",
        res.features.get("rsi_14") == 99.0,
        f"features={res.features}",
    )

    # --- 5. dep-chain planner (union window) ----------------------------------
    recorder = _RecordingProvider(StaticCandleProvider(candles))
    res = await engine(reg_full, provider=recorder).get_features(
        _request(as_of, ("ema_20",))
    )
    span_min = (recorder.calls[0][3] - recorder.calls[0][2]).total_seconds() / 60
    print(f"planner: window span={span_min:.0f}m features={res.features} "
          f"warn={res.capability_warning!r}")
    _check(
        "dep chain: window widens to union (ema warmup 3 + dep rsi → ≥4 bars), "
        "dep value not returned",
        span_min >= 4 and set(res.features) == {"ema_20"} and "WARMUP_INCOMPLETE:ema_20"
        in (res.capability_warning or ""),
    )

    # --- 6. resolver seam ------------------------------------------------------
    res = await engine(
        reg_full,
        provider=StaticCandleProvider(candles),
        resolver=_Resolver("1m"),
    ).get_features(_request(as_of, ("rsi_14",), timeframe=None))
    _check(
        "resolver supplies '1m' → used unchanged (stamped on result)",
        res.timeframe == "1m",
        f"timeframe={res.timeframe}",
    )
    for resolver, label in ((None, "no resolver"), (_Resolver(None), "resolver→None")):
        try:
            await engine(
                reg_full,
                provider=StaticCandleProvider(candles),
                resolver=resolver,
            ).get_features(_request(as_of, ("rsi_14",), timeframe=None))
            _check(f"omitted timeframe ({label}) → ValidationError", False)
        except ValidationError:
            _check(f"omitted timeframe ({label}) → ValidationError", True)
    try:
        await engine(
            reg_full,
            provider=StaticCandleProvider(candles),
            resolver=_Resolver("7m"),
        ).get_features(_request(as_of, ("rsi_14",), timeframe=None))
        _check("resolver unknown '7m' → ValidationError", False)
    except ValidationError:
        _check("resolver unknown '7m' → ValidationError", True)

    # --- 7. wrong-series guard -------------------------------------------------
    try:
        await engine(
            _registry(_analytic("rsi_14", min_history=1)),
            provider=_PassthroughProvider(candles),
        ).get_features(_request(as_of, ("rsi_14",), timeframe="5m"))
        _check("wrong-series guard: 1m candles on 5m request → FeatureEngineDataError",
               False)
    except FeatureEngineDataError as exc:
        _check("wrong-series guard: 1m candles on 5m request → FeatureEngineDataError",
               True, str(exc)[:80])

    # --- 8. §8b quality gates on thin real data -------------------------------
    res = await engine(
        _registry(_analytic("rsi_14", min_history=50)),
        provider=StaticCandleProvider(candles),
    ).get_features(_request(as_of, ("rsi_14",)))
    _check(
        "INSUFFICIENT:rsi_14 on thin data (need 50 bars) → omitted, no compute",
        res.features == {} and "INSUFFICIENT:rsi_14" in (res.capability_warning or ""),
        f"warn={res.capability_warning!r}",
    )
    res = await engine(
        _registry(_analytic("rsi_14", min_history=1, warmup=10)),
        provider=StaticCandleProvider(candles),
    ).get_features(_request(as_of, ("rsi_14",)))
    _check(
        "WARMUP_INCOMPLETE:rsi_14 (need 11, have thin window) → computed + flagged",
        "rsi_14" in res.features
        and "WARMUP_INCOMPLETE:rsi_14" in (res.capability_warning or ""),
        f"features={res.features} warn={res.capability_warning!r}",
    )

    # --- 9. dead cache → fail-open (recompute both times) ---------------------
    dead_recorder = _RecordingProvider(StaticCandleProvider(candles))
    dead_engine = engine(
        reg_full,
        provider=dead_recorder,
        cache=_LiveCache(args.dead),
    )
    req = _request(as_of, ("rsi_14",))
    res1 = await dead_engine.get_features(req)
    res2 = await dead_engine.get_features(req)
    _check(
        "dead cache fail-open: both requests succeed via recompute",
        res1.features == res2.features == {"rsi_14": 14.0} and len(dead_recorder.calls) == 2,
        f"provider_calls={len(dead_recorder.calls)}",
    )

    # --- 10. LIVE Valkey: miss/hit timings — P16 gate <1ms --------------------
    # Benchmark law (AGENTS §4.1: prod-like, not first-call): warm-up the connection
    # pool + pydantic validators + structlog ON A DIFFERENT KEY, then measure
    # steady-state hits. Gate = median (P95 budgets are percentile-based); every
    # sample printed so the human sees the full distribution — loopback RTT to the
    # Docker-published Valkey dominates and is machine-load sensitive.
    print(f"\nlive Valkey: {args.valkey}")
    live = _LiveCache(args.valkey)
    if not _check("live Valkey ping", await live.ping(), args.valkey):
        await live.close()
        print("\nRESULT: FAILED")
        return 1
    try:
        live_recorder = _RecordingProvider(StaticCandleProvider(candles))
        live_engine = engine(reg_full, provider=live_recorder, cache=live)
        req = _request(as_of, ("rsi_14",))
        warm_req = _request(as_of, ("ema_20",))  # different key — must not pollute miss

        await live_engine.get_features(warm_req)  # warm-up: pool + validators (not timed)
        warm_keys = set(live.get_keys)  # measured key must be a different one

        t0 = time.perf_counter_ns()
        res_miss = await live_engine.get_features(req)
        miss_ms = (time.perf_counter_ns() - t0) / 1e6
        # warm-up (1) + measured miss (1) may fetch; the 5 hits below must fetch nothing
        calls_after_miss = len(live_recorder.calls)

        hit_samples: list[float] = []
        res_hit = res_miss
        for _ in range(5):
            t0 = time.perf_counter_ns()
            res_hit = await live_engine.get_features(req)
            hit_samples.append((time.perf_counter_ns() - t0) / 1e6)
        hit_ms = statistics.median(hit_samples)

        key = next((k for k in live.get_keys if k not in warm_keys), "")
        print(f"miss={miss_ms:.3f}ms hits={[f'{h:.3f}' for h in hit_samples]} "
              f"median={hit_ms:.3f}ms provider_calls={len(live_recorder.calls)} "
              f"ttl={live.set_ttls[:1]}")
        print(f"key={key}")
        _check(
            "cache miss→hit: 5 hits served from Valkey (0 extra fetches, equal result)",
            res_hit == res_miss and len(live_recorder.calls) == calls_after_miss,
            f"fetches_during_hits={len(live_recorder.calls) - calls_after_miss}",
        )
        _check(
            "cache key: feat: namespace, no user_id, TTL 60",
            key.startswith("feat:") and "user" not in key.lower()
            and live.set_ttls[:1] == [60],
        )
        _check(
            "P16 gate: steady-state cache hit median <1ms",
            hit_ms < 1.0,
            f"median={hit_ms:.3f}ms min={min(hit_samples):.3f} max={max(hit_samples):.3f}",
        )
        for written in set(live.get_keys):
            await live.forget(written)  # cleanup — rerun must be a miss, not a stale hit
    finally:
        await live.close()

    if _failures:
        for failure in _failures:
            print(f"[FAIL] {failure}")
        print("\nRESULT: FAILED")
        return 1
    print("\nRESULT: ALL OK (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
