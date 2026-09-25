"""
P17 / M3.2 Analytics compute files — Spec tests (WRITTEN BEFORE CODE).
WHY: AGENTS.md §2 Step 3 — tests first against docs/specs/m3.2_analytics.spec.md
(spec LGTM'd by human 2026-09-25: "lgtm to put the respective now"; pins REV4).

Direct impl import ONLY — no importorskip. Collection RED until Step 5
(honest "not implemented"; house style per test_m3.0/m3.1); after impl,
100% pass, 0 skips (§2 Step 6).

Covers §Test plan items 1-24: RSI Wilder goldens/branches/boundaries, EMA
first-value seed goldens, tick_rate exact means, REQUIREMENTS pins, Isolation
Law signature, P17 discovery batch lock, engine e2e on real forex profile.
Adversarial (§Adversarial): inf/nan period, period -5, params=None, +inf close,
hostile unknown keys.
Money: none (indicator floats, not settlement) — hand-calc comments on golden
tests per §2 Step 3; exact == on dyadic/branch values, approx only where binary
representation demands it (100 - 100/7).

Declares the m3.1 spec §Amendment: test_m3.1_spec.py:289 now proves the empty
package via a SYNTHETIC monkeypatched scan (fix (a) — zero prod change).
"""
from __future__ import annotations

import inspect
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

# Direct impl import — fails collection until Step 5 (honest RED).
from modules.m3_analytics.analytics import ema_20, rsi_14, tick_rate
from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.analytics_registry import (
    AnalyticsRegistry,
    DataRequirement,
)
from modules.m3_analytics.engine.feature_engine import (
    FeatureEngine,
    FeatureRequest,
)
from modules.m3_analytics.engine.profiles import Profiles
from shared.contracts.market_data import Candle, CapabilityStatus

_ROOT = Path(__file__).resolve().parents[2]
_CAP_YAML = _ROOT / "config" / "capabilities.yaml"
_ASSETS_DIR = _ROOT / "config" / "assets"

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_ASCENDING_15 = [10.0 + i for i in range(15)]  # every change a gain -> avg_loss=0


def _series(
    closes: list[float],
    *,
    tick_counts: list[int] | None = None,
    interval: str = "5m",
    end: datetime = _T0,
) -> list[Candle]:
    """Candle list with given closes (+optional tick_counts), newest ending at `end`."""
    n = len(closes)
    out = []
    for i, close in enumerate(closes):
        start = end - timedelta(minutes=5 * (n - i))
        out.append(
            Candle(
                open=close,
                high=close,
                low=close,
                close=close,
                tick_count=tick_counts[i] if tick_counts is not None else 1,
                interval=interval,
                interval_start=start,
                interval_end=start + timedelta(minutes=5),
            )
        )
    return out


class _StaticProvider:
    """§6a.4-shaped provider returning a fixed series (e2e fixture)."""

    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    async def get_candles(
        self,
        instrument_id: str,
        timeframe: str,
        window_start: datetime,
        window_end: datetime,
        as_of: datetime,
        broker: str,
    ) -> list[Candle]:
        return list(self._candles)


def _engine(provider: _StaticProvider) -> FeatureEngine:
    # registry=None -> D4 discovery (default registry: from_package() finds the files)
    return FeatureEngine(
        provider=provider,
        capability_registry=CapabilityRegistry.from_yaml(_CAP_YAML),
        registry=None,
        resolver=None,
        cache=None,
        profiles=Profiles.from_dir(_ASSETS_DIR),
    )


def _request(features: tuple[str, ...]) -> FeatureRequest:
    return FeatureRequest(
        instrument_id="FX.EURUSD",
        broker="deriv",
        timeframe="5m",
        features=features,
        as_of=_T0,
        asset_class="forex",
    )


# --- 1-4: RSI golden + branch values (exact where dyadic) ---


def test_rsi_golden_wilder_recursive_last_value():
    # Hand calc (spec §Test plan 1), p=2, changes +1, -0.5, 0, +0.5:
    # seed: avg_gain=(1+0)/2=0.5, avg_loss=(0+0.5)/2=0.25 -> RS=2 -> 66.66...
    # step3: g=(0.5*1+0)/2=0.25, l=(0.25*1+0)/2=0.125 -> RS=2
    # step4: g=(0.25*1+0.5)/2=0.375, l=(0.125*1+0)/2=0.0625 -> RS=6
    # RSI = 100 - 100/(1+6) = 100 - 100/7 = 85.71428571428571...
    closes = [10.0, 11.0, 10.5, 10.5, 11.0]
    value = rsi_14.compute(_series(closes), {"period": 2.0})
    assert value == pytest.approx(85.71428571428571, abs=1e-12)


def test_rsi_flat_prices_exactly_100():
    # Hand calc: changes 0,0,0 -> avg_gain=0, avg_loss=0 -> RS=0/0 undefined ->
    # project canonical rule (REV4 pins): the avg_loss==0 BRANCH is the spec -> 100.0
    assert rsi_14.compute(_series([5.0] * 4), {"period": 2.0}) == 100.0


def test_rsi_avg_loss_zero_with_gains_exactly_100():
    # Hand calc: changes +1,+1 -> avg_gain=1, avg_loss=0 -> branch -> 100.0 (no epsilon)
    assert rsi_14.compute(_series([10.0, 11.0, 12.0]), {"period": 2.0}) == 100.0


def test_rsi_all_losses_exactly_0():
    # Hand calc: changes -1,-1 -> avg_gain=0, avg_loss=1 -> branch -> 0.0 (no epsilon)
    assert rsi_14.compute(_series([10.0, 9.0, 8.0]), {"period": 2.0}) == 0.0


# --- 5-6: RSI seed boundary + dynamic override ---


def test_rsi_seed_boundary_n_equals_period_is_nan_n_plus_one_computes():
    # 14 closes @ p14: seed needs p changes = 15 closes -> n<=p -> nan (honest refusal)
    at_boundary = rsi_14.compute(_series(_ASCENDING_15[:14]), {"period": 14.0})
    assert math.isnan(at_boundary)
    # 15 closes = pinned mathematical minimum (REV4: 14 changes need 15 closes)
    just_over = rsi_14.compute(_series(_ASCENDING_15), {"period": 14.0})
    assert just_over == 100.0  # all gains -> avg_loss==0 branch


def test_rsi_dynamic_period_100_on_15_bars_is_nan():
    # param_overrides period=100 > static minimum_history 15 reaches compute with a
    # series smaller than the period -> nan -> engine invalid:<f>, never a partial
    # number (§Open flag: planner uses static REQUIREMENTS; compute refuses honestly)
    value = rsi_14.compute(_series(_ASCENDING_15), {"period": 100.0})
    assert math.isnan(value)


# --- 7-11: RSI validation, adversarial, non-finite, range ---


def test_rsi_period_below_2_valueerror():
    series = _series([10.0, 11.0, 12.0])
    for bad in (1.0, -5.0, 0.0):
        with pytest.raises(ValueError):
            rsi_14.compute(series, {"period": bad})


def test_rsi_non_integral_or_nonfinite_period_valueerror():
    # adversarial: inf must NOT reach int(inf) (OverflowError class) — isfinite first
    series = _series(_ASCENDING_15)
    for bad in (14.5, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            rsi_14.compute(series, {"period": bad})


def test_rsi_empty_candles_valueerror():
    with pytest.raises(ValueError):
        rsi_14.compute([], {"period": 14.0})


def test_rsi_nonfinite_close_returns_nan():
    # adversarial: nan and +inf bars -> NaN sentinel, never raises, never inf result
    nan_series = _series([10.0, 11.0, float("nan"), 12.0])
    assert math.isnan(rsi_14.compute(nan_series, {"period": 2.0}))
    inf_series = _series([10.0, 11.0, float("inf"), 12.0])
    assert math.isnan(rsi_14.compute(inf_series, {"period": 2.0}))


def test_rsi_result_bounded_0_to_100():
    closes = [
        10.0, 12.0, 11.0, 13.0, 12.0, 14.0, 13.0, 15.0,
        14.0, 16.0, 15.0, 17.0, 16.0, 18.0, 17.0,
    ]
    value = rsi_14.compute(_series(closes), {"period": 14.0})
    assert math.isfinite(value)
    assert 0.0 <= value <= 100.0


# --- 12-15: EMA seed + recursion, validation ---


def test_ema_golden_seed_and_recursion():
    # Hand calc (p=3, alpha=2/4=0.5): seed=2 -> 0.5*4+0.5*2=3.0
    # -> 0.5*6+0.5*3=4.5 -> 0.5*8+0.5*4.5=6.25 (dyadic -> exact ==)
    assert ema_20.compute(_series([2.0, 4.0, 6.0, 8.0]), {"period": 3.0}) == 6.25
    # Hand calc: [2,4] -> 0.5*4+0.5*2=3.0; seed-only [2] -> 2.0 (first-value seed)
    assert ema_20.compute(_series([2.0, 4.0]), {"period": 3.0}) == 3.0
    assert ema_20.compute(_series([2.0]), {"period": 3.0}) == 2.0


def test_ema_empty_candles_valueerror():
    with pytest.raises(ValueError):
        ema_20.compute([], {"period": 20.0})


def test_ema_period_invalid_valueerror():
    # adversarial incl. non-integral: 0 and -1 out of bounds (>=1), 2.5 not integral
    series = _series([2.0, 4.0, 6.0, 8.0])
    for bad in (0.0, -1.0, 2.5):
        with pytest.raises(ValueError):
            ema_20.compute(series, {"period": bad})


def test_ema_nonfinite_close_returns_nan():
    series = _series([2.0, float("nan"), 4.0])
    assert math.isnan(ema_20.compute(series, {"period": 3.0}))


# --- 16-18: tick_rate exact means ---


def test_tick_rate_mean_exact():
    # Hand calc: (8 + 4 + 12) / 3 = 24/3 = 8.0
    candles = _series([1.0, 1.0, 1.0], tick_counts=[8, 4, 12])
    assert tick_rate.compute(candles, {}) == 8.0


def test_tick_rate_zero_ticks_returns_zero():
    candles = _series([1.0, 1.0, 1.0], tick_counts=[0, 0, 0])
    assert tick_rate.compute(candles, {}) == 0.0


def test_tick_rate_empty_candles_valueerror():
    with pytest.raises(ValueError):
        tick_rate.compute([], {})


# --- 19, 24: params contract ---


def test_params_none_valueerror_and_empty_uses_defaults():
    series = _series(_ASCENDING_15)
    with pytest.raises(ValueError):
        rsi_14.compute(series, None)
    # defaults path: params={} still works (mirrors REQUIREMENTS.defaults fallback)
    assert rsi_14.compute(series, {}) == 100.0


def test_unknown_param_keys_ignored_defaults_still_apply():
    # forward-compat: unknown keys are ignored, effective period still 14
    value = rsi_14.compute(_series(_ASCENDING_15), {"period": 14.0, "junk": 1.0})
    assert value == 100.0


# --- 20: REQUIREMENTS pinned per spec (frozen + extra=forbid) ---


def test_requirements_pinned_per_spec():
    expected = {
        "rsi_14": (("price",), 15, 0, {"period": 14.0}),
        "ema_20": (("price",), 20, 5, {"period": 20.0}),
        "tick_rate": (("tick_count",), 1, 0, {}),
    }
    for module in (rsi_14, ema_20, tick_rate):
        req = module.REQUIREMENTS
        assert isinstance(req, DataRequirement)
        needs, minimum, warmup, defaults = expected[module.NAME]
        assert req.needs == needs
        assert req.minimum_history == minimum
        assert req.warmup == warmup
        assert req.defaults == defaults
        assert req.dependencies == ()
        assert req.provides_regime is False
        assert req.history_duration is None
        # frozen: field assignment raises; extra=forbid: unknown key raises
        with pytest.raises((ValidationError, AttributeError, TypeError)):
            req.minimum_history = 999
        with pytest.raises(ValidationError):
            DataRequirement.model_validate({"needs": ("price",), "smuggled_key": 1.0})


# --- 21-22: Isolation Law signature + P17 discovery batch lock ---


def test_compute_signature_isolation_contract():
    registry = AnalyticsRegistry()
    for module in (rsi_14, ema_20, tick_rate):
        registry.register(module)  # raises ValueError if any contract leg breaks
        assert list(inspect.signature(module.compute).parameters) == ["candles", "params"]
        for smuggled in ("provider", "clock", "as_of"):
            assert not hasattr(module, smuggled)
    assert sorted(registry.names()) == ["ema_20", "rsi_14", "tick_rate"]


def test_from_package_discovers_p17_batch():
    # positive half of the m3.1 :289 amendment (its synthetic-empty half lives there);
    # P17+P18 batch lock — declared amendment #1 in m3.2_p18 spec (3 -> 15 names).
    names = AnalyticsRegistry.from_package().names()
    assert sorted(names) == [
        "adx_14", "atr_14", "bb_percent", "choppiness", "donchian_20", "ema_20",
        "hurst", "macd", "returns", "rsi_14", "sma_20", "std_20", "tick_rate",
        "vol_regime", "zscore_20",
    ]


# --- 23: engine end-to-end (default registry + real forex profile) ---


async def test_engine_end_to_end_real_files_real_profile():
    closes = [10.0 + i for i in range(15)]  # ascending -> every change a gain
    candles = _series(closes, tick_counts=[10] * 15)
    engine = _engine(_StaticProvider(candles))  # registry=None -> D4 discovery
    result = await engine.get_features(_request(("rsi_14", "tick_rate")))
    assert result.features["rsi_14"] == 100.0  # avg_loss==0 branch, exact
    assert result.features["tick_rate"] == 10.0  # mean of fifteen 10s
    assert result.capability_status is CapabilityStatus.PROXY_LIMITED
    assert result.capability_warning is not None
    assert "tick_count \u2260 volume" in result.capability_warning
