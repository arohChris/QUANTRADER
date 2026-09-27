#!/usr/bin/env python3
"""
verify_m4.6_alpha_engine — Step 7 REAL-data replay (spec "Verify" section).
Chain under test (all real, no fakes): collector BTC 1m artifact candles ->
FeatureEngine(StaticCandleProvider) -> StrategyRunner -> AlphaEngine -> sink.
WHY offline: deterministic + re-runnable (no WS, no orders, dry-run law §3.10);
  the collector owns live capture; we consume its artifacts.
EXIT 0 iff >=1 candle processed AND every emitted signal is structurally valid
  AND zero exceptions escaped the engine. Signal COUNT is advisory (flat market
  is honest, never forced). Partial artifact lines are counted, never fabricated.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))  # same precedent as verify_m4.1_registry.py

# WHY: loop's per-candle failure warnings are summarized as grouped [failure]
# rows below with counts — raw lines would bury the summary (same data, less noise).
logging.getLogger("modules.m4_alpha.engine.loop").setLevel(logging.ERROR)

from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.feature_engine import (
    FeatureEngine,
    StaticCandleProvider,
)
from modules.m3_analytics.engine.profiles import Profiles
from modules.m4_alpha.engine.loop import AlphaEngine, CandleOutcome
from modules.m4_alpha.strategy.lifecycle import Lifecycle
from modules.m4_alpha.strategy.parameter_registry import ParameterRegistry
from modules.m4_alpha.strategy.runner import StrategyRunner
from modules.m4_alpha.strategy.strategy_registry import StrategyRegistry
from shared.contracts.market_data import Candle, MarketData
from shared.contracts.signal import Signal

_CAP_YAML = _ROOT / "config" / "capabilities.yaml"
_ASSETS_DIR = _ROOT / "config" / "assets"
_STRAT_DIR = _ROOT / "config" / "strategies"
_REQUIRED = ("interval_start", "interval_end", "open", "high", "low", "close")


def latest_run_dir(base: Path) -> Path:
    days = sorted((p for p in base.glob("*") if p.is_dir()),
                  key=lambda p: p.name)
    if not days:
        raise SystemExit(f"[FAIL] no run dirs under {base}")
    return days[-1]


def load_candles(paths: list[Path]) -> tuple[list[Candle], int, int]:
    """Dedupe by interval_start (live replaces seed), ascending. Returns
    (candles, skipped_partial, malformed) — partial lines are NEVER fabricated."""
    by_start: dict[datetime, Candle] = {}
    partial = malformed = 0
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(row, dict) or any(k not in row for k in _REQUIRED):
                partial += 1
                continue
            row.setdefault("interval", "1m")  # writer omits it; filename = authority
            try:
                candle = Candle.model_validate(row)
            except Exception:  # noqa: BLE001 — artifact noise, counted not crashed
                malformed += 1
                continue
            by_start[candle.interval_start] = candle
    return [by_start[k] for k in sorted(by_start)], partial, malformed


class CollectingSink:
    def __init__(self) -> None:
        self.signals: list[Signal] = []

    async def emit(self, signal: Signal) -> None:
        self.signals.append(signal)


def structural_errors(signals: list[Signal], emit_time: datetime) -> list[str]:
    bad: list[str] = []
    for s in signals:
        if not 0.0 < s.strength <= 1.0:
            bad.append(f"{s.signal_id} strength {s.strength} outside (0,1]")
        if s.direction not in ("long", "short"):
            bad.append(f"{s.signal_id} direction {s.direction!r}")
        if not s.valid_until > emit_time:
            bad.append(f"{s.signal_id} valid_until {s.valid_until} <= emit time")
        if not isinstance(s.shadow, bool):
            bad.append(f"{s.signal_id} shadow not bool")
    return bad


async def run(args: argparse.Namespace) -> int:
    day_dir = Path(args.data_dir)
    history = day_dir / "history_1m.jsonl"
    live = day_dir / "live_candles_1m.jsonl"
    candles, partial, malformed = load_candles([history, live])
    print(f"[data] history={history} exists={history.exists()} "
          f"live={live.exists()} usable={len(candles)} "
          f"partial_skipped={partial} malformed={malformed}")
    if not candles:
        print("[FAIL] no usable candles")
        return 1
    print(f"[data] range {candles[0].interval_start.isoformat()} -> "
          f"{candles[-1].interval_end.isoformat()}")

    wall = datetime.now(UTC)
    ready = [c for c in candles if c.interval_end <= wall]
    future = len(candles) - len(ready)
    if not ready:
        print("[FAIL] every candle is still formative (interval_end > now)")
        return 1

    # ---- real chain (console construction precedent, verify_m4.2:482) ----
    registry = StrategyRegistry(package="modules.m3_analytics.strategies")
    registry.load()
    params = ParameterRegistry(_STRAT_DIR)
    params.load(registry.names())
    capability = CapabilityRegistry.from_yaml(_CAP_YAML)
    feature_engine = FeatureEngine(
        provider=StaticCandleProvider(ready),
        capability_registry=capability,
        resolver=None,
        cache=None,
        profiles=Profiles.from_dir(_ASSETS_DIR),
    )
    runner = StrategyRunner(
        registry=registry, params=params, lifecycle=Lifecycle(registry.names()),
        capability=capability, engine=feature_engine)
    sink = CollectingSink()
    engine = AlphaEngine(names=registry.names(), runner=runner, sink=sink)
    print(f"[chain] {len(registry.names())} strategies: {', '.join(registry.names())}")

    processed = 0
    escaped: list[str] = []
    skipped: Counter[str] = Counter()
    failure_rows: Counter[str] = Counter()
    latencies: list[float] = []
    for candle in ready:
        md = MarketData(
            instrument_id=args.instrument_id, symbol=args.symbol, broker="deriv",
            provider="deriv", provider_symbol=args.provider_symbol,
            kind="candle", watermark=processed + 1, payload=candle,
            exchange_timestamp=candle.interval_end,
        )
        started = time.perf_counter()
        try:
            outcome: CandleOutcome = await engine.on_candle(
                md, env=args.env, now=datetime.now(UTC))
        except Exception as exc:  # noqa: BLE001 — any escape = EXIT 1 below
            escaped.append(f"{candle.interval_start.isoformat()}: "
                           f"{type(exc).__name__}: {exc}")
            continue
        latencies.append((time.perf_counter() - started) * 1000.0)
        processed += 1
        if outcome.skip_reason is not None:
            skipped[outcome.skip_reason] += 1
        for f in outcome.failures:
            failure_rows[f"{f.strategy} {f.error_type}: {f.message}"] += 1

    emit_time = datetime.now(UTC)
    bad = structural_errors(sink.signals, emit_time)
    per_strategy = Counter(s.strategy_id for s in sink.signals)
    per_direction = Counter(s.direction for s in sink.signals)

    print(f"[replay] processed={processed} future_precheck_skipped={future} "
          f"quality_skips={dict(skipped) or '{}'} "
          f"latency_ms_median={statistics.median(latencies) if latencies else 0:.3f} "
          "(ADVISORY vs analytics budget)")
    print(f"[signals] total={len(sink.signals)} "
          f"per_strategy={dict(per_strategy) or '{}'} "
          f"per_direction={dict(per_direction) or '{}'}")
    for row, count in sorted(failure_rows.items()):
        print(f"[failure] {count}x {row}")
    for s in sink.signals[:5]:
        print(f"[signal] {s.strategy_id} {s.direction} strength={s.strength} "
              f"valid_until={s.valid_until.isoformat()} shadow={s.shadow} "
              f"id={s.signal_id}")
    if not sink.signals:
        print("[ADVISORY] 0 signals on this data — count is advisory per spec "
              "(market may be flat / warmup); structural check still applies")
    if escaped:
        for row in escaped:
            print(f"[FAIL] escaped {row}")
    if bad:
        for row in bad:
            print(f"[FAIL] structural {row}")

    ok = processed >= 1 and not escaped and not bad
    print(f"RESULT: {'ALL OK (exit 0)' if ok else 'FAILED (exit 1)'} — "
          f"processed={processed} signals={len(sink.signals)} "
          f"failures={sum(failure_rows.values())}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M4.6 AlphaEngine real-data replay")
    parser.add_argument("--data-dir", default=None,
                        help="run dir (default: latest data/live_signals/<day>)")
    parser.add_argument("--instrument-id", default="CRYPTO.BTCUSD")
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--provider-symbol", default="cryBTCUSD")
    parser.add_argument("--env", default="paper", choices=["paper", "prod"])
    args = parser.parse_args(argv)
    if args.data_dir is None:
        args.data_dir = latest_run_dir(_ROOT / "data" / "live_signals")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
