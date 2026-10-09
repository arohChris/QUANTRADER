"""
M3.2 Book Feature Library batch 1 (10 analytics) — Spec tests (WRITTEN BEFORE CODE).
WHY: AGENTS.md §2 Step 3 — tests first against docs/specs/m3.2_book_feature_library.spec.md
(no LGTM yet; Step 4 gate pending). Book = Volume-Free Trading Strategy Playbook
Feature Library cards; formula sources cited in the spec §5 (Kaufman, TradingView
ta.percentrank, Bollinger official, Lo-MacKinlay).

Direct impl import ONLY — no importorskip. Collection RED until Step 5 (honest
"not implemented"; house style per test_m3.2_spec.py / test_m3.2_p18_spec.py);
after impl, 100% pass, 0 skips (§2 Step 6).

Covers spec sections: §5 per-analytic goldens (hand-calc comments, exact == on
dyadic/branch values, approx abs=1e-12 only for irrationals), min-bar boundaries
(nan one below / finite at), param validation bounds incl. inf/nan (isfinite
BEFORE int), REQUIREMENTS pin table, Isolation signature, 24-name discovery lock
(declared §7 amendment), capability entries, VR 2-implementation cross-check
(§3.5), one adversarial NOT in the spec (unsorted closes — bounded output).
Money: none (indicator floats) — §4 of the spec.
"""
from __future__ import annotations

import inspect
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

# Direct impl import — fails collection until Step 5 (honest RED).
from modules.m3_analytics.analytics import (
    anchored_twap,
    atr_percentile_rank,
    bollinger_bandwidth,
    bollinger_bandwidth_percentile,
    efficiency_ratio,
    market_state,
    ols_r_squared,
    swing_high,
    swing_low,
    variance_ratio,
)
from modules.m3_analytics.engine.analytics_registry import AnalyticsRegistry, DataRequirement
from shared.contracts.market_data import Candle

_ROOT = Path(__file__).resolve().parents[2]
_CAP_YAML = _ROOT / "config" / "capabilities.yaml"

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

_ALL = (
    efficiency_ratio, atr_percentile_rank, ols_r_squared, variance_ratio,
    swing_high, swing_low, market_state, bollinger_bandwidth,
    bollinger_bandwidth_percentile, anchored_twap,
)

# Shared override dict for cross-module tests: valid for every module that reads
# the key it sees (unknown keys ignored by param policy §4); p18 precedent.
_MERGED = {"period": 4.0, "window": 4.0, "lookback": 4.0, "stddev": 2.0}


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
    """Candle list from (high, low, close) tuples — TR/pivot paths need real h/l."""
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


def _state_series(highs: list[float], lows: list[float]) -> list[Candle]:
    """market_state fixture: distinct high/low per bar, close at the midpoint."""
    return _series_ohlc([(h, l, (h + l) / 2.0) for h, l in zip(highs, lows)])


# --- contract: REQUIREMENTS pin table (spec §5 — these pins ARE the DB request) ---


def test_requirements_pin_table_matches_spec():
    expected = {
        efficiency_ratio: (21, {"period": 20.0}),
        atr_percentile_rank: (263, {"period": 14.0, "window": 250.0}),  # 250+14-1
        ols_r_squared: (50, {"period": 50.0}),
        variance_ratio: (7, {"period": 5.0}),  # q+2
        swing_high: (5, {"lookback": 2.0}),  # 2k+1
        swing_low: (5, {"lookback": 2.0}),
        market_state: (8, {"lookback": 2.0}),  # 3k+2
        bollinger_bandwidth: (20, {"period": 20.0, "stddev": 2.0}),
        bollinger_bandwidth_percentile: (119, {"period": 20.0, "stddev": 2.0, "lookback": 100.0}),
        anchored_twap: (1, {}),
    }
    for module, (min_hist, defaults) in expected.items():
        req = module.REQUIREMENTS
        assert isinstance(req, DataRequirement), module.NAME
        assert req.needs == ("price",), module.NAME
        assert req.minimum_history == min_hist, module.NAME
        assert req.warmup == 0, module.NAME
        assert req.dependencies == (), module.NAME
        assert req.defaults == defaults, module.NAME
        assert req.provides_regime is False, module.NAME
        assert req.history_duration is None, module.NAME


def test_compute_signature_isolation_contract():
    """Isolation Law (registry would raise anyway; signature + no smuggled attrs)."""
    registry = AnalyticsRegistry()
    for module in _ALL:
        registry.register(module)  # NAME/compute/REQUIREMENTS validated at register
        assert list(inspect.signature(module.compute).parameters) == ["candles", "params"]
        for smuggled in ("provider", "clock", "as_of", "timeframe"):
            assert not hasattr(module, smuggled), f"{module.NAME} smuggles {smuggled}"
    assert sorted(registry.names()) == sorted(m.NAME for m in _ALL)


def test_from_package_discovers_34_names():
    # Declared §7 amendment: 14 existing + 10 new = 24 (locks in m3.2_spec /
    # m3.2_p18_spec amended the same way in Step 5 — until then the world is red).
    # 2026-10-02: 24 -> 34, m4.7 Tier-1 analytics batch — declared amendment 2,
    # m4.7_playbook_tier1 spec (tests LGTM'd before this edit).
    names = sorted(AnalyticsRegistry.from_package().names())
    assert names == [
        "adx_14", "anchored_twap", "atr_14", "atr_percentile_rank", "bar_low",
        "bb_percent", "bollinger_bandwidth", "bollinger_bandwidth_percentile",
        "choppiness", "close_change", "close_position", "donchian_20",
        "efficiency_ratio", "ema_20", "ema_21", "ema_55", "ema_8", "hurst",
        "last_close", "macd", "market_state", "ols_r_squared", "rejection_bar",
        "returns", "rsi_14", "sma_20", "std_20", "supertrend_flip",
        "sweep_reversal", "swing_high", "swing_low", "variance_ratio",
        "vol_regime", "zscore_20",
    ]
    assert len(names) == 34


def test_capabilities_yaml_declares_all_ten_fully_supported():
    data = yaml.safe_load(_CAP_YAML.read_text(encoding="utf-8"))
    features = data["features"]
    for module in _ALL:
        entry = features.get(module.NAME)
        assert entry is not None, f"capabilities.yaml missing {module.NAME}"
        assert entry["status"] == "FULLY_SUPPORTED", module.NAME
        assert entry["requires"] == ["price"], module.NAME


# --- shared data/param policy (spec §4) ---


def test_params_none_raises_valueerror_for_all():
    series = _series([10.0 + (i % 7) * 0.5 for i in range(70)])
    for module in _ALL:
        with pytest.raises(ValueError, match="params"):
            module.compute(series, None)


def test_empty_candles_raises_valueerror_for_all():
    for module in _ALL:
        with pytest.raises(ValueError):
            module.compute([], dict(_MERGED))


def test_nonfinite_ohlc_returns_nan_for_all():
    # House policy (§4): ANY non-finite high/low/close anywhere in range -> nan.
    # 70 bars clears every min under _MERGED (max: market_state lookback4 -> 14),
    # so the nan-at-index-2 sentinel is the only cause of the nan result.
    closes = [10.0 + (i % 7) * 0.5 for i in range(70)]
    closes[2] = float("nan")
    series = _series(closes)
    for module in _ALL:
        assert math.isnan(module.compute(series, dict(_MERGED))), module.NAME


def test_adversarial_unsorted_closes_still_bounded():
    # NOT in the spec (§2.3): feed bug delivers chronologically shuffled closes.
    # Safe failure for ER = triangle inequality still holds -> strictly in [0, 1],
    # never a crash, never an out-of-band value a strategy gate could act on.
    shuffled = [14.0, 10.0, 18.0, 11.0, 17.0, 12.0, 16.0, 13.0, 15.0,
                10.5, 14.5, 11.5, 17.5, 12.5, 16.5, 13.5, 15.5, 10.0,
                18.5, 11.0, 17.0]
    value = efficiency_ratio.compute(_series(shuffled), {"period": 20.0})
    assert math.isfinite(value)
    assert 0.0 <= value <= 1.0


# --- 5.1 efficiency_ratio (Kaufman ER) ---


def test_efficiency_ratio_hand_calc():
    # Hand calc: closes [100,101,103,102,105], p=4 -> net |105-100| = 5,
    # path 1+2+1+3 = 7 -> 5/7 = 0.7142857142857143 (single division, exact ==)
    closes = [100.0, 101.0, 103.0, 102.0, 105.0]
    assert efficiency_ratio.compute(_series(closes), {"period": 4.0}) == 0.7142857142857143


def test_efficiency_ratio_one_way_series_is_one():
    closes = [float(i) for i in range(1, 22)]  # 21 bars, p=20 -> net == path
    assert efficiency_ratio.compute(_series(closes), {"period": 20.0}) == 1.0


def test_efficiency_ratio_jagged_series_hand_calc():
    # Hand calc: 21 closes zigzag +2,-1 (x10) from 10 -> net |20-10| = 10,
    # path 10*(2+1) = 30 -> 10/30 (one division; module must match exactly)
    closes = [10.0, 12.0, 11.0, 13.0, 12.0, 14.0, 13.0, 15.0, 14.0, 16.0, 15.0,
              17.0, 16.0, 18.0, 17.0, 19.0, 18.0, 20.0, 19.0, 21.0, 20.0]
    assert efficiency_ratio.compute(_series(closes), {"period": 20.0}) == 10.0 / 30.0


def test_efficiency_ratio_flat_returns_zero():
    # D5: path == 0 -> 0.0 (gate-safe: >0.25 / >0.3 must fail)
    closes = [5.0] * 25
    assert efficiency_ratio.compute(_series(closes), {"period": 20.0}) == 0.0


def test_efficiency_ratio_boundary_nan_below_21_at_21_computes():
    assert math.isnan(efficiency_ratio.compute(_series([float(i) for i in range(1, 21)]),
                                               {"period": 20.0}))
    assert efficiency_ratio.compute(_series([float(i) for i in range(1, 22)]),
                                    {"period": 20.0}) == 1.0


def test_efficiency_ratio_period_bounds_valueerror():
    # spec §5.1: period integral >= 2; isfinite BEFORE int (no int(inf) crash)
    series = _series([float(i) for i in range(1, 25)])
    for bad in (1, 0, -5, 1.5, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="period"):
            efficiency_ratio.compute(series, {"period": bad})


def test_efficiency_ratio_adversarial_absurd_period_returns_nan():
    # adversarial (§5.1): period=1e9 — no window reuse, no crash, honest nan
    assert math.isnan(efficiency_ratio.compute(_series([float(i) for i in range(1, 30)]),
                                               {"period": 1e9}))


# --- 5.2 atr_percentile_rank (TV ta.percentrank over pinned ATR series) ---


def _atr_rank_fixture_7() -> list[Candle]:
    # Each bar contains the prior close -> TR = high-low (spec §5.2):
    # TRs [10,11,14,12,13,9,8]; Wilder p=4: seed 47/4, then (a*3+t)/4.
    highs = [10.0, 16.0, 24.0, 27.0, 33.0, 34.0, 36.0]
    lows = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 28.0]
    closes = [5.0, 10.5, 17.0, 21.0, 26.5, 29.5, 32.0]
    return _series_ohlc(list(zip(highs, lows, closes)))


def test_atr_percentile_rank_hand_calc():
    # Hand calc: TRs [10,11,14,12,13,9,8] p=4 -> ATRs [11.75, 12.0625,
    # 11.296875, 10.47265625] (all dyadic, exact); window=4, cur=10.47265625
    # -> #{v <= cur} = 1 -> 100*1/4 = 25.0 (TV percentrank, inclusive <=)
    params = {"period": 4.0, "window": 4.0}
    assert atr_percentile_rank.compute(_atr_rank_fixture_7(), params) == 25.0


def test_atr_percentile_rank_constant_atr_is_100():
    # D4 trap (spec §6): flat bars -> all ATRs equal 0 -> every v <= cur
    # -> 100.0 (TV-compatible; gates must pair with ER/AD as the book does)
    params = {"period": 4.0, "window": 4.0}
    assert atr_percentile_rank.compute(_series([5.0] * 7), params) == 100.0


def test_atr_percentile_rank_boundary_nan_below_7_at_7_computes():
    # min = window+period-1 = 7 under these overrides
    assert math.isnan(atr_percentile_rank.compute(
        _atr_rank_fixture_7()[:6], {"period": 4.0, "window": 4.0}))
    assert atr_percentile_rank.compute(
        _atr_rank_fixture_7(), {"period": 4.0, "window": 4.0}) == 25.0


def test_atr_percentile_rank_default_window_needs_263_bars():
    # spec §5.2 default pin: 250+14-1 = 263 — one below must be nan (DB-request pin)
    flat_262 = _series([5.0] * 262)
    assert math.isnan(atr_percentile_rank.compute(flat_262, {"period": 14.0, "window": 250.0}))
    flat_263 = _series([5.0] * 263)
    assert atr_percentile_rank.compute(flat_263, {"period": 14.0, "window": 250.0}) == 100.0


def test_atr_percentile_rank_param_bounds_valueerror():
    series = _atr_rank_fixture_7()
    for bad in (0, -1, 1.5, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="period"):
            atr_percentile_rank.compute(series, {"period": bad, "window": 4.0})
    for bad in (1, 0, -5, 2.5, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="window"):
            atr_percentile_rank.compute(series, {"period": 4.0, "window": bad})


def test_atr_percentile_rank_adversarial_absurd_window_returns_nan():
    # adversarial (§5.2): window=1e6 on a 7-bar series -> nan, no alloc/crash
    assert math.isnan(atr_percentile_rank.compute(
        _atr_rank_fixture_7(), {"period": 4.0, "window": 1e6}))


# --- 5.3 ols_r_squared ---


def test_ols_r_squared_hand_calc_trend():
    # Hand calc: y=[1,3,2,4] x=[0..3], means 1.5/2.5 -> Sxy=4.0, Sxx=5.0,
    # Syy=5.0 -> R^2 = 16/25 = 0.64 (exact single division)
    assert ols_r_squared.compute(_series([1.0, 3.0, 2.0, 4.0]), {"period": 4.0}) == 0.64


def test_ols_r_squared_perfect_trend_is_one():
    # Hand calc: y=[1,2,3,4] -> Sxy=5.0, Sxx=Syy=5.0 -> 25/25 = 1.0
    assert ols_r_squared.compute(_series([1.0, 2.0, 3.0, 4.0]), {"period": 4.0}) == 1.0


def test_ols_r_squared_perfect_downtrend_is_one():
    # Sign-invariance: R^2 of a perfect decline is still 1.0 (Sxy=-5 -> squared)
    assert ols_r_squared.compute(_series([4.0, 3.0, 2.0, 1.0]), {"period": 4.0}) == 1.0


def test_ols_r_squared_flat_returns_zero():
    # D5: Syy == 0 (undefined R^2) -> gate-safe 0.0, never a ZeroDivisionError
    assert ols_r_squared.compute(_series([7.0] * 10), {"period": 10.0}) == 0.0


def test_ols_r_squared_boundary_nan_below_50_at_50_computes():
    assert math.isnan(ols_r_squared.compute(
        _series([float(i) for i in range(1, 50)]), {"period": 50.0}))
    assert ols_r_squared.compute(
        _series([float(i) for i in range(1, 51)]), {"period": 50.0}) == 1.0


def test_ols_r_squared_period_bounds_valueerror():
    # spec §5.3: period integral >= 3 (2 points cannot be a regression with df)
    series = _series([float(i) for i in range(1, 55)])
    for bad in (2, 1, 0, -5, 50.5, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="period"):
            ols_r_squared.compute(series, {"period": bad})


def test_ols_r_squared_adversarial_absurd_period_returns_nan():
    assert math.isnan(ols_r_squared.compute(
        _series([float(i) for i in range(1, 55)]), {"period": 1e9}))


# --- 5.4 variance_ratio (Lo-MacKinlay construction, book gate form) ---


def test_variance_ratio_hand_calc_alternation():
    # Hand calc: closes [10,11,10,11,10,11] q=2 -> rq = [0,0,0,0] (ln(10/10)...)
    # -> Var(rq)=0 exactly; Var(r1)=0.008720669159359434 (±ln(1.1) alternating)
    # -> VR = 0/(2*Var(r1)) = 0.0 (exact)
    closes = [10.0, 11.0, 10.0, 11.0, 10.0, 11.0]
    assert variance_ratio.compute(_series(closes), {"period": 2.0}) == 0.0


def test_variance_ratio_flat_prices_return_one():
    # D5/D7: Var(r1)==0 -> 1.0 (null random-walk value; gate <1 fails safe)
    assert variance_ratio.compute(_series([10.0] * 20), {"period": 5.0}) == 1.0


def test_variance_ratio_boundary_nan_below_q_plus_2_at_q_plus_2_computes():
    # min = q+2 under the EFFECTIVE q: default q=5 -> 7 (REQUIREMENTS pin,
    # locked by the pin-table test); override q=2 -> 4.
    six = [10.0, 11.0, 10.0, 11.0, 10.0, 11.0]
    assert math.isnan(variance_ratio.compute(_series(six), {"period": 5.0}))
    three = [10.0, 11.0, 10.0]
    assert math.isnan(variance_ratio.compute(_series(three), {"period": 2.0}))
    # Hand calc: 4 alternating closes q=2 -> rq = [ln(10/10), ln(11/11)] = [0,0]
    # -> Var(rq) = 0 exactly, Var(r1) = alternating +/- ln(1.1) > 0 -> 0.0
    assert variance_ratio.compute(_series(three + [11.0]), {"period": 2.0}) == 0.0


def test_variance_ratio_nonpositive_close_returns_nan():
    # log domain (same rule as returns.py): <=0 price is not a number here
    assert math.isnan(variance_ratio.compute(_series([0.0, 5.0, 6.0, 5.0, 6.0, 5.0, 6.0]),
                                             {"period": 2.0}))


def test_variance_ratio_q_bounds_valueerror():
    # spec §5.4: q integral >= 2 (q=1 would be Var/Var, not a ratio across scales)
    series = _series([10.0 + (i % 5) for i in range(20)])
    for bad in (1, 0, -1, 2.5, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="period"):
            variance_ratio.compute(series, {"period": bad})


def test_variance_ratio_adversarial_absurd_q_returns_nan():
    assert math.isnan(variance_ratio.compute(
        _series([10.0 + (i % 5) for i in range(20)]), {"period": 1e9}))


def test_variance_ratio_crosscheck_second_implementation():
    # §3.5 second independent implementation: moment form E[x^2]-E[x]^2 for each
    # variance (module uses sum-of-squared-deviations form) — deterministic series,
    # diff <= 1e-9.
    closes = [100.0 + ((i * 37) % 23) - 11.0 + i * 0.25 for i in range(60)]
    q = 5
    module_value = variance_ratio.compute(_series(closes), {"period": float(q)})
    r1 = [math.log(closes[i] / closes[i - 1]) for i in range(1, 60)]
    rq = [math.log(closes[i] / closes[i - q]) for i in range(q, 60)]

    def _var_moments(values: list[float]) -> float:
        mean = sum(values) / len(values)
        return sum(v * v for v in values) / len(values) - mean * mean

    second = _var_moments(rq) / (q * _var_moments(r1))
    assert math.isfinite(module_value)
    assert abs(module_value - second) <= 1e-9


# --- 5.5 / 5.6 swing_high / swing_low (fractal pivots, k bars each side) ---


def test_swing_high_hand_calc():
    # Hand calc: highs [10,12,11,13,9,8] k=2 i=5 -> j=3 confirmed (j+k=5 <= i),
    # window [12,11,13,9,8], 13 strict max -> 13.0
    closes = [10.0, 12.0, 11.0, 13.0, 9.0, 8.0]
    assert swing_high.compute(_series(closes), {"lookback": 2.0}) == 13.0


def test_swing_low_hand_calc():
    # Hand calc: lows [10,7,8,5,11,12] k=2 i=5 -> j=3 window [7,8,5,11,12],
    # 5 strict min -> 5.0 (module reads .low via h=l=c fixture lows)
    lows = [10.0, 7.0, 8.0, 5.0, 11.0, 12.0]
    bars = [(l + 1.0, l, l + 0.5) for l in lows]  # high=low+1, close=low+0.5
    assert swing_low.compute(_series_ohlc(bars), {"lookback": 2.0}) == 5.0


def test_swing_high_returns_most_recent_pivot():
    # Two confirmed pivots (14 at j=2, 13 at j=5) -> the NEAREST one (13.0),
    # not the older extreme — A1/S5 read the pivot they are standing on.
    closes = [10.0, 14.0, 11.0, 12.0, 10.0, 13.0, 11.0, 9.0]
    assert swing_high.compute(_series(closes), {"lookback": 2.0}) == 13.0


def test_swing_high_boundary_nan_below_5_at_5_computes():
    # min = 2k+1 = 5: 4 bars -> no confirmable j (j>=2 and j+2<=3 impossible)
    assert math.isnan(swing_high.compute(_series([10.0, 12.0, 11.0, 13.0]),
                                         {"lookback": 2.0}))
    # 5 bars with a pivot exactly at j=2 (the only confirmable index) -> 14.0
    assert swing_high.compute(_series([10.0, 12.0, 14.0, 11.0, 10.0]),
                              {"lookback": 2.0}) == 14.0


def test_swing_high_flat_ties_return_nan():
    # strict local extreme: equal highs are not a pivot -> nan, never a level
    assert math.isnan(swing_high.compute(_series([5.0] * 8), {"lookback": 2.0}))


def test_swing_lookback_bounds_valueerror():
    series = _series([10.0, 12.0, 11.0, 13.0, 9.0, 8.0])
    for bad in (0, -1, 1.5, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="lookback"):
            swing_high.compute(series, {"lookback": bad})
        with pytest.raises(ValueError, match="lookback"):
            swing_low.compute(series, {"lookback": bad})


def test_swing_adversarial_absurd_lookback_returns_nan():
    series = _series([10.0 + (i % 6) for i in range(30)])
    assert math.isnan(swing_high.compute(series, {"lookback": 1e9}))
    assert math.isnan(swing_low.compute(series, {"lookback": 1e9}))


# --- 5.7 market_state (float encoding +1/-1/0 — spec §6 D1) ---

_BULL_H = [10.0, 11.0, 12.0, 11.0, 10.0, 15.0, 14.0, 13.0]
_BULL_L = [9.0, 8.0, 7.0, 9.0, 9.0, 8.0, 9.0, 10.0]
_BEAR_H = [10.0, 14.0, 15.0, 12.0, 11.0, 13.0, 10.0, 9.0]
_BEAR_L = [9.0, 7.0, 6.0, 8.0, 7.0, 5.0, 7.0, 8.0]


def test_market_state_bull_fixture():
    # Hand calc: pivots H=[12,15] (HH), L=[7,8] (HL) -> +1.0
    series = _state_series(_BULL_H, _BULL_L)
    assert market_state.compute(series, {"lookback": 2.0}) == 1.0


def test_market_state_bear_fixture():
    # Hand calc: pivots H=[15,13] (LH), L=[6,5] (LL) -> -1.0
    series = _state_series(_BEAR_H, _BEAR_L)
    assert market_state.compute(series, {"lookback": 2.0}) == -1.0


def test_market_state_chop_mixed_fixture():
    # Hand calc: H=[12,15] HH but L=[6,5] LL -> mixed -> 0.0 (chop)
    series = _state_series(_BULL_H, _BEAR_L)
    assert market_state.compute(series, {"lookback": 2.0}) == 0.0


def test_market_state_boundary_nan_below_8_at_8_computes():
    # min = 3k+2 = 8: 7 bars -> only one pivot of each kind -> nan; 8 -> +1.0
    assert math.isnan(market_state.compute(
        _state_series(_BULL_H[:7], _BULL_L[:7]), {"lookback": 2.0}))
    assert market_state.compute(
        _state_series(_BULL_H, _BULL_L), {"lookback": 2.0}) == 1.0


def test_market_state_flat_returns_nan():
    # strict pivots never form on ties -> fewer than 2 of either -> nan
    assert math.isnan(market_state.compute(_state_series([5.0] * 10, [5.0] * 10),
                                           {"lookback": 2.0}))


def test_market_state_lookback_bounds_valueerror_and_absurd_nan():
    series = _state_series(_BULL_H, _BULL_L)
    for bad in (0, -1, 1.5, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="lookback"):
            market_state.compute(series, {"lookback": bad})
    long_series = _state_series(_BULL_H * 4, _BULL_L * 4)
    assert math.isnan(market_state.compute(long_series, {"lookback": 1e9}))


# --- 5.8 bollinger_bandwidth (official: (upper-lower)/mid, population sigma) ---


def test_bollinger_bandwidth_hand_calc():
    # Hand calc: closes [1,2,3,4,5] p=5: mean 3, pop var ((4+1+0+1+4)/5)=2,
    # sigma=sqrt(2) -> (upper-lower)/mid = 4*sqrt(2)/3 = 1.885618083164127
    value = bollinger_bandwidth.compute(_series([1.0, 2.0, 3.0, 4.0, 5.0]),
                                        {"period": 5.0, "stddev": 2.0})
    assert value == pytest.approx(4 * math.sqrt(2) / 3, abs=1e-12)


def test_bollinger_bandwidth_flat_returns_zero():
    # sigma == 0 -> numerator 0 -> exactly 0.0 (canonical zero-width branch)
    assert bollinger_bandwidth.compute(_series([5.0] * 20),
                                       {"period": 20.0, "stddev": 2.0}) == 0.0


def test_bollinger_bandwidth_zero_stddev_returns_zero():
    # k=0 -> 2*0*sigma = 0 -> 0.0 (param allowed: finite >= 0, mirrors bb_percent)
    series = _series([float(i) for i in range(1, 21)])
    assert bollinger_bandwidth.compute(series, {"period": 20.0, "stddev": 0.0}) == 0.0


def test_bollinger_bandwidth_zero_mid_returns_nan():
    # D8: mid == 0 -> ratio undefined -> nan (division guard), not 0.0, not inf.
    # Hand calc: closes [2,-2,2,-2] sum 0 -> mid = 0/4 = 0 exactly
    series = _series([2.0, -2.0, 2.0, -2.0])
    assert math.isnan(bollinger_bandwidth.compute(series, {"period": 4.0, "stddev": 2.0}))


def test_bollinger_bandwidth_boundary_nan_below_20_at_20_computes():
    # Hand calc at=20: closes 1..20, mean 10.5, sum dev^2 = 665 (2*(0.5^2+...+9.5^2),
    # dyadic exact), var = 665/20 = 33.25, sigma=sqrt(33.25) -> 4*sqrt(33.25)/10.5
    assert math.isnan(bollinger_bandwidth.compute(
        _series([float(i) for i in range(1, 20)]), {"period": 20.0, "stddev": 2.0}))
    value = bollinger_bandwidth.compute(
        _series([float(i) for i in range(1, 21)]), {"period": 20.0, "stddev": 2.0})
    assert value == pytest.approx(4 * math.sqrt(665 / 20) / 10.5, abs=1e-12)


def test_bollinger_bandwidth_param_bounds_valueerror():
    series = _series([float(i) for i in range(1, 25)])
    for bad in (0, -5, 1.5, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="period"):
            bollinger_bandwidth.compute(series, {"period": bad, "stddev": 2.0})
    for bad in (-1.0, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="stddev"):
            bollinger_bandwidth.compute(series, {"period": 20.0, "stddev": bad})


def test_bollinger_bandwidth_adversarial_absurd_period_returns_nan():
    assert math.isnan(bollinger_bandwidth.compute(
        _series([float(i) for i in range(1, 25)]), {"period": 1e9, "stddev": 2.0}))


# --- 5.9 bollinger_bandwidth_percentile (book bbwp; TV percentrank semantics) ---


def test_bollinger_bandwidth_percentile_hand_calc():
    # Hand calc: closes [10,12,10,11,10] p=2 k=1 -> bandwidths
    # [2/11, 2/11, 1/10.5, 1/10.5] (float-identical pairs), cur = last,
    # #{v <= cur} = 2 -> 100*2/4 = 50.0
    closes = [10.0, 12.0, 10.0, 11.0, 10.0]
    params = {"period": 2.0, "stddev": 1.0, "lookback": 4.0}
    assert bollinger_bandwidth_percentile.compute(_series(closes), params) == 50.0


def test_bollinger_bandwidth_percentile_constant_is_100():
    # D4 trap: flat bars -> every bandwidth 0.0 -> all <= cur -> 100.0
    params = {"period": 2.0, "stddev": 2.0, "lookback": 4.0}
    assert bollinger_bandwidth_percentile.compute(_series([5.0] * 5), params) == 100.0


def test_bollinger_bandwidth_percentile_boundary_nan_below_5_at_5_computes():
    closes = [10.0, 12.0, 10.0, 11.0, 10.0]
    params = {"period": 2.0, "stddev": 1.0, "lookback": 4.0}
    assert math.isnan(bollinger_bandwidth_percentile.compute(_series(closes[:4]), params))
    assert bollinger_bandwidth_percentile.compute(_series(closes), params) == 50.0


def test_bollinger_bandwidth_percentile_default_lookback_needs_119_bars():
    # spec §5.9 default pin: 100+20-1 = 119
    assert math.isnan(bollinger_bandwidth_percentile.compute(
        _series([5.0] * 118), {"period": 20.0, "stddev": 2.0, "lookback": 100.0}))
    assert bollinger_bandwidth_percentile.compute(
        _series([5.0] * 119), {"period": 20.0, "stddev": 2.0, "lookback": 100.0}) == 100.0


def test_bollinger_bandwidth_percentile_nan_bandwidth_in_window_propagates():
    # TV rule: na anywhere in the rank window -> na (never drop the sample)
    closes = [2.0, -2.0, 2.0, -2.0, 2.0]
    params = {"period": 2.0, "stddev": 1.0, "lookback": 4.0}
    assert math.isnan(bollinger_bandwidth_percentile.compute(_series(closes), params))


def test_bollinger_bandwidth_percentile_param_bounds_valueerror():
    series = _series([10.0, 12.0, 10.0, 11.0, 10.0])
    for bad in (1, 0, 2.5, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="lookback"):
            bollinger_bandwidth_percentile.compute(
                series, {"period": 2.0, "stddev": 1.0, "lookback": bad})
    for bad in (0, -5, 1.5, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="period"):
            bollinger_bandwidth_percentile.compute(
                series, {"period": bad, "stddev": 1.0, "lookback": 4.0})
    for bad in (-1.0, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="stddev"):
            bollinger_bandwidth_percentile.compute(
                series, {"period": 2.0, "stddev": bad, "lookback": 4.0})


def test_bollinger_bandwidth_percentile_adversarial_absurd_lookback_returns_nan():
    series = _series([10.0, 12.0, 10.0, 11.0, 10.0])
    assert math.isnan(bollinger_bandwidth_percentile.compute(
        series, {"period": 2.0, "stddev": 1.0, "lookback": 1e6}))


# --- 5.10 anchored_twap (window anchor — spec §6 D2) ---


def test_anchored_twap_hand_calc():
    # Hand calc: (10+11+12)/3 = 33/3 = 11.0 (exact)
    assert anchored_twap.compute(_series([10.0, 11.0, 12.0]), {}) == 11.0


def test_anchored_twap_single_bar_is_that_close():
    assert anchored_twap.compute(_series([7.25]), {}) == 7.25


def test_anchored_twap_unknown_param_keys_cannot_alter_result():
    # adversarial: a param this analytic does not have must be inert (spec §5.10)
    series = _series([10.0, 11.0, 12.0])
    assert anchored_twap.compute(series, {"period": -5.0, "window": 1e9}) == 11.0
