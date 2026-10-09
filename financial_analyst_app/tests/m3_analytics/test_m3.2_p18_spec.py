"""
P18 / M3.2 remaining 12 analytics — Spec tests (WRITTEN BEFORE CODE).
WHY: AGENTS.md §2 Step 3 — tests first against docs/specs/m3.2_p18_per_asset.spec.md
(blanket human LGTM 2026-09-25 "plesae fill the analysis and the strategies now lgtm";
REV4 pins bind verbatim).

Direct impl import ONLY — no importorskip. Collection RED until Step 5 (honest
"not implemented"; house style per test_m3.2_spec.py); after impl, 100% pass,
0 skips (§2 Step 6).

Covers spec sections: per-analytic goldens (hand calc comments), canonical zero
branches (no epsilon), min-bar boundaries (nan one below / finite at), param
validation bounds incl. log-denominator adversarials, REQUIREMENTS pin table,
Isolation signature, 15-name discovery lock (declared amendment #1), engine e2e
with real forex profile + FULLY_SUPPORTED status.
Money: none (indicator floats) — exact == on dyadic/branch values, approx only
where binary representation demands it (ln(2), macd, zscore of uniform window).
"""
from __future__ import annotations

import inspect
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

# Direct impl import — fails collection until Step 5 (honest RED).
from modules.m3_analytics.analytics import (
    adx_14,
    atr_14,
    bb_percent,
    choppiness,
    donchian_20,
    hurst,
    macd,
    returns,
    sma_20,
    std_20,
    vol_regime,
    zscore_20,
)
from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.analytics_registry import AnalyticsRegistry, DataRequirement
from modules.m3_analytics.engine.feature_engine import FeatureEngine, FeatureRequest
from modules.m3_analytics.engine.profiles import Profiles
from shared.contracts.market_data import Candle, CapabilityStatus

_ROOT = Path(__file__).resolve().parents[2]
_CAP_YAML = _ROOT / "config" / "capabilities.yaml"
_ASSETS_DIR = _ROOT / "config" / "assets"

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

_ALL = (
    sma_20, std_20, zscore_20, returns, atr_14, adx_14,
    macd, bb_percent, donchian_20, choppiness, vol_regime, hurst,
)


def _series(closes: list[float], *, interval: str = "5m", end: datetime = _T0) -> list[Candle]:
    """Candle list with h=l=c=given close (dyadic-friendly fixture), newest last."""
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
                interval=interval,
                interval_start=start,
                interval_end=start + timedelta(minutes=5),
            )
        )
    return out


def _series_ohlc(bars: list[tuple[float, float, float]], *, end: datetime = _T0) -> list[Candle]:
    """Candle list from (high, low, close) tuples — TR/DM paths need real h/l."""
    n = len(bars)
    out = []
    for i, (high, low, close) in enumerate(bars):
        start = end - timedelta(minutes=5 * (n - i))
        out.append(
            Candle(
                open=close,
                high=high,
                low=low,
                close=close,
                interval="5m",
                interval_start=start,
                interval_end=start + timedelta(minutes=5),
            )
        )
    return out


class _StaticProvider:
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    async def get_candles(self, instrument_id, timeframe, window_start, window_end, as_of, broker):
        return list(self._candles)


def _engine(provider: _StaticProvider) -> FeatureEngine:
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


# --- sma_20 ---


def test_sma_golden_mean():
    # Hand calc: (2+4+6+8)/4 = 20/4 = 5.0
    assert sma_20.compute(_series([2.0, 4.0, 6.0, 8.0]), {"period": 4.0}) == 5.0


def test_sma_boundary_nan_below_minimum_exactly_period_computes():
    assert math.isnan(sma_20.compute(_series([1.0, 2.0, 3.0]), {"period": 4.0}))
    assert sma_20.compute(_series([2.0, 4.0, 6.0, 8.0]), {"period": 4.0}) == 5.0
    # defaults path: params={} -> period 20 (REQUIREMENTS.defaults mirror)
    assert math.isnan(sma_20.compute(_series([2.0, 4.0, 6.0, 8.0]), {}))
    assert sma_20.compute(_series([float(i) for i in range(20)]), {}) == 9.5  # mean 0..19


def test_sma_flat_returns_flat_value():
    assert sma_20.compute(_series([5.0] * 20), {}) == 5.0


# --- std_20 ---


def test_std_golden_population_sigma():
    # Hand calc: [3,3,5,5] mean 4, devs -1,-1,1,1 -> var 4/4 = 1 -> sigma 1.0
    assert std_20.compute(_series([3.0, 3.0, 5.0, 5.0]), {"period": 4.0}) == 1.0


def test_std_flat_window_exactly_zero():
    assert std_20.compute(_series([5.0] * 4), {"period": 4.0}) == 0.0


def test_std_boundary_and_default():
    assert math.isnan(std_20.compute(_series([1.0, 2.0]), {"period": 3.0}))
    assert std_20.compute(_series([float(i) for i in range(20)]), {}) > 0.0


# --- zscore_20 ---


def test_zscore_golden_last_over_mean():
    # Hand calc: [3,3,5,5] p=4 -> mean 4, sigma 1 -> (5-4)/1 = 1.0 exact
    assert zscore_20.compute(_series([3.0, 3.0, 5.0, 5.0]), {"period": 4.0}) == 1.0


def test_zscore_flat_canonical_zero_not_nan():
    # Canonical spec branch: sigma==0 -> 0.0 (no dispersion), never nan
    assert zscore_20.compute(_series([5.0] * 4), {"period": 4.0}) == 0.0


def test_zscore_boundary_nan_below_minimum():
    assert math.isnan(zscore_20.compute(_series([1.0, 2.0]), {"period": 3.0}))


# --- returns ---


def test_returns_golden_log_ratio():
    # Hand calc: ln(4/2) = ln(2) = 0.6931471805599453...
    value = returns.compute(_series([2.0, 4.0]), {})
    assert value == pytest.approx(0.6931471805599453, abs=1e-12)


def test_returns_flat_exactly_zero():
    # Hand calc: ln(5/5) = ln(1) = 0.0 exact
    assert returns.compute(_series([5.0, 5.0]), {}) == 0.0


def test_returns_nonpositive_close_nan_and_single_bar_nan():
    assert math.isnan(returns.compute(_series([0.0, 5.0]), {}))
    assert math.isnan(returns.compute(_series([-2.0, -1.0]), {}))
    assert math.isnan(returns.compute(_series([5.0]), {}))


# --- atr_14 ---


def test_atr_golden_tv_tr_seed_dyadic():
    # Hand calc p=2: TV TR: bar0 h10 l8 -> TR=2; bar1 h11 l9 pc10 -> max(2,1,1)=2
    # seed = (2+2)/2 = 2.0 exact; bar2 TR=2 -> (2*1+2)/2 = 2.0
    bars = [(10.0, 8.0, 10.0), (11.0, 9.0, 11.0), (12.0, 10.0, 12.0)]
    assert atr_14.compute(_series_ohlc(bars), {"period": 2.0}) == 2.0


def test_atr_flat_series_exactly_zero():
    assert atr_14.compute(_series([5.0] * 14), {}) == 0.0


def test_atr_boundary_nan_below_period_at_period_computes():
    assert math.isnan(atr_14.compute(_series([5.0]), {"period": 2.0}))
    bars = [(10.0, 8.0, 10.0), (11.0, 9.0, 11.0)]
    assert atr_14.compute(_series_ohlc(bars), {"period": 2.0}) == 2.0


def test_atr_nonfinite_high_returns_nan():
    bars = [(10.0, 8.0, 10.0), (float("nan"), 9.0, 11.0)]
    assert math.isnan(atr_14.compute(_series_ohlc(bars), {"period": 2.0}))


# --- adx_14 ---


def test_adx_golden_monotone_up_100_exact():
    # Hand calc p=2, bars (h,l,c): (10,10,10),(11,10,11),(12,11,12),(13,12,13)
    # i=1..3: +DM=1, -DM=0, TR=1 each -> smoothed sDM=sTR=1
    # +DI=100, -DI=0 -> DX = 100*100/100 = 100 -> ADX seed mean(100,100) = 100.0 exact
    bars = [(10.0, 10.0, 10.0), (11.0, 10.0, 11.0), (12.0, 11.0, 12.0), (13.0, 12.0, 13.0)]
    assert adx_14.compute(_series_ohlc(bars), {"period": 2.0}) == 100.0


def test_adx_flat_series_canonical_zero():
    # sTR==0 -> DI=0/0 branch -> DX=0 -> ADX=0.0 exact (no epsilon)
    assert adx_14.compute(_series([5.0] * 4), {"period": 2.0}) == 0.0


def test_adx_boundary_two_p_bars():
    bars = [(10.0, 10.0, 10.0), (11.0, 10.0, 11.0), (12.0, 11.0, 12.0)]
    assert math.isnan(adx_14.compute(_series_ohlc(bars), {"period": 2.0}))  # 3 < 2*2
    bars4 = bars + [(13.0, 12.0, 13.0)]
    assert adx_14.compute(_series_ohlc(bars4), {"period": 2.0}) == 100.0  # 4 == 2p


def test_adx_default_minimum_28_boundary():
    assert math.isnan(adx_14.compute(_series([float(i) for i in range(27)]), {}))
    value = adx_14.compute(_series([float(i) for i in range(28)]), {})
    assert value == 100.0  # monotone -> every DX 100 -> ADX 100 exact


# --- macd ---


def test_macd_golden_line_value():
    # Hand calc fast=2 slow=4, closes [2,4,6,8,10,12]:
    # EMA_f seed 2, a=2/3: 10/3 -> 46/9 -> 190/27 -> 730/81 -> 2674/243 = 11.004115226337449
    # EMA_s seed 2, a=0.4: 2.8 -> 4.08 -> 5.648 -> 7.3888 -> 9.23328
    # line = 2674/243 - 9.23328 = 1.7708352263374483
    closes = [2.0, 4.0, 6.0, 8.0, 10.0, 12.0]
    value = macd.compute(_series(closes), {"fast": 2.0, "slow": 4.0})
    assert value == pytest.approx(1.7708352263374483, abs=1e-12)


def test_macd_boundary_n_below_slow_nan_at_slow_computes():
    # n < slow -> nan (EMA_slow not through one full period); n == slow -> value
    assert math.isnan(macd.compute(_series([2.0, 4.0, 6.0]), {"fast": 2.0, "slow": 4.0}))
    value = macd.compute(_series([2.0, 4.0, 6.0, 8.0]), {"fast": 2.0, "slow": 4.0})
    # Hand calc n=4: EMA_f = 190/27, EMA_s = 5.648 -> 7.037037... - 5.648 = 1.3890370370370367
    assert value == pytest.approx(1.3890370370370367, abs=1e-12)


def test_macd_fast_ge_slow_valueerror():
    series = _series([1.0, 2.0, 3.0, 4.0])
    for fast, slow in ((4.0, 4.0), (5.0, 3.0)):
        with pytest.raises(ValueError):
            macd.compute(series, {"fast": fast, "slow": slow})


def test_macd_defaults_path_twenty_six_bars():
    closes = [float(i) for i in range(26)]
    assert math.isfinite(macd.compute(_series(closes), {}))  # fast 12 < slow 26
    assert math.isnan(macd.compute(_series(closes[:25]), {}))


# --- bb_percent ---


def test_bb_golden_position_in_band():
    # Hand calc: [3,3,5,5] p=4 k=2 -> mean 4, sigma 1, upper 6, lower 2
    # %B = (5-2)/(6-2) = 3/4 = 0.75 exact
    assert bb_percent.compute(_series([3.0, 3.0, 5.0, 5.0]), {"period": 4.0, "stddev": 2.0}) == 0.75


def test_bb_flat_canonical_midline_half():
    assert bb_percent.compute(_series([5.0] * 4), {"period": 4.0, "stddev": 2.0}) == 0.5


def test_bb_unbounded_above_band_no_clamp():
    # last outside the 2-sigma band: p=6, five 0s + 100 -> mean a = 100/6,
    # sigma = a*sqrt(5) (devs: -a x5, +5a -> var = a^2*(5+25)/6 = 5a^2)
    # upper = a + 2*sigma = 91.20..; last 100 > upper -> %B ~ 1.059 > 1 (no clamp)
    closes = [0.0] * 5 + [100.0]
    value = bb_percent.compute(_series(closes), {"period": 6.0, "stddev": 2.0})
    a = 100.0 / 6.0
    sigma = a * math.sqrt(5.0)
    upper, lower = a + 2 * sigma, a - 2 * sigma
    assert value == pytest.approx((100.0 - lower) / (upper - lower), abs=1e-12)
    assert value > 1.0


def test_bb_boundary_nan_and_nonfinite_nan():
    assert math.isnan(bb_percent.compute(_series([1.0, 2.0]), {"period": 3.0}))
    assert math.isnan(bb_percent.compute(_series([1.0, float("nan"), 3.0, 4.0]), {"period": 4.0}))


# --- donchian_20 ---


def test_donchian_golden_upside_breakout_150():
    # Hand calc: prior [10,12,14] high 14 low 10 range 4; last 16 -> 100*(16-10)/4 = 150.0
    closes = [10.0, 12.0, 14.0, 16.0]
    assert donchian_20.compute(_series(closes), {"period": 3.0}) == 150.0


def test_donchian_golden_downside_minus_50():
    # Hand calc: prior high 14 low 10 range 4; last 8 -> 100*(8-10)/4 = -50.0
    assert donchian_20.compute(_series([10.0, 12.0, 14.0, 8.0]), {"period": 3.0}) == -50.0


def test_donchian_collapsed_window_canonical_branches():
    # Hand calc: prior [5,5,5] range 0 -> inside 50.0 / above 100.0 / below 0.0
    assert donchian_20.compute(_series([5.0, 5.0, 5.0, 5.0]), {"period": 3.0}) == 50.0
    assert donchian_20.compute(_series([5.0, 5.0, 5.0, 6.0]), {"period": 3.0}) == 100.0
    assert donchian_20.compute(_series([5.0, 5.0, 5.0, 4.0]), {"period": 3.0}) == 0.0


def test_donchian_boundary_n_below_p_plus_one_nan():
    assert math.isnan(donchian_20.compute(_series([10.0, 12.0]), {"period": 3.0}))


# --- choppiness ---


def test_chop_flat_canonical_100_exact():
    assert choppiness.compute(_series([5.0] * 4), {"period": 4.0}) == 100.0


def test_chop_pure_gap_series_zero_exact():
    # Hand calc p=2: bars 10,12 (dojis): TV TR0=0, TR1=max(0,|12-10|)=2 -> sum 2
    # range 12-10 = 2 -> ratio 1 -> log10(1)=0 -> chop = 0.0 exact (pure jump = trend)
    assert choppiness.compute(_series([10.0, 12.0]), {"period": 2.0}) == 0.0


def test_chop_period_one_valueerror_log_denominator():
    # adversarial: log10(1) = 0 would divide by zero — bounded >= 2 at the param
    with pytest.raises(ValueError):
        choppiness.compute(_series([1.0, 2.0, 3.0]), {"period": 1.0})


def test_chop_boundary_nan_below_period():
    assert math.isnan(choppiness.compute(_series([1.0, 2.0]), {"period": 3.0}))


def test_chop_result_theoretically_bounded_above_100():
    series = _series([10.0, 11.0, 12.0, 13.0, 14.0])
    value = choppiness.compute(series, {"period": 4.0})
    assert math.isfinite(value)
    assert value <= 100.0


# --- vol_regime ---


def test_vol_regime_golden_ratio_two():
    # Hand calc w=2: hist [0,2] sigma 1; now [4,8] sigma 2 -> 2.0 exact
    assert vol_regime.compute(_series([0.0, 2.0, 4.0, 8.0]), {"window": 2.0}) == 2.0


def test_vol_regime_flat_history_canonical_one():
    # hist [3,3] sigma 0 -> baseline 1.0 even though now [2,4] sigma 1 (spec branch)
    assert vol_regime.compute(_series([3.0, 3.0, 2.0, 4.0]), {"window": 2.0}) == 1.0


def test_vol_regime_boundary_two_windows():
    assert math.isnan(vol_regime.compute(_series([1.0, 2.0, 3.0]), {"window": 2.0}))
    assert vol_regime.compute(_series([1.0, 2.0, 3.0, 4.0]), {"window": 2.0}) > 0.0


# --- hurst ---


def test_hurst_golden_alternating_returns_near_zero():
    # Hand calc w=4: returns alternate +x,-x,+x,-x (x = ln(1.1)); mean 0 exact;
    # X oscillates 0,x,0,x,0 -> R = x; S = sqrt(mean(x^2)) = x -> RS ~ 1
    # H = ln(RS)/ln(4) ~ 0 (theoretically 0; float residue bounded by 1e-9)
    value = hurst.compute(_series([100.0, 110.0, 100.0, 110.0, 100.0]), {"window": 4.0})
    assert abs(value - 0.0) <= 1e-9


def test_hurst_constant_returns_canonical_half():
    # Hand calc: closes double each bar -> returns all ln(2) -> centered all 0
    # -> S == 0 -> canonical 0.5 (deterministic series, no measurable memory)
    value = hurst.compute(_series([4.0, 8.0, 16.0, 32.0, 64.0]), {"window": 4.0})
    assert value == 0.5


def test_hurst_flat_closes_canonical_half():
    assert hurst.compute(_series([5.0] * 5), {"window": 4.0}) == 0.5


def test_hurst_window_one_valueerror_log_denominator():
    with pytest.raises(ValueError):
        hurst.compute(_series([1.0, 2.0, 3.0]), {"window": 1.0})


def test_hurst_boundary_nan_below_w_plus_one():
    assert math.isnan(hurst.compute(_series([1.0, 2.0, 3.0, 4.0]), {"window": 4.0}))


def test_hurst_nonpositive_close_nan():
    assert math.isnan(hurst.compute(_series([100.0, -50.0, 60.0, 70.0, 80.0]), {"window": 4.0}))


# --- universal edges: empty / params None / defaults / unknown keys ---


@pytest.mark.parametrize("module", _ALL)
def test_empty_candles_valueerror(module):
    with pytest.raises(ValueError):
        module.compute([], {})


@pytest.mark.parametrize("module", _ALL)
def test_params_none_valueerror(module):
    with pytest.raises(ValueError):
        module.compute(_series([1.0, 2.0, 3.0, 4.0]), None)


@pytest.mark.parametrize("module", _ALL)
def test_unknown_param_keys_ignored(module):
    # forward-compat: hostile junk keys never change behavior, never raise
    series = _series([float(i) + 1.0 for i in range(60)])
    with_junk = module.compute(series, {"period": 4.0, "window": 4.0, "evil": 1.0})
    clean = module.compute(series, {"period": 4.0, "window": 4.0})
    assert with_junk == clean or (math.isnan(with_junk) and math.isnan(clean))


@pytest.mark.parametrize("module", _ALL)
def test_nonfinite_close_returns_nan_not_raise(module):
    series = _series([10.0, 11.0, float("nan"), 12.0, 13.0] + [14.0 + i for i in range(60)])
    assert math.isnan(module.compute(series, {"period": 4.0, "window": 4.0, "fast": 2.0, "slow": 3.0}))


# --- param validation bounds (adversarial) ---


@pytest.mark.parametrize(
    "module, key, bad",
    [
        (sma_20, "period", 0.0),
        (std_20, "period", -1.0),
        (zscore_20, "period", 1.0),  # zscore bound >= 2
        (zscore_20, "period", 2.5),
        (atr_14, "period", 0.0),
        (adx_14, "period", 0.0),
        (adx_14, "period", 3.5),
        (bb_percent, "period", 0.0),
        (bb_percent, "stddev", float("inf")),
        (bb_percent, "stddev", -1.0),
        (donchian_20, "period", 0.0),
        (choppiness, "period", 1.5),
        (vol_regime, "window", 0.0),
        (hurst, "window", 2.5),
        (macd, "fast", 0.0),
        (macd, "slow", -3.0),
    ],
)
def test_param_bounds_valueerror(module, key, bad):
    series = _series([float(i) + 1.0 for i in range(60)])
    with pytest.raises(ValueError):
        module.compute(series, {key: bad})


@pytest.mark.parametrize(
    "module",
    [sma_20, std_20, zscore_20, atr_14, adx_14, bb_percent, donchian_20, choppiness, vol_regime, hurst, macd],
)
def test_nonfinite_period_valueerror_never_int_crash(module):
    # adversarial: isfinite BEFORE int() — inf/nan must raise ValueError, not OverflowError
    series = _series([float(i) + 1.0 for i in range(60)])
    for bad in (float("inf"), float("nan")):
        with pytest.raises(ValueError):
            module.compute(series, {"period": bad, "window": bad, "fast": bad, "slow": bad})


def test_huge_close_values_finite_or_nan_no_overflow_crash():
    # adversarial: 1e12-scale prices handled — finite result or honest nan, never a crash
    series = _series([1e12 + i for i in range(60)])
    for module in _ALL:
        value = module.compute(series, {"period": 4.0, "window": 4.0, "fast": 2.0, "slow": 3.0})
        assert math.isnan(value) or math.isfinite(value)


# --- REQUIREMENTS pinned per spec ---


def test_requirements_pinned_per_spec():
    expected = {
        "sma_20": (("price",), 20, 0, {"period": 20.0}),
        "std_20": (("price",), 20, 0, {"period": 20.0}),
        "zscore_20": (("price",), 20, 0, {"period": 20.0}),
        "returns": (("price",), 2, 0, {}),
        "atr_14": (("price",), 14, 0, {"period": 14.0}),
        "adx_14": (("price",), 28, 0, {"period": 14.0}),
        "macd": (("price",), 26, 0, {"fast": 12.0, "slow": 26.0}),
        "bb_percent": (("price",), 20, 0, {"period": 20.0, "stddev": 2.0}),
        "donchian_20": (("price",), 21, 0, {"period": 20.0}),
        "choppiness": (("price",), 14, 0, {"period": 14.0}),
        "vol_regime": (("price",), 40, 0, {"window": 20.0}),
        "hurst": (("price",), 51, 0, {"window": 50.0}),
    }
    for module in _ALL:
        req = module.REQUIREMENTS
        assert isinstance(req, DataRequirement)
        needs, minimum, warmup, defaults = expected[module.NAME]
        assert req.needs == needs, module.NAME
        assert req.minimum_history == minimum, module.NAME
        assert req.warmup == warmup, module.NAME
        assert req.defaults == defaults, module.NAME
        assert req.dependencies == ()
        assert req.provides_regime is False
        assert req.history_duration is None
        with pytest.raises((ValidationError, AttributeError, TypeError)):
            req.minimum_history = 999


# --- Isolation Law + discovery lock (declared amendment #1) ---


def test_compute_signature_isolation_contract():
    registry = AnalyticsRegistry()
    for module in _ALL:
        registry.register(module)
        assert list(inspect.signature(module.compute).parameters) == ["candles", "params"]
        for smuggled in ("provider", "clock", "as_of"):
            assert not hasattr(module, smuggled)


def test_from_package_discovers_p17_plus_p18_batch():
    # Declared amendment #1 (m3.2_p18 spec): P17 lock extends 3 -> 15 names.
    # 2026-09-30: 14 -> 24, Book Feature Library batch 1 — declared §7 amendment,
    # m3.2_book_feature_library spec (tests LGTM'd before this edit).
    # 2026-10-02: 24 -> 34, m4.7 Tier-1 analytics batch — declared amendment 2,
    # m4.7_playbook_tier1 spec (tests LGTM'd before this edit).
    names = AnalyticsRegistry.from_package().names()
    assert sorted(names) == [
        "adx_14", "anchored_twap", "atr_14", "atr_percentile_rank", "bar_low",
        "bb_percent", "bollinger_bandwidth", "bollinger_bandwidth_percentile",
        "choppiness", "close_change", "close_position", "donchian_20",
        "efficiency_ratio", "ema_20", "ema_21", "ema_55", "ema_8", "hurst",
        "last_close", "macd", "market_state", "ols_r_squared", "rejection_bar",
        "returns", "rsi_14", "sma_20", "std_20", "supertrend_flip",
        "sweep_reversal", "swing_high", "swing_low", "variance_ratio",
        "vol_regime", "zscore_20",
    ]


# --- engine end-to-end (default registry + real forex profile) ---


async def test_engine_end_to_end_p18_features_fullly_supported():
    # 51-bar ascending series satisfies every min in the request set (hurst 51, adx 28)
    closes = [10.0 + i for i in range(51)]
    engine = _engine(_StaticProvider(_series(closes)))
    result = await engine.get_features(
        _request(("sma_20", "adx_14", "hurst", "vol_regime", "zscore_20"))
    )
    assert result.features["sma_20"] == 50.5  # mean of last 20 = values 41..60 -> 50.5 exact
    assert result.features["adx_14"] == 100.0  # monotone -> every DX 100 -> ADX 100
    assert result.features["hurst"] == pytest.approx(
        0.761630694861961, abs=1e-12
    )  # R/S on arithmetic walk (comment: declining log-returns -> persistent, H > 0.5)
    assert result.features["vol_regime"] > 0.0  # two disjoint windows both varying
    assert math.isfinite(result.features["zscore_20"])
    assert result.capability_status is CapabilityStatus.FULLY_SUPPORTED  # real yaml entries
    assert result.capability_warning is None
