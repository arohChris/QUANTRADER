"""WHY: M3.2 Step 7 — real-data verification of the first three D4 compute files
(spec docs/specs/m3.2_analytics.spec.md §Verify). NOT unit tests: proves D4
discovery finds the P17 batch, the golden values hold outside pytest, TWO
independent RSI implementations agree on a deterministic walk seeded from a REAL
sample close, the engine honestly refuses thin real data (INSUFFICIENT), and an
end-to-end run over realistic bars prints all three features + the proxy warning.

Usage: python scripts/verify_m3.2_analytics.py [--input data/sample_ticks_realistic.jsonl]
Exit: 0 = all checks OK · 1 = any check failed. Latency line is ADVISORY only
(§4.1 analytics <20ms enforcement is the engine's latency header — a later slice).
"""
from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.m2_market_data.candles import CandleBuilder
from modules.m2_market_data.ingest.ingest import TickIngestor
from modules.m2_market_data.normalize import Normalizer
from modules.m2_market_data.validate import validate
from modules.m2_market_data.watermark import WatermarkRegistry
from modules.m3_analytics.analytics import ema_20, rsi_14, tick_rate
from modules.m3_analytics.capability.capability_registry import (
    CapabilityRegistry,
)
from modules.m3_analytics.engine.analytics_registry import AnalyticsRegistry
from modules.m3_analytics.engine.feature_engine import (
    FeatureEngine,
    FeatureRequest,
    StaticCandleProvider,
)
from modules.m3_analytics.engine.profiles import Profiles
from modules.reference import reference
from shared.contracts.internal_tick import InternalTick
from shared.contracts.market_data import Candle, CapabilityStatus, MarketData
from shared.core.clock import SimulatedClock

_CAP_YAML = ROOT / "config" / "capabilities.yaml"
_ASSETS_DIR = ROOT / "config" / "assets"
_DEFAULT_CLOCK = "2026-03-22T19:47:00+00:00"
_WALK_SEED = 20260925
_REAL_SAMPLE_CLOSE = 1.08548  # from data/sample_ticks_realistic.jsonl (M2.4 output)

_failures: list[str] = []


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(label)
    return ok


class _ReferenceFacade:
    def current_snapshot_id(self) -> str | None:
        return reference.current_snapshot_id()

    def to_canonical(self, native: str, broker: str) -> str:
        return reference.to_canonical(native, broker)

    def exists(self, instrument_id: str, broker: str) -> bool:
        return reference.exists(instrument_id, broker)

    def get_instrument(self, instrument_id: str) -> dict:
        return reference.get_instrument(instrument_id)


def _build_candles(input_path: Path) -> list[Candle]:
    """Real ticks → M2.1→M2.2→M2.3→M2.4 (same pipeline as verify_m3.1)."""
    clock = SimulatedClock(datetime.fromisoformat(_DEFAULT_CLOCK))
    ingestor = TickIngestor(provider="deriv", clock=clock)
    normalizer = Normalizer(
        reference=_ReferenceFacade(), watermark_registry=WatermarkRegistry()
    )
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
            continue
        key = f"{checked.instrument_id}|{checked.broker}"
        if key not in builders:
            builders[key] = CandleBuilder(
                instrument_id=checked.instrument_id or "unknown",
                broker=checked.broker,
                interval="1m",
            )
        closed = builders[key].on_tick(checked)
        if isinstance(closed, Candle) and key.startswith("FX.EURUSD|"):
            candles.append(closed)
    for key, builder in builders.items():
        flushed = builder.close()
        if isinstance(flushed, Candle) and key.startswith("FX.EURUSD|"):
            candles.append(flushed)
    candles.sort(key=lambda c: c.interval_start)
    return candles


def _mk_candles(closes: list[float], *, ticks: int = 5, end: datetime | None = None) -> list[Candle]:
    """Minimal 1m candles with given closes (fixture series for computes)."""
    end = end or datetime(2026, 6, 1, 12, 0, tzinfo=UTC)  # tz-aware: as_of validator
    out = []
    for i, close in enumerate(closes):
        start = end - timedelta(minutes=len(closes) - i)
        out.append(
            Candle(
                open=close,
                high=close,
                low=close,
                close=close,
                tick_count=ticks,
                interval="1m",
                interval_start=start,
                interval_end=start + timedelta(minutes=1),
            )
        )
    return out


def _naive_rsi(closes: list[float], period: int = 14) -> float:
    """§3.5 second implementation — Wilder 1978 re-derived with a DIFFERENT code
    shape (alpha-form running average, rs·100/(1+rs) algebra) than
    analytics/rsi_14.py. Any disagreement beyond float noise is a bug in one of them."""
    if len(closes) <= period:
        raise ValueError("naive rsi needs > period closes")
    changes = [(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    avg_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    avg_loss = sum(max(-change, 0.0) for change in changes[:period]) / period
    for i in range(period, len(changes)):
        gain = max(changes[i], 0.0)
        loss = max(-changes[i], 0.0)
        avg_gain += (gain - avg_gain) / period  # alpha-form: a + (x-a)/p
        avg_loss += (loss - avg_loss) / period
    if avg_loss == 0.0:
        return 100.0
    if avg_gain == 0.0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 * rs / (1.0 + rs)


def _walk_candles(count: int) -> list[Candle]:
    """Deterministic 1m random walk seeded from the REAL sample close (spec §Verify 3)."""
    rng = random.Random(_WALK_SEED)
    price = _REAL_SAMPLE_CLOSE
    closes: list[float] = []
    for _ in range(count):
        price += rng.uniform(-0.0004, 0.0004)
        closes.append(round(price, 5))
    return _mk_candles(closes, ticks=rng.randint(1, 20))


def _request(as_of: datetime, features: tuple[str, ...]) -> FeatureRequest:
    return FeatureRequest(
        instrument_id="FX.EURUSD",
        broker="deriv",
        timeframe="1m",
        features=features,
        as_of=as_of,
        asset_class="forex",
    )


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="Verify M3.2 compute files (Step 7)")
    parser.add_argument("--input", default="data/sample_ticks_realistic.jsonl")
    args = parser.parse_args()

    # --- 1. D4 discovery -------------------------------------------------------
    names = sorted(AnalyticsRegistry.from_package().names())
    print(f"discovery: {names}")
    _check(
        "D4 discovery finds exactly P17 + P18 batches (15)",
        names == [
            "adx_14",
            "atr_14",
            "bb_percent",
            "choppiness",
            "donchian_20",
            "ema_20",
            "hurst",
            "macd",
            "returns",
            "rsi_14",
            "sma_20",
            "std_20",
            "tick_rate",
            "vol_regime",
            "zscore_20",
        ],
    )

    # --- 2. golden exactness (outside pytest) ----------------------------------
    flat = rsi_14.compute(_mk_candles([5.0] * 4), {"period": 2.0})
    losses = rsi_14.compute(_mk_candles([10.0, 9.0, 8.0]), {"period": 2.0})
    ema_golden = ema_20.compute(_mk_candles([2.0, 4.0, 6.0, 8.0]), {"period": 3.0})
    rate_golden = tick_rate.compute(_mk_candles([1.0, 1.0, 1.0], ticks=4), {})
    print(f"goldens: flat={flat} loss={losses} ema={ema_golden} tick_rate={rate_golden}")
    _check("golden: RSI flat -> 50.0 exactly (REV5/A48 neutral)", flat == 50.0)
    _check("golden: RSI all-loss -> 0.0 exactly", losses == 0.0)
    _check("golden: EMA [2,4,6,8] p3 -> 6.25 exactly", ema_golden == 6.25)
    # hand calc for tick_rate: (4+4+4)/3 = 12/3 = 4.0
    _check("golden: tick_rate mean -> 4.0 exactly", rate_golden == 4.0)

    # --- 3. two independent RSI implementations on the walk --------------------
    walk = _walk_candles(60)
    walk_closes = [c.close for c in walk]
    diffs: list[float] = []
    last_value = float("nan")
    for n in range(15, 61):
        ours = rsi_14.compute(_mk_candles(walk_closes[:n]), {"period": 14.0})
        theirs = _naive_rsi(walk_closes[:n], period=14)
        diffs.append(abs(ours - theirs))
        last_value = ours
    max_diff = max(diffs)
    print(f"cross-check: {len(diffs)} windows (15..60) seed={_WALK_SEED} "
          f"max_diff={max_diff:.3e} rsi_14(last 60)={last_value:.6f}")
    _check(
        "2-impl RSI agreement: max diff <= 1e-9 (spec §Verify 3)",
        max_diff <= 1e-9,
        f"max_diff={max_diff:.3e}",
    )

    # --- 4. real-sample thin path: honest refusal ------------------------------
    real_candles = _build_candles(Path(args.input))
    if not _check("real candles built from sample ticks (M2.4)", len(real_candles) >= 1,
                  f"count={len(real_candles)}"):
        print("\nRESULT: FAILED")
        return 1
    real_as_of = max(c.interval_end for c in real_candles)
    cap_registry = CapabilityRegistry.from_yaml(_CAP_YAML)
    profiles = Profiles.from_dir(_ASSETS_DIR)

    def engine() -> FeatureEngine:
        # registry=None -> D4 discovery (the REAL files, real forex profile params)
        return FeatureEngine(
            provider=StaticCandleProvider(real_candles),
            capability_registry=cap_registry,
            registry=None,
            resolver=None,
            cache=None,
            profiles=profiles,
        )

    res = await engine().get_features(_request(real_as_of, ("rsi_14",)))
    print(f"real thin: features={res.features} warn={res.capability_warning!r}")
    _check(
        "real sample is thinner than 15 bars -> INSUFFICIENT:rsi_14, no compute",
        res.features == {} and "INSUFFICIENT:rsi_14" in (res.capability_warning or ""),
        f"bars={len(real_candles)} warn={res.capability_warning!r}",
    )

    # --- 5. engine e2e on the 60-bar walk (default registry + real profiles) ---
    walk_as_of = max(c.interval_end for c in walk)
    walk_engine = FeatureEngine(
        provider=StaticCandleProvider(walk),
        capability_registry=cap_registry,
        registry=None,
        resolver=None,
        cache=None,
        profiles=profiles,
    )
    res = await walk_engine.get_features(
        _request(walk_as_of, ("rsi_14", "ema_20", "tick_rate"))
    )
    print(
        f"e2e: rsi_14={res.features.get('rsi_14')!r} ema_20={res.features.get('ema_20')!r} "
        f"tick_rate={res.features.get('tick_rate')!r} status={res.capability_status.value} "
        f"warn={res.capability_warning!r}"
    )
    _check(
        "e2e: all three features present on 60 pristine bars",
        set(res.features) == {"rsi_14", "ema_20", "tick_rate"},
        f"features={sorted(res.features)}",
    )
    _check(
        "e2e: tick_rate stamps PROXY_LIMITED + 'tick_count != volume' warning",
        res.capability_status is CapabilityStatus.PROXY_LIMITED
        and "tick_count \u2260 volume" in (res.capability_warning or ""),
        f"status={res.capability_status.value}",
    )

    # --- 6. ADVISORY analytics latency (not a gate — §4.1 enforcement later) ---
    samples_ms: list[float] = []
    for _ in range(100):
        t0 = time.perf_counter_ns()
        rsi_14.compute(_mk_candles(walk_closes), {"period": 14.0})
        samples_ms.append((time.perf_counter_ns() - t0) / 1e6)
    median_ms = statistics.median(samples_ms)
    within = median_ms < 20.0
    print(
        f"[ADVISORY] rsi_14 compute median={median_ms:.3f}ms over 100 runs "
        f"(min={min(samples_ms):.3f} max={max(samples_ms):.3f}) vs §4.1 budget 20ms — "
        f"{'within' if within else 'EXCEEDS'} budget; enforcement = engine latency "
        f"header (later slice), reported NOT gated here"
    )

    if _failures:
        for failure in _failures:
            print(f"[FAIL] {failure}")
        print("\nRESULT: FAILED")
        return 1
    print("\nRESULT: ALL OK (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
