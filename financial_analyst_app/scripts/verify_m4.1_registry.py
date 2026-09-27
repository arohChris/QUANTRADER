"""WHY: M4.1 Step 7 — real-config verification of the Strategy Registry
(spec docs/specs/m4.1_strategy_registry.spec.md §Verify, EXIT 0/1). NOT unit
tests: re-runs the P22 gate against the REAL 7 strategy files, the REAL
config/strategies merge precedence (defaults 55 -> yaml 60 -> request 65), the
REAL capabilities.yaml deriv matrix (6 True / microstructure_flow False), and
golden Signals through the REAL runner + registries using the A40-LGTM'd
canned AnalyticsResult hands (trend +1.0 long, rsi 85 -> -0.75 short, blocked
strategy -> None with zero engine calls). NOTE honestly: full FeatureEngine E2E
(real candles -> features -> Signal) lands with the M4 engine loop slice, not
here.

Usage: python scripts/verify_m4.1_registry.py
Exit: 0 = all checks OK · 1 = any check failed. Latency line is ADVISORY only.
"""
from __future__ import annotations

import asyncio
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.feature_engine import FeatureRequest
from modules.m4_alpha.strategy.base_strategy import StrategyEntry
from modules.m4_alpha.strategy.lifecycle import Lifecycle
from modules.m4_alpha.strategy.parameter_registry import ParameterRegistry
from modules.m4_alpha.strategy.runner import StrategyRunner
from modules.m4_alpha.strategy.shadow_mode import load_shadow_allowlist
from modules.m4_alpha.strategy.strategy_registry import StrategyRegistry
from shared.contracts.analytics import AnalyticsResult
from shared.contracts.market_data import MarketData, Tick

_TS = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
_TREND = {"ema_20": 101.0, "sma_20": 100.0, "rsi_14": 65.0}  # m4.2 hand: +1.0
_RSI_HOT = {"rsi_14": 85.0}                                  # m4.2 hand: -0.75
_SEVEN = ("donchian_breakout", "mean_reversion", "microstructure_flow",
          "pullback", "regime_based", "rsi_overbought_oversold", "trend_following")

_failures: list[str] = []


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(label)
    return ok


def _md() -> MarketData:
    return MarketData(
        instrument_id="FX.EURUSD", symbol="EURUSD", broker="deriv", provider="deriv",
        provider_symbol="frxEURUSD", kind="tick", watermark=7,
        payload=Tick(price=1.0854, bid=1.08538, ask=1.08542, spread=0.00004),
        exchange_timestamp=_TS, reference_snapshot_id="REF-2026-09-25-001")


def _ar(features: dict[str, float]) -> AnalyticsResult:
    """Realistic canned AnalyticsResult — A40 hand values, symbol=EURUSD."""
    return AnalyticsResult(symbol="EURUSD", broker="deriv", timeframe="1m",
                           features=features)


class CannedEngine:
    """Scripted FeatureEngine stand-in (records calls, replays results)."""

    def __init__(self, *results: AnalyticsResult) -> None:
        self._queue = list(results)
        self.calls = 0

    async def get_features(self, request: FeatureRequest) -> AnalyticsResult:
        self.calls += 1
        if not self._queue:
            raise AssertionError("canned engine exhausted — verify bug")
        return self._queue.pop(0)


def _runner(engine: CannedEngine, allowlist: frozenset[str] = frozenset()) -> StrategyRunner:
    registry = StrategyRegistry()
    registry.load()                                   # the P22 gate, re-run live
    params = ParameterRegistry(ROOT / "config" / "strategies")
    params.load(registry.names())
    return StrategyRunner(
        registry=registry, params=params, lifecycle=Lifecycle(registry.names()),
        capability=CapabilityRegistry.from_yaml(ROOT / "config" / "capabilities.yaml"),
        engine=engine, allowlist=allowlist)


async def _run(runner: StrategyRunner, name: str, **over: object):
    base: dict[str, object] = {
        "market": _md(), "instrument_id": "FX.EURUSD", "broker": "deriv",
        "timeframe": "1m", "asset_class": "forex", "as_of": _TS, "now": _TS,
        "env": "paper"}
    base.update(over)
    return await runner.run(name, **base)  # type: ignore[arg-type]


async def _verify() -> None:
    # --- 1. P22 gate re-run: real discovery --------------------------------
    registry = StrategyRegistry()
    registry.load()
    names = registry.names()
    print(f"P22 discovery ({len(names)}): {list(names)}")
    _check("registry.load() discovers exactly the 7 sorted names", names == _SEVEN,
           f"got {names!r}")
    _check("entries() are frozen StrategyEntry objects",
           all(isinstance(e, StrategyEntry) for e in registry.entries()),
           f"n={len(registry.entries())}")

    # --- 2. REAL merge precedence: defaults 55 -> yaml 60 -> request 65 -----
    params = ParameterRegistry(ROOT / "config" / "strategies")
    params.load(names)
    defaults = {"rsi_threshold": 55.0}
    d_only = params.effective("mean_reversion", defaults=defaults)["rsi_threshold"]
    d_yaml = params.effective("trend_following", defaults=defaults)["rsi_threshold"]
    d_req = params.effective("trend_following", defaults=defaults,
                             request={"rsi_threshold": 65.0})["rsi_threshold"]
    print(f"merge demo rsi_threshold: defaults-only={d_only} +yaml={d_yaml} "
          f"+request={d_req} | yaml version={params.version('trend_following')!r} "
          f"id={params.strategy_id('trend_following')!r}")
    _check("defaults-only path keeps module default (55)", d_only == 55.0)
    _check("yaml beats module defaults (60)", d_yaml == 60.0)
    _check("request beats yaml (65)", d_req == 65.0)
    _check("yaml float 2.1 normalized to str '2.1'",
           params.version("trend_following") == "2.1")
    _check("missing yaml -> stable defaults (id=name, version=1.0)",
           params.strategy_id("mean_reversion") == "mean_reversion"
           and params.version("mean_reversion") == "1.0")

    # --- 3. REAL capabilities.yaml deriv eligibility matrix ----------------
    capability = CapabilityRegistry.from_yaml(ROOT / "config" / "capabilities.yaml")
    expected = {n: n != "microstructure_flow" for n in names}
    print("\nstrategy eligibility @deriv (real config):")
    for strategy, want in expected.items():
        eligible, reason = capability.is_strategy_eligible(strategy, "deriv")
        print(f"  {strategy:<28} deriv -> ({eligible!s:<5}, {reason!r})")
        _check(f"eligibility {strategy}@deriv == {want}", eligible is want, reason)

    # --- 4. golden runs through the REAL runner + registries ---------------
    print("\ngolden runs (real runner, A40 canned hands):")
    paper = await _run(_runner(CannedEngine(_ar(_TREND))), "trend_following")
    _check("trend paper -> Signal long strength 1.0",
           paper is not None and paper.direction == "long" and paper.strength == 1.0
           and paper.strategy_id == "trend_following::ema_trend"
           and paper.version == "2.1" and paper.shadow is False,
           f"got {getattr(paper, 'direction', None)} {getattr(paper, 'strength', None)}")
    if paper is not None:
        print(f"  signal: {paper.strategy_id}@{paper.version} {paper.direction} "
              f"strength={paper.strength} shadow={paper.shadow} "
              f"valid_until={paper.valid_until.isoformat()} "
              f"producer={paper.provenance.producer if paper.provenance else None}")
    prod = await _run(_runner(CannedEngine(_ar(_TREND))), "trend_following",
                      env="prod")
    _check("trend prod unlisted -> shadow True (fail-closed)",
           prod is not None and prod.shadow is True)
    listed = await _run(_runner(CannedEngine(_ar(_TREND)),
                                allowlist=frozenset({"trend_following::ema_trend@2.1"})),
                        "trend_following", env="prod")
    _check("trend prod allowlisted -> shadow False",
           listed is not None and listed.shadow is False)
    allow = load_shadow_allowlist(ROOT / "config" / "strategies")
    _check("real shadow_allowlist.yaml loads fail-closed (prod: [])",
           allow == frozenset(), f"got {allow!r}")
    rsi = await _run(_runner(CannedEngine(_ar(_RSI_HOT))), "rsi_overbought_oversold")
    _check("rsi 85 -> Signal short strength 0.75",
           rsi is not None and rsi.direction == "short" and rsi.strength == 0.75
           and rsi.strategy_id == "rsi_overbought_oversold" and rsi.version == "1.0",
           f"got {getattr(rsi, 'direction', None)} {getattr(rsi, 'strength', None)}")
    micro_engine = CannedEngine(_ar({"order_book_imbalance": 0.9}))
    micro = await _run(_runner(micro_engine), "microstructure_flow")
    _check("microstructure blocked @deriv -> None with ZERO engine calls",
           micro is None and micro_engine.calls == 0,
           f"calls={micro_engine.calls}")

    # --- 5. ADVISORY latency: 100 x run() (reported, NOT gated) ------------
    engine = CannedEngine(*([_ar(_TREND)] * 100))
    runner = _runner(engine)
    samples: list[float] = []
    for _ in range(100):
        t0 = time.perf_counter_ns()
        await _run(runner, "trend_following")
        samples.append((time.perf_counter_ns() - t0) / 1e6)
    print(f"\n[ADVISORY] run() median {statistics.median(samples):.4f}ms over "
          f"100 runs (min {min(samples):.4f} / max {max(samples):.4f}) vs §4.1 "
          f"context — reported NOT gated")
    print("NOTE honestly: full FeatureEngine E2E (real candles -> features -> "
          "Signal) lands with the M4 engine loop slice, not here.")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    asyncio.run(_verify())
    if _failures:
        for failure in _failures:
            print(f"[FAIL] {failure}")
        print("\nRESULT: FAILED")
        return 1
    print("\nRESULT: ALL OK (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
