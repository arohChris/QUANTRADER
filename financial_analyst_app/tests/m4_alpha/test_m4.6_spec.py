"""
M4.6 ALPHA ENGINE LOOP — Spec tests (WRITTEN BEFORE CODE — AGENTS §2 Step 3).

WHY: first real candles -> Signal path (A58 spec LGTM,
     docs/specs/m4.6_alpha_engine.spec.md). Honest RED until Step 5:
     ModuleNotFoundError `modules.m4_alpha.engine` (pytest EXIT 2) — never skipped.

Contract-notes for the A59 review (declared, not silent):
     1. FakeRunner re-raises ANY BaseException it is scripted with, so B3 can
        prove the loop's `except Exception` does NOT swallow KeyboardInterrupt.
     2. B4 shares ONE strategy_params dict across all names (on_candle law):
        with the REAL runner the window check fires before engine I/O, so
        capability-blocked strategies return None without a failure row —
        "unaffected" means exactly that.
     3. E15/B4 composition reuses the m4.1 E12 golden hands verbatim
        (ema_20/sma_20/rsi_14 keys, values 101/100/65 and 85).
     4. E14 pins Phase 0 as unconditional: empty `names` does NOT waive the
        env/shape validation (spec: fail-fast even with nothing to run).
     5. Fake signals are minimal frozen Signal contract instances; real
        construction stays m4.1 runner territory.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest

from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.feature_engine import FeatureRequest
from modules.m4_alpha.engine.loop import (  # RED until Step 5
    AlphaEngine,
    CandleOutcome,
    StrategyFailure,
)
from modules.m4_alpha.strategy.lifecycle import Lifecycle
from modules.m4_alpha.strategy.parameter_registry import ParameterRegistry
from modules.m4_alpha.strategy.runner import StrategyRunner
from modules.m4_alpha.strategy.strategy_registry import StrategyRegistry
from shared.contracts.analytics import AnalyticsResult
from shared.contracts.market_data import Candle, MarketData, Tick
from shared.contracts.signal import Signal

_APP = Path(__file__).resolve().parents[2]
_CAP_YAML = _APP / "config" / "capabilities.yaml"
_STRAT_DIR = _APP / "config" / "strategies"

_TS = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)           # interval_start
_END = _TS + timedelta(minutes=1)                         # interval_end 12:01:00
_NOW = _END + timedelta(seconds=5)                        # wall clock after close
_VALID = _NOW + timedelta(seconds=60)


def _candle(**over: object) -> Candle:
    base: dict[str, object] = {
        "open": 1.0850, "high": 1.0860, "low": 1.0840, "close": 1.0855,
        "interval": "1m", "interval_start": _TS, "interval_end": _END,
    }
    base.update(over)
    return Candle(**base)  # type: ignore[arg-type]


def _md(payload: Candle | Tick | None = None, *,
        kind: Literal["tick", "quote", "trade", "candle", "order_book"] = "candle",
        instrument_id: str | None = "FX.EURUSD", broker: str = "deriv") -> MarketData:
    if payload is None:
        payload = _candle()
    return MarketData(
        instrument_id=instrument_id, symbol="EURUSD", broker=broker,
        provider="deriv", provider_symbol="frxEURUSD", kind=kind, watermark=7,
        payload=payload, exchange_timestamp=_END,
        reference_snapshot_id="REF-2026-09-25-001",
    )


def _sig(tag: str = "a") -> Signal:
    return Signal(
        signal_id=uuid4(), strategy_id=f"trend_following::ema_trend@{tag}",
        version="1.0", symbol="EURUSD", broker="deriv", direction="long",
        strength=0.5, timeframe="1m", valid_until=_VALID,
    )


class FakeRunner:
    """Records every (name, kwargs) call; replays scripted results/exceptions."""

    def __init__(self, script: dict[str, object] | None = None) -> None:
        self.script = script or {}
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def run(self, name: str, **kwargs: object) -> Signal | None:
        self.calls.append((name, kwargs))
        result = self.script.get(name)
        if isinstance(result, BaseException):  # B3 needs BaseException propagation
            raise result
        return result  # type: ignore[return-value]


class RecordingSink:
    def __init__(self, *, raise_on: int | None = None) -> None:
        self.signals: list[Signal] = []
        self._raise_on = raise_on

    async def emit(self, signal: Signal) -> None:
        if self._raise_on is not None and len(self.signals) == self._raise_on:
            raise RuntimeError("sink down")
        self.signals.append(signal)


def _engine(names: tuple[str, ...], runner: FakeRunner,
            sink: RecordingSink | None = None) -> AlphaEngine:
    return AlphaEngine(names=names, runner=runner, sink=sink or RecordingSink())


class FakeEngine:
    """Scripted FeatureEngine — same contract as m4.1 tests (pops in request order)."""

    def __init__(self, *results: AnalyticsResult) -> None:
        self._results = list(results)
        self.requests: list[FeatureRequest] = []

    async def get_features(self, request: FeatureRequest) -> AnalyticsResult:
        self.requests.append(request)
        return self._results.pop(0)


def _real_runner(engine: FakeEngine) -> tuple[StrategyRunner, StrategyRegistry]:
    registry = StrategyRegistry(package="modules.m3_analytics.strategies")
    registry.load()
    params = ParameterRegistry(_STRAT_DIR)
    params.load(registry.names())
    runner = StrategyRunner(
        registry=registry, params=params, lifecycle=Lifecycle(registry.names()),
        capability=CapabilityRegistry.from_yaml(_CAP_YAML), engine=engine,
    )
    return runner, registry


def _analytics(features: dict[str, float]) -> AnalyticsResult:
    return AnalyticsResult(symbol="EURUSD", broker="deriv", timeframe="1m",
                           features=features)


# ---------------------------------------------------------------- E-edges

async def test_e1_happy_path_runs_all_names_in_order() -> None:
    sig_a, sig_c = _sig("a"), _sig("c")
    runner = FakeRunner({"a": sig_a, "b": None, "c": sig_c})
    sink = RecordingSink()
    engine = _engine(("a", "b", "c"), runner, sink)

    outcome = await engine.on_candle(_md(), env="paper", now=_NOW)

    assert [name for name, _ in runner.calls] == ["a", "b", "c"]
    assert outcome.signals == (sig_a, sig_c)
    assert outcome.failures == ()
    assert outcome.skip_reason is None
    assert sink.signals == [sig_a, sig_c]


async def test_e2_formative_candle_refused() -> None:
    runner = FakeRunner()
    engine = _engine(("a",), runner)

    with pytest.raises(ValueError, match="interval_end"):
        await engine.on_candle(_md(), env="paper", now=_END - timedelta(seconds=1))
    assert runner.calls == []


async def test_e3_naive_timestamps_rejected() -> None:
    cases = [
        ("now", {"now": datetime(2026, 9, 26, 12, 1)}),  # noqa: DTZ001 — naive under test
        ("interval_end", {"now": _NOW}),
        ("interval_start", {"now": _NOW}),
    ]
    for label, kw in cases:
        payload = _candle()
        if label == "interval_end":
            payload = _candle(
                interval_end=datetime(2026, 9, 26, 12, 1))  # noqa: DTZ001 — naive under test
        if label == "interval_start":
            payload = _candle(
                interval_start=datetime(2026, 9, 26, 12, 0))  # noqa: DTZ001 — naive under test
        runner = FakeRunner()
        engine = _engine(("a",), runner)
        with pytest.raises(ValueError, match="UTC"):
            await engine.on_candle(_md(payload), env="paper", **kw)
        assert runner.calls == [], label


async def test_e4_wrong_shape_rejected_before_any_run() -> None:
    cases = [
        ("tick kind", _md(_candle(), kind="tick")),
        ("tick payload with candle kind", _md(Tick(price=1.085), kind="candle")),
        ("instrument_id None", _md(instrument_id=None)),
        ("empty broker", _md(broker="")),
    ]
    for label, md in cases:
        runner = FakeRunner()
        engine = _engine(("a",), runner)
        with pytest.raises(ValueError):
            await engine.on_candle(md, env="paper", now=_NOW)
        assert runner.calls == [], label


async def test_e5_gapped_candle_skips_with_zero_io() -> None:
    runner = FakeRunner()
    engine = _engine(("a",), runner)

    outcome = await engine.on_candle(
        _md(_candle(quality_status="GAP_AFFECTED")), env="paper", now=_NOW)

    assert runner.calls == []
    assert outcome == CandleOutcome((), (), "quality=GAP_AFFECTED")


async def test_e6_low_activity_still_runs() -> None:
    runner = FakeRunner({"a": _sig()})
    engine = _engine(("a",), runner)

    outcome = await engine.on_candle(
        _md(_candle(quality_status="LOW_ACTIVITY")), env="paper", now=_NOW)

    assert [name for name, _ in runner.calls] == ["a"]
    assert len(outcome.signals) == 1
    assert outcome.skip_reason is None


async def test_e7_unknown_quality_status_skipped_safer() -> None:
    runner = FakeRunner()
    engine = _engine(("a",), runner)

    outcome = await engine.on_candle(
        _md(_candle(quality_status="WEIRD")), env="paper", now=_NOW)

    assert runner.calls == []
    assert outcome.skip_reason == "quality=WEIRD"


async def test_e8_one_failing_strategy_does_not_block_others() -> None:
    sig_b = _sig("b")
    runner = FakeRunner({"a": ValueError("boom"), "b": sig_b, "c": None})
    engine = _engine(("a", "b", "c"), runner)

    outcome = await engine.on_candle(_md(), env="paper", now=_NOW)

    assert [name for name, _ in runner.calls] == ["a", "b", "c"]
    assert outcome.failures == (StrategyFailure("a", "ValueError", "boom"),)
    assert outcome.signals == (sig_b,)


async def test_e9_all_none_is_quiet_empty_outcome() -> None:
    runner = FakeRunner({"a": None, "b": None})
    sink = RecordingSink()
    engine = _engine(("a", "b"), runner, sink)

    outcome = await engine.on_candle(_md(), env="paper", now=_NOW)

    assert outcome == CandleOutcome((), (), None)
    assert sink.signals == []


async def test_e10_asset_class_derived_from_instrument_prefix() -> None:
    cases = [("FX.EURUSD", "forex"), ("CRYPTO.BTCUSD", "crypto"),
             ("COMMODITY.XAUUSD", "commodity")]
    for instrument_id, expected in cases:
        runner = FakeRunner({"a": None})
        engine = _engine(("a",), runner)
        await engine.on_candle(_md(instrument_id=instrument_id), env="paper", now=_NOW)
        assert runner.calls[0][1]["asset_class"] == expected, instrument_id

    runner = FakeRunner()
    engine = _engine(("a",), runner)
    with pytest.raises(ValueError, match="asset_class"):
        await engine.on_candle(_md(instrument_id="STOCK.AAPL"), env="paper", now=_NOW)
    assert runner.calls == []


async def test_e11_as_of_is_candle_close_not_now_and_kwargs_passthrough() -> None:
    for user_id in (None, "u1"):
        runner = FakeRunner({"a": None})
        engine = _engine(("a",), runner)
        await engine.on_candle(_md(), env="prod", now=_NOW, user_id=user_id)
        kw = runner.calls[0][1]
        # Hand: interval_end 12:01:00 is the PIT boundary; now stays the wall clock
        assert kw["as_of"] == _END
        assert kw["now"] == _NOW
        assert kw["timeframe"] == "1m"
        assert kw["env"] == "prod"
        assert kw["user_id"] == user_id
        assert kw["broker"] == "deriv"
        assert kw["instrument_id"] == "FX.EURUSD"


async def test_e12_emit_receives_signals_once_in_names_order() -> None:
    sig_a, sig_c = _sig("a"), _sig("c")
    runner = FakeRunner({"a": sig_a, "b": None, "c": sig_c})
    sink = RecordingSink()
    engine = _engine(("a", "b", "c"), runner, sink)

    outcome = await engine.on_candle(_md(), env="paper", now=_NOW)

    assert sink.signals == [sig_a, sig_c]
    assert sink.signals[0] is outcome.signals[0]
    assert sink.signals[1] is outcome.signals[1]


async def test_e13_sink_error_propagates_loud() -> None:
    runner = FakeRunner({"a": _sig()})
    sink = RecordingSink(raise_on=0)
    engine = _engine(("a",), runner, sink)

    with pytest.raises(RuntimeError, match="sink down"):
        await engine.on_candle(_md(), env="paper", now=_NOW)


async def test_e14_empty_names_still_validates_inputs() -> None:
    sink = RecordingSink()
    engine = _engine((), FakeRunner(), sink)

    outcome = await engine.on_candle(_md(), env="paper", now=_NOW)
    assert outcome == CandleOutcome((), (), None)
    assert sink.signals == []

    with pytest.raises(ValueError, match="env"):
        await engine.on_candle(_md(), env="live", now=_NOW)


async def test_e15_composition_golden_real_runner_real_registries() -> None:
    # Hand calc: m4.1 E12 golden — ema_20 101 > sma_20 100, rsi 65 >= 60
    #   -> evaluate +1.0 -> Signal long strength 1.0 (paper env: never shadowed)
    # Hand calc: rsi 85 >= 70 -> evaluate -0.75 -> Signal short strength 0.75
    trend = _analytics({"ema_20": 101.0, "sma_20": 100.0, "rsi_14": 65.0})
    rsi_hot = _analytics({"rsi_14": 85.0})
    fake_engine = FakeEngine(trend, rsi_hot)
    runner, _ = _real_runner(fake_engine)
    sink = RecordingSink()
    names = ("trend_following", "rsi_overbought_oversold", "microstructure_flow")
    engine = AlphaEngine(names=names, runner=runner, sink=sink)

    outcome = await engine.on_candle(_md(), env="paper", now=_NOW)

    assert len(outcome.signals) == 2
    trend_sig = next(s for s in outcome.signals
                     if s.strategy_id.startswith("trend_following"))
    rsi_sig = next(s for s in outcome.signals
                   if s.strategy_id.startswith("rsi_overbought"))
    assert (trend_sig.direction, trend_sig.strength) == ("long", 1.0)
    assert (rsi_sig.direction, rsi_sig.strength) == ("short", 0.75)
    assert all(s.shadow is False for s in outcome.signals)
    # microstructure blocked by capability BEFORE any feature I/O (no 3rd request)
    assert len(fake_engine.requests) == 2
    assert outcome.failures == ()


# ---------------------------------------------------------------- B-group

async def test_b1_interval_end_must_exceed_start() -> None:
    for payload in (_candle(interval_end=_TS),
                    _candle(interval_end=_TS - timedelta(seconds=1))):
        runner = FakeRunner()
        engine = _engine(("a",), runner)
        with pytest.raises(ValueError, match="interval"):
            await engine.on_candle(_md(payload), env="paper", now=_NOW)
        assert runner.calls == []


def test_b2_bad_names_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="names"):
        AlphaEngine(names=("a", "a"), runner=FakeRunner(), sink=RecordingSink())
    with pytest.raises(ValueError, match="names"):
        AlphaEngine(names=("a", 123), runner=FakeRunner(), sink=RecordingSink())  # type: ignore[arg-type]


async def test_b3_keyboard_interrupt_propagates_not_isolated() -> None:
    runner = FakeRunner({"a": KeyboardInterrupt(), "b": _sig()})
    engine = _engine(("a", "b"), runner)

    with pytest.raises(KeyboardInterrupt):
        await engine.on_candle(_md(), env="paper", now=_NOW)
    # isolation must NOT have continued to "b" after BaseException
    assert [name for name, _ in runner.calls] == ["a"]


async def test_b4_hostile_window_isolated_with_real_runner_zero_io() -> None:
    fake_engine = FakeEngine()
    runner, _ = _real_runner(fake_engine)
    sink = RecordingSink()
    engine = AlphaEngine(
        names=("trend_following", "microstructure_flow"),
        runner=runner, sink=sink)

    outcome = await engine.on_candle(_md(), env="paper", now=_NOW,
                                     strategy_params={"valid_until_seconds": -5})

    assert outcome.signals == ()
    assert len(outcome.failures) == 1
    failure = outcome.failures[0]
    assert failure.strategy == "trend_following"
    assert failure.error_type == "ValueError"
    assert "valid_until_seconds" in failure.message
    # window check happens BEFORE engine I/O — no feature call was spent
    assert fake_engine.requests == []
    assert sink.signals == []
