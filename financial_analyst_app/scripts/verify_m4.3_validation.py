"""WHY: M4.3 Step 7 — real-data verification of the Signal Validator.

Runs today's REAL BTC 1m collector data through the full M4.6 chain (FeatureEngine
-> StrategyRunner -> AlphaEngine) and validates every resulting Signal with the NEW
M4.3 SignalValidator (validator A, real stream), then injects ONE LABELED case per
rejection class on a fresh validator (validator B). ALL OK = all 6 rejection
classes demonstrated AND >=1 clean real signal forwarded (ready for M5).

Exit 0 = ALL OK; 1 = failure (printed reason); 2 = data unavailable.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

_v46spec = importlib.util.spec_from_file_location(
    "v46", APP / "scripts" / "verify_m4.6_alpha_engine.py")
assert _v46spec is not None and _v46spec.loader is not None
v46 = importlib.util.module_from_spec(_v46spec)
_v46spec.loader.exec_module(v46)

from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.feature_engine import (
    FeatureEngine,
    StaticCandleProvider,
)
from modules.m3_analytics.engine.profiles import Profiles
from modules.m4_alpha.engine.loop import AlphaEngine
from modules.m4_alpha.signal import (
    SignalRejection,
    SignalRejectReason,
    SignalValidator,
)
from modules.m4_alpha.strategy.lifecycle import Lifecycle
from modules.m4_alpha.strategy.parameter_registry import ParameterRegistry
from modules.m4_alpha.strategy.runner import StrategyRunner
from modules.m4_alpha.strategy.strategy_registry import StrategyRegistry
from shared.contracts.market_data import MarketData

INSTRUMENT = "CRYPTO.BTCUSD"
REPLAY_BARS = 10


class CollectingSink:
    def __init__(self) -> None:
        self.signals = []

    async def emit(self, signal) -> None:
        self.signals.append(signal)


def _md(candle) -> MarketData:
    return MarketData(
        instrument_id=INSTRUMENT, symbol="BTCUSD", broker="deriv",
        provider="deriv", provider_symbol="cryBTCUSD", kind="candle",
        watermark=0, payload=candle, exchange_timestamp=candle.interval_end)


async def main() -> int:
    day = v46.latest_run_dir(APP / "data" / "live_signals")
    candles, partial, _malformed = v46.load_candles([day / "history_1m.jsonl"])
    if len(candles) < 50:
        print(f"[FAIL] only {len(candles)} complete candles (partial={partial})")
        return 2
    wall = datetime.now(UTC)
    ready = [c for c in candles if c.interval_end <= wall]

    registry = StrategyRegistry(package="modules.m3_analytics.strategies")
    registry.load()
    params = ParameterRegistry(APP / "config" / "strategies")
    params.load(registry.names())
    capability = CapabilityRegistry.from_yaml(APP / "config" / "capabilities.yaml")
    engine = FeatureEngine(
        provider=StaticCandleProvider(ready),
        capability_registry=capability, resolver=None, cache=None,
        profiles=Profiles.from_dir(APP / "config" / "assets"))
    runner = StrategyRunner(
        registry=registry, params=params, lifecycle=Lifecycle(registry.names()),
        capability=capability, engine=engine)
    sink = CollectingSink()
    alpha = AlphaEngine(names=registry.names(), runner=runner, sink=sink)

    for c in ready[-REPLAY_BARS:]:
        await alpha.on_candle(_md(c), env="paper", now=wall)
    # validate AFTER replay: signal.timestamp = wall at construction, so a
    # `now` captured pre-replay would read every signal as future (invalid).
    check_wall = datetime.now(UTC)
    real = sink.signals
    print(f"[data] {len(ready)} bars, replayed last {REPLAY_BARS}; "
          f"real signals={len(real)} partial_skipped={partial}")
    if not real:
        print("[FAIL] no signals from the real chain")
        return 1
    bad_id = [s for s in real if s.symbol != INSTRUMENT]
    if bad_id:
        print(f"[FAIL] runner identity law broken: {bad_id[0].symbol!r} "
              f"(must be {INSTRUMENT!r})")
        return 1
    print(f"[identity] all {len(real)} signals carry instrument_id {INSTRUMENT!r} "
          f"(runner fix live)")

    # --- validator A: the real stream (fresh window; validate immediately) ---
    va = SignalValidator()
    res_a = va.validate_batch(real, now=check_wall)
    counts: dict[str, int] = {}
    for r in res_a.rejected:
        counts[r.reason.value] = counts.get(r.reason.value, 0) + 1
    print(f"[real stream] validated={len(res_a.validated)} "
          f"rejected={len(res_a.rejected)} {counts}")
    for s in list(res_a.validated)[:3]:
        print(f"  FORWARD -> {s.strategy_id} {s.direction} strength={s.strength:.4f} "
              f"valid_until={s.valid_until.isoformat()}")
    if not res_a.validated:
        print("[FAIL] zero clean signals forwarded from the real stream")
        return 1

    # --- validator B: one LABELED injection per rejection class (fresh state) ---
    vb = SignalValidator()
    base = real[0]
    seen: set[str] = set()

    def _note(out, want: SignalRejectReason, label: str) -> None:
        got = out.reason.value if isinstance(out, SignalRejection) else "PASSED"
        ok = isinstance(out, SignalRejection) and out.reason == want
        print(f"  [{'ok' if ok else 'FAIL'}] {label:<34} -> {got}")
        if ok:
            seen.add(want.value)

    from shared.contracts.signal import Signal

    # duplicate: distinct signal_ids, same key parts, same now
    first = vb.validate(base.model_copy(update={"signal_id": uuid4()}), now=check_wall)
    print(f"  [{'ok' if isinstance(first, Signal) else 'FAIL'}] "
          f"{'first pass records key':<34} -> "
          f"{'PASS' if isinstance(first, Signal) else 'REJECTED'}")
    _note(vb.validate(base.model_copy(update={"signal_id": uuid4()}), now=check_wall),
          SignalRejectReason.duplicate, "duplicate same key within 60s")
    _note(vb.validate(base.model_copy(update={"shadow": True}), now=check_wall),
          SignalRejectReason.shadow, "shadow=True")
    _note(vb.validate(base.model_copy(
        update={"valid_until": check_wall - timedelta(seconds=1)}), now=check_wall),
        SignalRejectReason.stale, "now > valid_until")
    _note(vb.validate(base.model_copy(
        update={"timestamp": check_wall - timedelta(seconds=61)}), now=check_wall),
        SignalRejectReason.latency_exceeded, "age 61s > budget 60s")
    _note(vb.validate(base.model_copy(
        update={"symbol": "FAKE.NOPES"}), now=check_wall),
        SignalRejectReason.unknown_symbol, "unknown instrument_id")
    _note(vb.validate(base.model_copy(
        update={"timestamp": check_wall + timedelta(seconds=5)}), now=check_wall),
        SignalRejectReason.invalid_timestamp, "future signal.timestamp")

    missing = sorted({r.value for r in SignalRejectReason} - seen)
    if missing:
        print(f"[FAIL] rejection classes not demonstrated: {missing}")
        return 1
    print(f"[classes] all 6 rejection classes demonstrated; "
          f"natural duplicates in real stream: {counts.get('duplicate', 0)}")
    print("[OK] M4.3 VALIDATION ALL OK — clean signals ready for M5")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
