"""
M4.1 STRATEGY REGISTRY — Spec tests (WRITTEN BEFORE CODE — AGENTS §2 Step 3).

WHY: P22 gate is `registry.load()`; this file locks discovery, contract
     validation, capability/lifecycle/param/shadow gates, and the ONLY
     Signal-construction path BEFORE modules/m4_alpha exists (A54 spec LGTM,
     docs/specs/m4.1_strategy_registry.spec.md). Honest RED until Step 5:
     ModuleNotFoundError `modules.m4_alpha` (pytest EXIT 2) — never skipped.

Contract-notes for the A55 review (declared, not silent):
     1. StrategyEntry fields pinned here = (name, module, slots, defaults,
        evaluate) — spec's strategy_id/version list amended: those are
        yaml-owned (ParameterRegistry), not module truth.
     2. Duplicate-NAME edge is structurally unreachable while NAME==module-stem
        is enforced (one dir cannot hold two same-stem files); impl keeps the
        runtime duplicate check as defense-in-depth per spec — review-only.
     3. discovery names must come back SORTED (deterministic load order).
     4. capability gate keys are bare NAMEs (config/capabilities.yaml
        `strategies:` block), NOT `family::sub` strategy_ids.
     5. Signal.version is the strategy version STRING (contract field) —
        ParameterRegistry must normalize yaml float 2.1 -> "2.1".
"""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.feature_engine import FeatureRequest
from modules.m4_alpha.strategy.base_strategy import StrategyEntry  # RED until Step 5
from modules.m4_alpha.strategy.lifecycle import Lifecycle
from modules.m4_alpha.strategy.parameter_registry import (
    ParameterConfigError,
    ParameterRegistry,
)
from modules.m4_alpha.strategy.runner import StrategyRunner
from modules.m4_alpha.strategy.shadow_mode import apply_shadow, load_shadow_allowlist
from modules.m4_alpha.strategy.strategy_registry import (
    StrategyContractError,
    StrategyRegistry,
    UnknownStrategyError,
)
from shared.contracts.analytics import AnalyticsResult
from shared.contracts.market_data import MarketData, Tick
from shared.contracts.signal import Signal

_APP = Path(__file__).resolve().parents[2]
_CAP_YAML = _APP / "config" / "capabilities.yaml"
_STRAT_DIR = _APP / "config" / "strategies"
_TS = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)

_TREND = {"ema_20": 101.0, "sma_20": 100.0, "rsi_14": 65.0}   # m4.2 hand: +1.0 long
_RSI_HOT = {"rsi_14": 85.0}                                    # m4.2 hand: -0.75
_RSI_MID = {"rsi_14": 50.0}                                    # -> 0.0 no signal

_VALID_MODULE = """\
NAME = "{name}"
FEATURE_SLOTS = {slots}
PARAM_DEFAULTS = {defaults}
{extra}def evaluate(features, params):
    {body}
"""


def _md() -> MarketData:
    return MarketData(
        instrument_id="FX.EURUSD", symbol="EURUSD", broker="deriv", provider="deriv",
        provider_symbol="frxEURUSD", kind="tick", watermark=7,
        payload=Tick(price=1.0854, bid=1.08538, ask=1.08542, spread=0.00004),
        exchange_timestamp=_TS, reference_snapshot_id="REF-2026-09-25-001",
    )


def _analytics(features: dict[str, float] | None = None) -> AnalyticsResult:
    return AnalyticsResult(
        symbol="EURUSD", broker="deriv", timeframe="1m",
        features={} if features is None else features,
    )


class FakeEngine:
    """Scripted FeatureEngine — records every request, replays canned results."""

    def __init__(self, *results: AnalyticsResult) -> None:
        self._results = list(results)
        self.requests: list[FeatureRequest] = []

    async def get_features(self, request: FeatureRequest) -> AnalyticsResult:
        self.requests.append(request)
        if not self._results:
            raise AssertionError("FakeEngine exhausted — test bug")
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _runner(
    engine: FakeEngine, *, package: str = "modules.m3_analytics.strategies",
    config_dir: Path = _STRAT_DIR, allowlist: frozenset[str] = frozenset(),
) -> StrategyRunner:
    registry = StrategyRegistry(package=package)
    registry.load()
    params = ParameterRegistry(config_dir)
    params.load(registry.names())
    capability = CapabilityRegistry.from_yaml(_CAP_YAML)
    return StrategyRunner(
        registry=registry, params=params, lifecycle=Lifecycle(registry.names()),
        capability=capability, engine=engine, allowlist=allowlist,
    )


async def _run(runner: StrategyRunner, name: str, **over: object) -> Signal | None:
    base: dict[str, object] = {
        "market": _md(), "instrument_id": "FX.EURUSD", "broker": "deriv",
        "timeframe": "1m", "asset_class": "forex", "as_of": _TS, "now": _TS,
        "env": "paper",
    }
    base.update(over)
    return await runner.run(name, **base)  # type: ignore[arg-type]


def _write_pkg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
               files: dict[str, str]) -> str:
    pkg = tmp_path / "fake_strats"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for stem, body in files.items():
        (pkg / f"{stem}.py").write_text(body, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return "fake_strats"


def _module(name: str = "demo", slots: str = '{"a": ("analytic_x", {})}',
            defaults: str = '{"k": 1.0}', body: str = "return 0.0",
            extra: str = "") -> str:
    return _VALID_MODULE.format(name=name, slots=slots, defaults=defaults,
                                body=body, extra=extra)


# ---- E1/E2/E3/E4: discovery + contract validation (P22 gate) --------------------

def test_load_discovers_exactly_seven_names_sorted() -> None:
    registry = StrategyRegistry()
    registry.load()
    assert registry.names() == (
        "donchian_breakout", "mean_reversion", "microstructure_flow",
        "pullback", "regime_based", "rsi_overbought_oversold", "trend_following",
    )


def test_entries_hold_real_module_contract() -> None:
    registry = StrategyRegistry()
    registry.load()
    entry = registry.get("trend_following")
    assert isinstance(entry, StrategyEntry)
    assert entry.name == "trend_following"
    assert entry.slots == {
        "fast": ("ema_20", {"period": 10.0}),
        "slow": ("sma_20", {"period": 20.0}),
        "rsi": ("rsi_14", {}),
    }
    assert entry.defaults == {"rsi_threshold": 60.0, "divergence_full_pct": 1.0}
    assert callable(entry.evaluate)


def test_strategy_entry_is_frozen() -> None:
    registry = StrategyRegistry()
    registry.load()
    entry = registry.get("trend_following")
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.name = "hacked"  # type: ignore[misc]


def test_load_is_idempotent() -> None:
    registry = StrategyRegistry()
    registry.load()
    first = registry.names()
    registry.load()
    assert registry.names() == first


def test_get_unknown_raises_unknown_strategy_error() -> None:
    registry = StrategyRegistry()
    registry.load()
    with pytest.raises(UnknownStrategyError):
        registry.get("ghost_strategy")


def test_private_and_init_modules_skipped(tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    package = _write_pkg(tmp_path, monkeypatch, {
        "__init__": "",
        "_hidden": _module(name="_hidden"),
        "visible": _module(name="visible"),
    })
    registry = StrategyRegistry(package=package)
    registry.load()
    assert registry.names() == ("visible",)


@pytest.mark.parametrize(("stem", "source"), [
    ("demo", (
        'NAME = "demo"\nFEATURE_SLOTS = {"a": ("x", {})}\n'
        'PARAM_DEFAULTS = {"k": 1.0}\n'
    )),                                                         # missing evaluate
    ("demo", (
        'NAME = "demo"\nFEATURE_SLOTS = {"a": ("x", {})}\n'
        'PARAM_DEFAULTS = {"k": 1.0}\n'
        'def evaluate(features):\n    return 0.0\n'
    )),                                              # signature != [f, p]
    ("demo", _module(name="Demo One")),                            # non-snake NAME
    ("other_stem", _module(name="demo")),                          # NAME != stem
    ("demo", _module(slots='{"a": ("x", {}), "b": ("x", {})}')),    # non-distinct slots
    ("demo", _module(defaults='{"k": float("nan")}')),              # non-finite default
    ("demo", _module(extra='provider = "deriv"\n')),                # isolation attr
])
def test_contract_violation_rejected_at_load(tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch,
                                             stem: str, source: str) -> None:
    package = _write_pkg(tmp_path, monkeypatch, {stem: source})
    registry = StrategyRegistry(package=package)
    with pytest.raises(StrategyContractError):
        registry.load()


# ---- E7/E8/E9: parameter registry ------------------------------------------------

def test_param_precedence_request_over_yaml_over_defaults() -> None:
    runner = _runner(FakeEngine(_analytics(_TREND)))
    assert runner.params.effective(
        "trend_following", defaults={"rsi_threshold": 60.0},
        request={"rsi_threshold": 55.0},
    )["rsi_threshold"] == 55.0
    assert runner.params.effective(
        "trend_following", defaults={"rsi_threshold": 60.0},
    )["rsi_threshold"] == 60.0                                             # yaml (60)
    assert runner.params.effective(
        "mean_reversion", defaults={"entry": 2.0},
    )["entry"] == 2.0                                                     # no yaml -> default


def test_strategy_without_yaml_uses_defaults_and_stable_ids() -> None:
    runner = _runner(FakeEngine(_analytics({"z": 1.0})))
    assert runner.params.version("mean_reversion") == "1.0"
    assert runner.params.strategy_id("mean_reversion") == "mean_reversion"
    assert runner.params.version("trend_following") == "2.1"   # yaml float 2.1 -> str
    assert runner.params.strategy_id("trend_following") == "trend_following::ema_trend"


def test_effective_params_include_yaml_extras_and_validity() -> None:
    runner = _runner(FakeEngine(_analytics(_TREND)))
    merged = runner.params.effective("trend_following", defaults={"rsi_threshold": 60.0})
    assert merged["valid_until_seconds"] == 60                            # yaml window
    assert merged["ema_fast"] == 10                                        # yaml extra
    assert merged["rsi_threshold"] == 60


@pytest.mark.parametrize("text", [
    "params: [1, 2]\n",                       # params not a mapping
    "version: [2.1]\n",                       # version not a str
    'params:\n  k: "x"\n',                    # param not a number
    "params: [1, 2\n",                        # yaml parse error (unclosed flow)
])
def test_malformed_yaml_rejected_at_load(tmp_path: Path, text: str) -> None:
    (tmp_path / "trend_following.yaml").write_text(text, encoding="utf-8")
    params = ParameterRegistry(tmp_path)
    with pytest.raises(ParameterConfigError):
        params.load(["trend_following"])


# ---- E5/E6: capability + lifecycle gates ----------------------------------------

async def test_capability_gate_blocks_microstructure_on_deriv() -> None:
    engine = FakeEngine(_analytics({"order_book_imbalance": 0.9}))
    runner = _runner(engine)
    assert await _run(runner, "microstructure_flow") is None
    assert engine.requests == []            # gate fires BEFORE any feature I/O


async def test_capability_gate_passes_trend_and_shares_one_request() -> None:
    engine = FakeEngine(_analytics(_TREND))
    runner = _runner(engine)
    signal = await _run(runner, "trend_following")
    assert isinstance(signal, Signal)
    assert len(engine.requests) == 1


async def test_lifecycle_disable_blocks_then_reenable_restores() -> None:
    engine = FakeEngine(_analytics(_TREND), _analytics(_TREND))
    runner = _runner(engine)
    runner.lifecycle.disable("trend_following")
    assert await _run(runner, "trend_following") is None
    assert engine.requests == []
    runner.lifecycle.enable("trend_following")
    assert isinstance(await _run(runner, "trend_following"), Signal)
    assert len(engine.requests) == 1


def test_lifecycle_unknown_name_raises() -> None:
    lifecycle = Lifecycle(("trend_following",))
    with pytest.raises(UnknownStrategyError):
        lifecycle.disable("ghost")
    with pytest.raises(UnknownStrategyError):
        lifecycle.enable("ghost")
    assert lifecycle.is_enabled("trend_following") is True    # default enabled


# ---- component 6: FeatureRequest construction ------------------------------------

async def test_feature_request_built_from_slot_union() -> None:
    engine = FakeEngine(_analytics(_TREND))
    runner = _runner(engine)
    assert await _run(runner, "trend_following") is not None
    request = engine.requests[0]
    assert request.features == ("ema_20", "sma_20", "rsi_14")  # slot order
    assert request.param_overrides == {                        # empty {} slots omitted
        "ema_20": {"period": 10.0}, "sma_20": {"period": 20.0},
    }
    assert request.as_of == _TS and request.timeframe == "1m"
    assert request.asset_class == "forex" and request.broker == "deriv"
    assert request.instrument_id == "FX.EURUSD"


# ---- E10-E14/E16-E18: runner behaviour -------------------------------------------

async def test_missing_slot_analytic_raises_naming_slot_and_analytic() -> None:
    engine = FakeEngine(_analytics({"ema_20": 101.0}))       # sma_20 + rsi_14 absent
    runner = _runner(engine)
    with pytest.raises(ValueError, match="slow"):
        await _run(runner, "trend_following")


async def test_evaluate_zero_returns_none() -> None:
    engine = FakeEngine(_analytics(_RSI_MID))
    runner = _runner(engine)
    assert await _run(runner, "rsi_overbought_oversold") is None


async def test_golden_trend_long_full_strength() -> None:
    # Hand calc (m4.2 A40): fast=101 slow=100 gap 1% = full -> evaluate +1.0
    #   -> Signal long, strength = |+1.0| = 1.0 (exact pass-through, no clamp)
    engine = FakeEngine(_analytics(_TREND))
    runner = _runner(engine)
    signal = await _run(runner, "trend_following")
    assert isinstance(signal, Signal)
    assert signal.direction == "long" and signal.strength == 1.0
    assert signal.symbol == "EURUSD" and signal.broker == "deriv"
    assert signal.timeframe == "1m" and signal.shadow is False
    assert signal.strategy_id == "trend_following::ema_trend"
    assert signal.version == "2.1" and signal.user_id is None


async def test_golden_rsi_overbought_short_075() -> None:
    # Hand calc (m4.2 A40): rsi=85, ob=70 -> progress 15/30 = 0.5
    #   -> evaluate -(0.5 + 0.5*0.5) = -0.75 -> Signal short, strength 0.75
    engine = FakeEngine(_analytics(_RSI_HOT))
    runner = _runner(engine)
    signal = await _run(runner, "rsi_overbought_oversold")
    assert isinstance(signal, Signal)
    assert signal.direction == "short" and signal.strength == 0.75
    assert signal.strategy_id == "rsi_overbought_oversold"
    assert signal.version == "1.0"


async def test_valid_until_exact_utc_window() -> None:
    engine = FakeEngine(_analytics(_TREND))
    runner = _runner(engine)
    now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    signal = await _run(runner, "trend_following", now=now)
    assert isinstance(signal, Signal)
    assert signal.valid_until == datetime(2026, 9, 26, 12, 1, tzinfo=UTC)  # +60s


@pytest.mark.parametrize("field", ["as_of", "now"])
async def test_naive_timestamps_rejected_before_io(field: str) -> None:
    engine = FakeEngine(_analytics(_TREND))
    runner = _runner(engine)
    naive = datetime(2026, 9, 26, 12, 0)                       # noqa: DTZ001 — attack
    with pytest.raises(ValueError):
        await _run(runner, "trend_following", **{field: naive})
    assert engine.requests == []


@pytest.mark.parametrize("window", [0, 7200])
async def test_validity_window_bounds_enforced(window: int) -> None:
    engine = FakeEngine(_analytics(_TREND))
    runner = _runner(engine)
    with pytest.raises(ValueError):
        await _run(
            runner, "trend_following",
            strategy_params={"valid_until_seconds": window},
        )


async def test_determinism_equal_except_generated_ids() -> None:
    analytics = _analytics(_TREND)                 # SAME result both runs (lineage fixed)
    engine = FakeEngine(analytics, analytics)
    runner = _runner(engine)
    first = await _run(runner, "trend_following")
    second = await _run(runner, "trend_following")
    assert isinstance(first, Signal) and isinstance(second, Signal)
    volatile = {"signal_id", "trace_id", "correlation_id", "timestamp"}
    assert first.model_dump(exclude=volatile) == second.model_dump(exclude=volatile)


@pytest.mark.parametrize("user_id", ["42", None])
async def test_user_id_passthrough(user_id: str | None) -> None:
    engine = FakeEngine(_analytics(_TREND))
    runner = _runner(engine)
    signal = await _run(runner, "trend_following", user_id=user_id)
    assert isinstance(signal, Signal)
    assert signal.user_id == user_id


async def test_provenance_and_analytics_refs_exact() -> None:
    analytics = _analytics(_TREND)
    engine = FakeEngine(analytics)
    runner = _runner(engine)
    signal = await _run(runner, "trend_following")
    assert isinstance(signal, Signal)
    assert signal.analytics_refs == [analytics.correlation_id]
    assert signal.provenance is not None
    assert signal.provenance.producer == "m4.alpha/trend_following::ema_trend@2.1"
    assert signal.provenance.analytics_ids == [analytics.correlation_id]
    assert signal.provenance.market_watermark == 7
    assert signal.provenance.signal_strength == 1.0
    assert signal.explainability is None                 # honest: no weight model yet


async def test_runner_unknown_strategy_raises() -> None:
    engine = FakeEngine(_analytics(_TREND))
    runner = _runner(engine)
    with pytest.raises(UnknownStrategyError):
        await _run(runner, "ghost")


# ---- E15: shadow mode -------------------------------------------------------------

def test_apply_shadow_matrix() -> None:
    signal = Signal(
        signal_id=uuid4(), strategy_id="trend_following::ema_trend", version="2.1",
        symbol="EURUSD", broker="deriv", direction="long", strength=1.0,
        timeframe="1m", valid_until=_TS + timedelta(seconds=60),
    )
    key = "trend_following::ema_trend@2.1"
    prod_unlisted = apply_shadow(signal, env="prod", allowlist=frozenset())
    assert prod_unlisted.shadow is True                              # fail-closed
    prod_listed = apply_shadow(signal, env="prod", allowlist=frozenset({key}))
    assert prod_listed.shadow is False
    paper = apply_shadow(signal, env="paper", allowlist=frozenset())
    assert paper.shadow is False
    with pytest.raises(ValueError):
        apply_shadow(signal, env="live", allowlist=frozenset())


def test_shadow_allowlist_loader_fail_closed(tmp_path: Path) -> None:
    assert load_shadow_allowlist(tmp_path) == frozenset()            # missing file
    (tmp_path / "shadow_allowlist.yaml").write_text(
        'prod:\n  - "trend_following::ema_trend@2.1"\n', encoding="utf-8")
    assert load_shadow_allowlist(tmp_path) == frozenset({"trend_following::ema_trend@2.1"})


async def test_runner_tags_shadow_in_prod_but_still_returns() -> None:
    engine = FakeEngine(_analytics(_TREND), _analytics(_TREND))
    runner = _runner(engine, allowlist=frozenset())      # prod allowlist empty
    prod = await _run(runner, "trend_following", env="prod")
    assert isinstance(prod, Signal)
    assert prod.shadow is True                           # returned, not dropped
    paper = await _run(runner, "trend_following", env="paper")
    assert isinstance(paper, Signal) and paper.shadow is False


# ---- B-group: adversarial ----------------------------------------------------------

@pytest.mark.parametrize("ret", ["2.0", "None", "'x'"])
async def test_evaluate_contract_breach_raises(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch,
                                               ret: str) -> None:
    # NAME==stem: fake file must be trend_following so the capability gate passes;
    # slots must match FakeEngine's canned features so mapping reaches evaluate.
    package = _write_pkg(tmp_path, monkeypatch, {
        "trend_following": _module(
            name="trend_following",
            slots='{"fast": ("ema_20", {}), "slow": ("sma_20", {}), "rsi": ("rsi_14", {})}',
            body=f"return {ret}",
        ),
    })
    runner = _runner(FakeEngine(_analytics(_TREND)), package=package)
    with pytest.raises(StrategyContractError):
        await _run(runner, "trend_following")


async def test_engine_features_none_raises() -> None:
    broken = _analytics({}).model_copy(update={"features": None})
    runner = _runner(FakeEngine(broken))
    with pytest.raises(ValueError):
        await _run(runner, "trend_following")


async def test_extra_feature_keys_ignored() -> None:
    junk = {**_TREND, "hostile": 999.0}
    engine = FakeEngine(_analytics(junk))
    runner = _runner(engine)
    signal = await _run(runner, "trend_following")
    assert isinstance(signal, Signal) and signal.strength == 1.0


async def test_invalid_env_and_broker_rejected_before_io() -> None:
    engine = FakeEngine(_analytics(_TREND), _analytics(_TREND))
    runner = _runner(engine)
    with pytest.raises(ValueError):
        await _run(runner, "trend_following", env="live")
    with pytest.raises(ValueError):
        await _run(runner, "trend_following", broker="")
    assert engine.requests == []                   # validation precedes any I/O


async def test_failure_does_not_poison_next_run() -> None:
    broken = _analytics({}).model_copy(update={"features": None})
    engine = FakeEngine(broken, _analytics(_TREND))
    runner = _runner(engine)
    with pytest.raises(ValueError):
        await _run(runner, "trend_following")            # B3-style failure first
    assert isinstance(
        await _run(runner, "trend_following"), Signal)    # clean run after
