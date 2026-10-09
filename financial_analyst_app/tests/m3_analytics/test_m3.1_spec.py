"""
P16 / M3.1 Feature Engine â€” orchestration â€” Spec tests (WRITTEN BEFORE CODE).
WHY: AGENTS.md Â§2 Step 3 â€” tests first against docs/specs/m3.1_feature_engine.spec.md
REV3 (ACTION 36 change list applied 2026-09-25).

Direct impl import ONLY â€” no importorskip. Collection RED until Step 5
(honest "not implemented"; house style per test_m3.0_spec.py); after impl,
100% pass, 0 skips (Â§2 Step 6).

Covers REV3 Â§Test plan (36 named) + 2 edge-coverers (profile-missing-at-load,
commodity profile map) = 38. Contract tests import ONLY frozen AnalyticsResult
path via engine output â€” no shared contract change (no bump).
No money path -> no hand-calc (spec Â§Test plan "Money: none").

ACTION 36 amendments (now spec'd in REV3 Â§Contracts/Â§Edges):
  M3.1-a: provides_regime module must expose REGIME_LABEL(value)->str
          (engine sets result.regime = REGIME_LABEL(computed_value)).
  M3.1-b: registry.register validates compute signature == (candles, params)
          at register time (Isolation Law enforcement, spec Â§Contracts).
  M3.1-c: Profiles maps asset_class "commodity" -> commodities.yaml.
  36-REV3: planner dependency union; PIT-vs-depth (as_of != depth);
          Â§6a.4 broker parity; completion guard; calendar-TF ban;
          partial history never silently truncated.
"""
from __future__ import annotations

import importlib
import inspect
import json
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from modules.m2_market_data import candles as m2_candles
from modules.m3_analytics.capability.capability_registry import CapabilityRegistry

# Direct impl import â€” fails collection until Step 5 (honest RED).
from modules.m3_analytics.engine.analytics_registry import (
    AnalyticsRegistry,
    DataRequirement,
)
from modules.m3_analytics.engine.errors import FeatureEngineDataError
from modules.m3_analytics.engine.feature_engine import (
    FeatureEngine,
    FeatureRequest,
)
from modules.m3_analytics.engine.profiles import Profiles
from shared.contracts.market_data import Candle, CapabilityStatus
from shared.timeframes import INTERVAL_MINUTES

_ROOT = Path(__file__).resolve().parents[2]
_CAP_YAML = _ROOT / "config" / "capabilities.yaml"
_ASSETS_DIR = _ROOT / "config" / "assets"

_UTC = UTC
_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=_UTC)


# --- fixtures: providers / cache / resolver (structural, no imports of impl) ---


class FakeProvider:
    """Records every get_candles call (Â§6a.4 port shape)."""

    def __init__(self, candles: list[Candle] | None = None, error: Exception | None = None):
        self.candles = candles or []
        self.error = error
        self.calls: list[dict] = []

    async def get_candles(
        self,
        instrument_id: str,
        timeframe: str,
        window_start: datetime,
        window_end: datetime,
        as_of: datetime,
        broker: str,
    ) -> list[Candle]:
        self.calls.append(
            {
                "instrument_id": instrument_id,
                "timeframe": timeframe,
                "window_start": window_start,
                "window_end": window_end,
                "as_of": as_of,
                "broker": broker,  # Â§6a.4 parity â€” REV3 restored the arg
            }
        )
        if self.error is not None:
            raise self.error
        return list(self.candles)


class FakeCache:
    """Records keys + TTLs; serves/records JSON payloads."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.gets: list[str] = []
        self.sets: list[tuple[str, int, str]] = []

    async def get(self, key: str) -> str | None:
        self.gets.append(key)
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.sets.append((key, ttl, value))
        self.store[key] = value


class FailingCache:
    """Dead-cache semantics: every op raises -> engine must fail-open (spec step 9)."""

    async def get(self, key: str) -> str:
        raise RuntimeError("cache down")

    async def setex(self, key: str, ttl: int, value: str) -> None:
        raise RuntimeError("cache down")


class FakeResolver:
    def __init__(self, value: str | None) -> None:
        self.value = value
        self.calls = 0

    def resolve(self, request: FeatureRequest) -> str | None:
        self.calls += 1
        return self.value


def _candles(n: int, *, interval: str = "5m", start: datetime = _T0) -> list[Candle]:
    out = []
    for i in range(n):
        s = start - timedelta(minutes=5 * (n - i))
        out.append(
            Candle(
                open=1.1,
                high=1.2,
                low=1.0,
                close=1.15,
                interval=interval,
                interval_start=s,
                interval_end=s + timedelta(minutes=5),
            )
        )
    return out


class _FixtureAnalytic:
    """D4 fixture analytic â€” exposes exactly NAME / REQUIREMENTS / compute (spec Â§Contracts)."""

    NAME: str
    REQUIREMENTS: DataRequirement
    compute: Callable[..., float]
    REGIME_LABEL: Callable[[float], str] | None = None

    def __init__(
        self,
        name: str,
        requirements: DataRequirement,
        compute: Callable[..., float],
        regime_label: Callable[[float], str] | None = None,
    ) -> None:
        self.NAME = name
        self.REQUIREMENTS = requirements
        self.compute = compute
        self.REGIME_LABEL = regime_label


def _analytic(
    name: str,
    *,
    min_history: int = 10,
    warmup: int = 0,
    defaults: dict | None = None,
    deps: tuple[str, ...] = (),
    history_duration: str | None = None,
    value: float | Callable[..., float] = 42.0,
    provides_regime: bool = False,
    regime_label: Callable[[float], str] | None = None,
    spy: list | None = None,
) -> _FixtureAnalytic:
    requirements = DataRequirement(
        minimum_history=min_history,
        warmup=warmup,
        dependencies=deps,
        defaults=dict(defaults or {}),
        history_duration=history_duration,
        provides_regime=provides_regime,
    )

    def compute(candles, params) -> float:
        if spy is not None:
            spy.append(name)
        if callable(value):
            return value(candles, params)
        return value

    return _FixtureAnalytic(name, requirements, compute, regime_label)


def _cap_registry() -> CapabilityRegistry:
    return CapabilityRegistry.from_yaml(_CAP_YAML)


def _profiles() -> Profiles:
    return Profiles.from_dir(_ASSETS_DIR)


def _engine(
    *,
    provider: FakeProvider | None = None,
    registry: AnalyticsRegistry | None = None,
    resolver=None,
    cache=None,
    cap_registry: CapabilityRegistry | None = None,
    profiles: Profiles | None = None,
) -> FeatureEngine:
    return FeatureEngine(
        provider=provider if provider is not None else FakeProvider(_candles(12)),
        capability_registry=cap_registry if cap_registry is not None else _cap_registry(),
        registry=registry,
        resolver=resolver,
        cache=cache,
        profiles=profiles if profiles is not None else _profiles(),
    )


def _request(
    *,
    features: tuple[str, ...] = ("rsi_14",),
    timeframe: str | None = "5m",
    broker: str = "deriv",
    asset_class: str = "forex",
    as_of: datetime = _T0,
    overrides: dict | None = None,
) -> FeatureRequest:
    return FeatureRequest(
        instrument_id="FX.EURUSD",
        broker=broker,
        timeframe=timeframe,
        features=tuple(features),
        as_of=as_of,
        asset_class=asset_class,
        param_overrides=overrides,
    )


# --- 1-3: request validation (spec Â§Edges) ---


async def test_request_validated_timeframe_unknown_rejected():
    engine = _engine()
    with pytest.raises(ValidationError):
        await engine.get_features(_request(timeframe="7m"))


def test_naive_as_of_rejected():
    with pytest.raises(ValidationError):
        _request(as_of=datetime(2026, 1, 1, 12, 0))  # noqa: DTZ001 â€” WHY naive: the rejection case itself


def test_empty_features_rejected():
    with pytest.raises(ValidationError):
        _request(features=())


# --- 4-7: capability interlock (M3.0 P15) ---


async def test_capability_gate_drops_unavailable_and_keeps_rest():
    # order_book_imbalance = UNAVAILABLE@deriv (real config/capabilities.yaml)
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14"))
    engine = _engine(provider=FakeProvider(_candles(12)), registry=reg)
    res = await engine.get_features(_request(features=("rsi_14", "order_book_imbalance")))
    assert res.features == {"rsi_14": 42.0}
    assert res.capability_status is CapabilityStatus.FULLY_SUPPORTED
    assert res.capability_warning is not None
    assert "order_book_imbalance UNAVAILABLE" in res.capability_warning
    assert "Deriv has_depth:false" in res.capability_warning


async def test_all_unavailable_returns_empty_unavailable_no_raise(
    monkeypatch: pytest.MonkeyPatch,
):
    # D4 compliance proof: a scan of an EMPTY analytics/ package = empty, not crash.
    # DECLARED AMENDMENT (m3.2 spec Â§Amendment, spec LGTM 2026-09-25): the former
    # literal `from_package().names() == ()` broke the moment P17 compute files
    # landed â€” fix (a) monkeypatches a SYNTHETIC empty package into the scan
    # (zero production change; from_package() signature stays frozen).
    real_import = importlib.import_module

    def _synthetic_empty(name: str, *args, **kwargs):
        if name == "modules.m3_analytics.analytics":
            return SimpleNamespace(__path__=[], __name__=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", _synthetic_empty)
    assert AnalyticsRegistry.from_package().names() == ()
    engine = _engine(provider=FakeProvider(_candles(12)), registry=None)
    res = await engine.get_features(
        _request(features=("order_book_imbalance", "totally_unknown_feature"))
    )
    assert res.features == {}
    assert res.capability_status is CapabilityStatus.UNAVAILABLE
    assert res.capability_warning is not None
    assert "order_book_imbalance UNAVAILABLE" in res.capability_warning
    assert "unknown_feature" in res.capability_warning


async def test_proxy_feature_included_with_warning(tmp_path):
    """The PROXY_LIMITED MECHANISM, tested against a capability config of its own.

    This test used to read the shipped `config/capabilities.yaml` and borrow the real
    `tick_rate` entry (PROXY_LIMITED, warning "tick_count != volume") as its example. That
    coupled a unit test to config CONTENT, so deleting that entry as part of the Candle 2.0.0
    migration broke the mechanism test for no reason. A synthetic PROXY_LIMITED feature now
    proves the same behaviour, and this test keeps proving it regardless of which real
    features are proxies - or whether any are.
    """
    cap_yaml = tmp_path / "capabilities.yaml"
    cap_yaml.write_text(
        "version: 1\n"
        "features:\n"
        "  synthetic_proxy:\n"
        "    status: PROXY_LIMITED\n"
        "    method: derived_from_something\n"
        "    warning: 'synthetic_proxy is a stand-in'\n"
        "    requires: [price]\n",
        encoding="utf-8",
    )
    reg = AnalyticsRegistry()
    reg.register(_analytic("synthetic_proxy"))
    engine = _engine(
        provider=FakeProvider(_candles(12)),
        registry=reg,
        cap_registry=CapabilityRegistry.from_yaml(cap_yaml),
    )
    res = await engine.get_features(_request(features=("synthetic_proxy",)))
    assert res.features == {"synthetic_proxy": 42.0}
    assert res.capability_status is CapabilityStatus.PROXY_LIMITED
    assert res.capability_warning is not None
    assert "synthetic_proxy is a stand-in" in res.capability_warning
    # PROXY_LIMITED is a WARNING, not a refusal - the value is still returned.
    # UNAVAILABLE (asserted in the test above) is the status that drops the feature.
    assert res.features["synthetic_proxy"] == 42.0


async def test_unknown_analytic_dropped_with_warning():
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14"))
    engine = _engine(provider=FakeProvider(_candles(12)), registry=reg)
    res = await engine.get_features(_request(features=("rsi_14", "wibble_99")))
    assert res.features == {"rsi_14": 42.0}
    assert res.capability_warning is not None
    assert "unknown_feature" in res.capability_warning


# --- 8: dependency order (spec flow step 7) ---


async def test_dependency_computed_before_dependent():
    spy: list[str] = []
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14", spy=spy, value=7.0))
    reg.register(_analytic("ema_20", deps=("rsi_14",), spy=spy, value=42.0))
    engine = _engine(provider=FakeProvider(_candles(12)), registry=reg)
    res = await engine.get_features(_request(features=("ema_20",)))
    assert spy == ["rsi_14", "ema_20"]  # dependency first, once each
    assert res.features == {"ema_20": 42.0}  # dep value computed but NOT returned


# --- 9-10: Â§8b quality gates ---


async def test_insufficient_history_omitted_with_warning():
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14", min_history=10))
    engine = _engine(provider=FakeProvider([]), registry=reg)
    res = await engine.get_features(_request())
    assert res.features == {}
    assert res.capability_warning is not None
    assert "INSUFFICIENT:rsi_14" in res.capability_warning
    # gate passed (rsi_14 FULLY_SUPPORTED) -> status stays FULLY_SUPPORTED, data thin
    assert res.capability_status is CapabilityStatus.FULLY_SUPPORTED


async def test_warmup_history_computed_with_flag():
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14", min_history=10, warmup=5))  # window [10, 15)
    engine = _engine(provider=FakeProvider(_candles(12)), registry=reg)
    res = await engine.get_features(_request())
    assert res.features == {"rsi_14": 42.0}  # computed despite incomplete warmup
    assert res.capability_warning is not None
    assert "WARMUP_INCOMPLETE:rsi_14" in res.capability_warning


# --- 11: param precedence (spec Â§Scope: overrides > profile > defaults) ---


async def test_param_precedence_overrides_beat_profile_beat_defaults():
    # real forex.yaml analytics section (Phase C): rsi_14 {period: 14}
    reg = AnalyticsRegistry()
    reg.register(
        _analytic(
            "rsi_14",
            defaults={"period": 10},
            value=lambda candles, params: float(params["period"]),
        )
    )
    reg.register(
        _analytic(
            "ema_20",
            defaults={"period": 5},
            value=lambda candles, params: float(params["period"]),
        )
    )
    engine = _engine(provider=FakeProvider(_candles(12)), registry=reg)

    res_profile = await engine.get_features(_request(features=("rsi_14",)))
    assert res_profile.features == {"rsi_14": 14.0}  # profile (14) beats default (10)

    res_over = await engine.get_features(
        _request(features=("rsi_14",), overrides={"rsi_14": {"period": 99}})
    )
    assert res_over.features == {"rsi_14": 99.0}  # request override beats profile

    res_default = await engine.get_features(_request(features=("ema_20",)))
    assert res_default.features == {"ema_20": 5.0}  # no profile entry -> default


# --- 12-15: Valkey cache (feat: namespace, TTL 60, fail-open) ---


async def test_cache_miss_then_hit_same_as_of():
    fk = FakeCache()
    provider = FakeProvider(_candles(12))
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14"))
    engine = _engine(provider=provider, registry=reg, cache=fk)

    res1 = await engine.get_features(_request())
    res2 = await engine.get_features(_request())

    assert res1.features == {"rsi_14": 42.0}
    assert res2 == res1  # JSON rebuild is byte-faithful (same ids/timestamp/values)
    assert provider.calls.__len__() == 1  # hit path never re-fetches
    assert len(fk.gets) == 2
    assert len(fk.sets) == 1
    key, ttl, _payload = fk.sets[0]
    assert key.startswith("feat:")
    assert ttl == 60  # spec TTL 60s


async def test_cache_key_distinct_per_as_of():
    fk = FakeCache()
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14"))
    engine = _engine(provider=FakeProvider(_candles(12)), registry=reg, cache=fk)

    await engine.get_features(_request(as_of=_T0))
    await engine.get_features(_request(as_of=_T0 + timedelta(minutes=5)))

    keys = {k for k, _ttl, _v in fk.sets}
    assert len(keys) == 2  # no cross-window bleed


async def test_cache_key_never_contains_user_id():
    fk = FakeCache()
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14"))
    engine = _engine(provider=FakeProvider(_candles(12)), registry=reg, cache=fk)

    res = await engine.get_features(_request())
    for key, _ttl, _v in fk.sets:
        assert "user" not in key.lower()
        assert key.startswith("feat:")
    dumped = json.dumps(res.model_dump(mode="json")).lower()
    assert "user" not in dumped  # tenant law: request has none, result carries none


async def test_cache_down_fails_open_recomputes():
    provider = FakeProvider(_candles(12))
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14"))
    engine = _engine(provider=provider, registry=reg, cache=FailingCache())

    res1 = await engine.get_features(_request())
    res2 = await engine.get_features(_request())

    assert res1.features == {"rsi_14": 42.0}
    assert res2.features == {"rsi_14": 42.0}
    assert provider.calls.__len__() == 2  # both requests recomputed, neither raised


# --- 16-17: typed data path + NaN/inf rejection ---


async def test_provider_error_typed_not_empty():
    original = RuntimeError("provider exploded")
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14"))
    engine = _engine(provider=FakeProvider(error=original), registry=reg)

    with pytest.raises(FeatureEngineDataError) as excinfo:
        await engine.get_features(_request())
    assert excinfo.value.__cause__ is original  # cause preserved, never empty-success


async def test_nan_compute_value_rejected_not_cached():
    fk = FakeCache()
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14", value=float("nan")))
    engine = _engine(provider=FakeProvider(_candles(12)), registry=reg, cache=fk)

    res = await engine.get_features(_request())

    assert "rsi_14" not in res.features  # NaN omitted from features
    assert res.capability_warning is not None
    assert "invalid:rsi_14" in res.capability_warning
    assert len(fk.sets) == 1  # sanitized result IS cached (spec: never cache the NaN)
    _key, _ttl, stored = fk.sets[0]
    assert "NaN" not in stored and "Infinity" not in stored
    assert "rsi_14" not in json.loads(stored)["features"]


# --- 18-19: registry load-time validation ---


def test_register_duplicate_name_valueerror():
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14"))
    with pytest.raises(ValueError, match="duplicate"):
        reg.register(_analytic("rsi_14"))


def test_register_cycle_valueerror():
    reg = AnalyticsRegistry()
    reg.register(_analytic("alpha_1", deps=("beta_1",)))
    with pytest.raises(ValueError, match="cycle"):
        reg.register(_analytic("beta_1", deps=("alpha_1",)))


# --- 20: regime (amendment M3.1-a: REGIME_LABEL callable) ---


async def test_regime_set_by_fixture_regime_analytic():
    def _label(value: float) -> str:
        return "trending" if value < 38.0 else "choppy"

    reg = AnalyticsRegistry()
    reg.register(
        _analytic(
            "ema_20",
            value=30.0,
            provides_regime=True,
            regime_label=_label,
        )
    )
    engine = _engine(provider=FakeProvider(_candles(12)), registry=reg)
    res = await engine.get_features(_request(features=("ema_20",)))
    assert res.regime == "trending"  # REGIME_LABEL(30.0)


# --- 21: frozen contract + symbol/timeframe/watermark pins ---


async def test_result_is_frozen_analyticsresult_lineage_watermark():
    candles = _candles(12)
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14"))
    engine = _engine(provider=FakeProvider(candles), registry=reg)

    res = await engine.get_features(_request())

    with pytest.raises(ValidationError):
        res.features = {}  # BaseContract frozen=True
    assert res.symbol == "FX.EURUSD"  # spec: symbol = instrument_id (value pin)
    assert res.broker == "deriv"
    assert res.timeframe == "5m"  # resolved timeframe stamped on output
    expected_wm = int(max(c.interval_start for c in candles).timestamp() * 1000)
    assert res.source_watermark == expected_wm  # lineage epoch-ms


# --- 22-23: adversarial ---


async def test_adversarial_hostile_feature_name_never_escapes():
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14"))
    engine = _engine(provider=FakeProvider(_candles(12)), registry=reg)
    res = await engine.get_features(
        _request(features=("rsi_14", "'; DROP TABLE features;--"))
    )
    assert res.features == {"rsi_14": 42.0}  # hostile name dropped as unknown
    assert res.capability_warning is not None
    assert "unknown_feature" in res.capability_warning


async def test_adversarial_timeframe_injection_rejected():
    engine = _engine()
    with pytest.raises(ValidationError):
        await engine.get_features(_request(timeframe="1m; rm -rf"))


# --- 24-26: Calculation Timeframe Law (Option A resolver seam) ---


async def test_timeframe_omitted_no_resolver_rejected():
    # Day-1: no resolver injected -> omitted must REJECT (no hidden default).
    engine = _engine(registry=AnalyticsRegistry())
    with pytest.raises(ValidationError):
        await engine.get_features(_request(timeframe=None))

    # Unresolvable -> REJECT even with a resolver that answers None.
    engine2 = _engine(registry=AnalyticsRegistry(), resolver=FakeResolver(None))
    with pytest.raises(ValidationError):
        await engine2.get_features(_request(timeframe=None))


async def test_resolver_supplied_timeframe_used_unchanged():
    fk = FakeCache()
    provider = FakeProvider(_candles(12))
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14"))
    resolver = FakeResolver("5m")
    engine = _engine(provider=provider, registry=reg, cache=fk, resolver=resolver)

    res = await engine.get_features(_request(timeframe=None))

    assert resolver.calls == 1
    assert provider.calls[0]["timeframe"] == "5m"  # passed unchanged to provider
    assert res.timeframe == "5m"  # stamped on result
    key = fk.sets[0][0]
    assert ":5m:" in key  # resolved value in cache key


async def test_resolver_unknown_value_registry_rejected():
    engine = _engine(registry=AnalyticsRegistry(), resolver=FakeResolver("7m"))
    with pytest.raises(ValidationError):
        await engine.get_features(_request(timeframe=None))


# --- 27-28: Historical Depth Law + wrong-series guard ---


async def test_depth_duration_3d_at_5m_requests_864_bars():
    provider = FakeProvider(_candles(12))
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14", min_history=5, warmup=0, history_duration="3d"))
    engine = _engine(provider=provider, registry=reg)

    await engine.get_features(_request(timeframe="5m"))

    call = provider.calls[0]
    span = call["window_end"] - call["window_start"]
    assert span / timedelta(minutes=5) == 864  # ceil(4320 min / 5 min) = 864
    assert call["window_end"] == _T0  # window ends at as_of
    assert call["timeframe"] == "5m"


async def test_provider_interval_mismatch_rejected():
    # Isolation Law: foreign series (1m when 5m resolved) must never be merged in.
    provider = FakeProvider(_candles(12, interval="1m"))
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14"))
    engine = _engine(provider=provider, registry=reg)
    with pytest.raises(FeatureEngineDataError):
        await engine.get_features(_request(timeframe="5m"))


# --- 29: Analytic Timeframe Isolation contract (amendment M3.1-b) ---


def test_analytic_has_no_provider_access():
    good = _analytic("rsi_14")
    sig = inspect.signature(good.compute)
    assert list(sig.parameters) == ["candles", "params"]  # exactly (candles, params)
    assert not any(hasattr(good, banned) for banned in ("provider", "clock", "as_of"))

    bad = _FixtureAnalytic(
        "bad_1",
        DataRequirement(minimum_history=1, warmup=0),
        lambda candles, params, provider: 1.0,  # smuggled I/O access
    )
    reg = AnalyticsRegistry()
    with pytest.raises(ValueError, match="signature"):
        reg.register(bad)


# --- 30: timeframe registry single-source parity (shared vs M2.4) ---


def test_tf_registry_parity_with_m2_candles():
    assert INTERVAL_MINUTES == m2_candles._INTERVAL_MINUTES  # drift-proof lock


# --- 31-32: profile loader edges (spec Â§Edges + amendment M3.1-c) ---


def test_profile_missing_at_load_fails_loud():
    # Spec edge: profile missing for asset_class -> error at config LOAD, not per request.
    with tempfile.TemporaryDirectory() as empty, pytest.raises(ValueError):
        Profiles.from_dir(Path(empty))


def test_profile_commodity_maps_to_commodities_yaml():
    # M3.1-c: asset_class vocabulary "commodity" but file commodities.yaml (known bug deferral).
    profiles = Profiles.from_dir(_ASSETS_DIR)
    assert isinstance(profiles.get("commodity"), dict)
    assert isinstance(profiles.get("forex"), dict)


# --- 33-38: ACTION 36 / REV3 â€” planner union, PIT-vs-depth, Â§6a.4, guards ---


async def test_planner_window_widens_to_dependency_requirements():
    # Planner Dependency Law: dep needing 50 bars widens the window even though
    # the dependent declares only 5 (union over graph, never top-level only).
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14", min_history=50))
    reg.register(_analytic("ema_20", min_history=5, deps=("rsi_14",)))
    provider = FakeProvider(_candles(60))
    engine = _engine(provider=provider, registry=reg)

    res = await engine.get_features(_request(features=("ema_20",), timeframe="5m"))

    call = provider.calls[0]
    span = call["window_end"] - call["window_start"]
    assert span / timedelta(minutes=5) >= 50  # union includes transitive dep depth
    assert res.features == {"ema_20": 42.0}


async def test_as_of_shift_keeps_depth_span_constant():
    # PIT-vs-Depth Law: as_of moves the window; depth span is requirement-derived.
    provider = FakeProvider(_candles(12))
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14", min_history=10, warmup=0))
    engine = _engine(provider=provider, registry=reg)

    await engine.get_features(_request(as_of=_T0))
    await engine.get_features(_request(as_of=_T0 + timedelta(hours=1)))

    call0, call1 = provider.calls
    span0 = call0["window_end"] - call0["window_start"]
    span1 = call1["window_end"] - call1["window_start"]
    assert span0 == span1  # depth never inferred from as_of
    assert call0["window_end"] == _T0
    assert call1["window_end"] == _T0 + timedelta(hours=1)  # window_end = as_of


async def test_provider_receives_broker_argument():
    # Â§6a.4 signature parity: broker is part of the port (REV3; REV2 dropped it).
    provider = FakeProvider(_candles(12))
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14"))
    engine = _engine(provider=provider, registry=reg)

    await engine.get_features(_request(broker="deriv"))

    assert provider.calls[0]["broker"] == "deriv"


async def test_provider_completion_beyond_window_rejected():
    # Completion guard: candle completing AFTER window_end = forming-candle leak.
    candles = _candles(12)
    candles[-1] = candles[-1].model_copy(
        update={"interval_end": candles[-1].interval_end + timedelta(minutes=5)}
    )
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14"))
    engine = _engine(provider=FakeProvider(candles), registry=reg)

    with pytest.raises(FeatureEngineDataError):
        await engine.get_features(_request(timeframe="5m"))


async def test_calendar_timeframe_1w_rejected():
    # Calendar buckets are NOT in the 9-string fixed-duration registry.
    engine = _engine()
    for tf in ("1w", "1mo", "1y"):
        with pytest.raises(ValidationError):
            await engine.get_features(_request(timeframe=tf))


async def test_partial_history_never_silently_truncated():
    # Demand-Driven Depth Law: available < required -> INSUFFICIENT, compute never runs.
    spy: list[str] = []
    reg = AnalyticsRegistry()
    reg.register(_analytic("rsi_14", min_history=10, spy=spy))
    engine = _engine(provider=FakeProvider(_candles(3)), registry=reg)

    res = await engine.get_features(_request())

    assert res.features == {}
    assert spy == []  # never computed on insufficient data
    assert res.capability_warning is not None
    assert "INSUFFICIENT:rsi_14" in res.capability_warning
