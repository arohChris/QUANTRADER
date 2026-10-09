"""Spec: docs/specs/m4.7_playbook_tier1.spec.md §Prerequisite analytics (Slice 5a).

Direct impl import ONLY — no importorskip: collection is honestly RED until
Step 5a implements the 10 modules (AGENTS.md §2 Step 3). Every golden value
below was double-checked against the pinned spec algorithm with an independent
scratch implementation (Temp _m47_handcalc.py, 2026-10-02) BEFORE this file
was written — all 25 reproduced exactly, so no pytest.approx is needed here.
"""

from __future__ import annotations

import inspect
import math
from datetime import UTC, datetime, timedelta

import pytest

# Direct impl import — fails collection until Step 5a (honest RED).
from modules.m3_analytics.analytics import (
    bar_low,
    close_change,
    close_position,
    ema_8,
    ema_21,
    ema_55,
    last_close,
    rejection_bar,
    supertrend_flip,
    sweep_reversal,
)
from shared.contracts.market_data import Candle

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

NEW10 = (
    ema_8,
    ema_21,
    ema_55,
    last_close,
    bar_low,
    close_position,
    close_change,
    rejection_bar,
    sweep_reversal,
    supertrend_flip,
)

_IDS = [m.NAME for m in NEW10]

# spec §Prerequisite analytics pins: name -> (minimum_history, warmup, defaults)
_REQ = {
    "ema_8": (8, 2, {"period": 8.0}),
    "ema_21": (21, 5, {"period": 21.0}),
    "ema_55": (55, 14, {"period": 55.0}),
    "last_close": (1, 0, {}),
    "bar_low": (1, 0, {}),
    "close_position": (1, 0, {}),
    "close_change": (2, 0, {}),
    "rejection_bar": (1, 0, {}),
    "sweep_reversal": (21, 0, {"window": 20.0, "within": 2.0}),
    "supertrend_flip": (11, 0, {"mult": 3.0, "period": 10.0}),
}


def _bars(rows: list[tuple[float, float, float, float]]) -> list[Candle]:
    """Candles from (open, high, low, close) tuples, oldest first."""
    n = len(rows)
    out = []
    for i, (open_, high, low, close) in enumerate(rows):
        start = _T0 - timedelta(minutes=5 * (n - i))
        out.append(
            Candle(
                open=open_,
                high=high,
                low=low,
                close=close,
                interval="5m",
                interval_start=start,
                interval_end=start + timedelta(minutes=5),
            )
        )
    return out


def _flat(n: int, price: float) -> list[Candle]:
    """n flat candles (o=h=l=c=price) — dyadic-friendly fixture."""
    return _bars([(price, price, price, price)] * n)


def _sweep_minus() -> list[Candle]:
    # Hand calc (spec §sweep_reversal): bars0-19 prior max high 100;
    # bar20 high 101 > 100 with close 99 back inside; bar21 bearish confirm
    # (close 98 < open 99), gap window 1 <= within 2 -> -1.0
    return _bars(
        [(99.0, 100.0, 98.0, 99.0)] * 20 + [(99.5, 101.0, 98.0, 99.0), (99.0, 99.5, 97.0, 98.0)]
    )


def _sweep_plus() -> list[Candle]:
    # Hand calc: bars0-19 prior min low 50; bar20 low 49 < 50 with close 50.5
    # back inside; bar21 bullish confirm (close 51 > open 50.5) -> +1.0
    return _bars(
        [(51.0, 52.0, 50.0, 51.0)] * 20 + [(51.0, 51.5, 49.0, 50.5), (50.5, 51.5, 50.0, 51.0)]
    )


def _up_flip() -> list[Candle]:
    """Supertrend up-flip fixture (open=close — Supertrend ignores open)."""
    return _bars([(8, 10, 8, 8), (10, 11, 9, 10), (11.5, 12, 10, 11.5)])


def _down_flip() -> list[Candle]:
    return _bars([(9, 10, 8, 9), (8.5, 9, 7, 8.5), (6.5, 8, 6, 6.5)])


def _stale_flip() -> list[Candle]:
    """Flip at bar 1 (close 12 > U0=11), bar 2 carries dir -> not fresh."""
    return _bars([(8, 10, 8, 8), (12, 13, 11, 12), (12.5, 13, 11, 12.5)])


# ---------------- contract (spec §Prerequisite analytics header) ----------------


@pytest.mark.parametrize("mod", NEW10, ids=_IDS)
def test_name_matches_module_stem(mod):
    assert mod.NAME == mod.__name__.rsplit(".", 1)[-1]


@pytest.mark.parametrize("mod", NEW10, ids=_IDS)
def test_compute_signature_and_isolation_attrs(mod):
    # Isolation Law: compute(candles, params) only — no provider/clock/as_of/tf
    assert list(inspect.signature(mod.compute).parameters) == ["candles", "params"]
    for attr in ("provider", "clock", "as_of", "timeframe"):
        assert not hasattr(mod, attr), f"{mod.NAME} must be stateless"


@pytest.mark.parametrize("mod", NEW10, ids=_IDS)
def test_requirements_pinned_from_spec(mod):
    min_hist, warmup, defaults = _REQ[mod.NAME]
    assert mod.REQUIREMENTS.needs == ("price",)
    assert mod.REQUIREMENTS.minimum_history == min_hist
    assert mod.REQUIREMENTS.warmup == warmup
    assert mod.REQUIREMENTS.defaults == defaults


# ---------------- edges (spec §Edges, "Every analytic") ----------------


@pytest.mark.parametrize("mod", NEW10, ids=_IDS)
def test_params_none_raises_value_error(mod):
    with pytest.raises(ValueError):
        mod.compute(_flat(3, 10.0), None)


@pytest.mark.parametrize("mod", NEW10, ids=_IDS)
def test_empty_candles_raises_value_error(mod):
    with pytest.raises(ValueError):
        mod.compute([], {})


@pytest.mark.parametrize("mod", NEW10, ids=_IDS)
def test_non_finite_close_returns_nan(mod):
    # spec: non-finite OHLC input -> math.nan honest refusal, never a number
    bars = _flat(4, 10.0) + _bars([(math.nan, math.nan, math.nan, math.nan)])
    assert math.isnan(mod.compute(bars, {}))


@pytest.mark.parametrize(
    ("mod", "bars", "explicit"),
    [
        (ema_8, _flat(8, 10.0), {"period": 8.0}),
        (ema_21, _flat(21, 10.0), {"period": 21.0}),
        (ema_55, _flat(55, 10.0), {"period": 55.0}),
        (sweep_reversal, _sweep_minus(), {"window": 20.0, "within": 2.0}),
        (supertrend_flip, _flat(12, 10.0), {"mult": 3.0, "period": 10.0}),
    ],
    ids=["ema_8", "ema_21", "ema_55", "sweep_reversal", "supertrend_flip"],
)
def test_empty_params_equals_explicit_defaults(mod, bars, explicit):
    # spec Edges: params={} == REQUIREMENTS.defaults == explicit override
    assert mod.compute(bars, {}) == mod.compute(bars, explicit)


# ---------------- params bounds (spec §Prerequisite analytics) ----------------


@pytest.mark.parametrize(
    "bad",
    [{"period": 0.0}, {"period": 2.5}, {"period": math.nan}, {"period": "8"}],
    ids=["zero", "non_integral", "nan", "string_adv"],
)
@pytest.mark.parametrize("mod", (ema_8, ema_21, ema_55), ids=("ema_8", "ema_21", "ema_55"))
def test_ema_period_out_of_bounds_raises(mod, bad):
    with pytest.raises(ValueError):
        mod.compute(_flat(8, 10.0), bad)


@pytest.mark.parametrize(
    "bad",
    [
        {"window": 4.0},
        {"window": 4.5},
        {"window": math.nan},
        {"within": 0.0},
        {"window": "20"},
    ],
    ids=["window_below_5", "window_non_integral", "window_nan", "within_zero", "window_string_adv"],
)
def test_sweep_param_out_of_bounds_raises(bad):
    with pytest.raises(ValueError):
        sweep_reversal.compute(_sweep_minus(), bad)


def test_sweep_within_one_is_valid_boundary():
    # spec: within integral >= 1 — 1 is the minimum legal value (not an error)
    assert sweep_reversal.compute(_sweep_minus(), {"within": 1.0}) == -1.0


@pytest.mark.parametrize(
    "bad",
    [
        {"mult": 0.0},
        {"mult": -1.0},
        {"mult": math.nan},
        {"mult": "3"},
        {"period": 0.0},
        {"period": 2.5},
        {"period": math.nan},
    ],
    ids=[
        "mult_zero",
        "mult_neg",
        "mult_nan",
        "mult_string_adv",
        "period_zero",
        "period_non_integral",
        "period_nan",
    ],
)
def test_supertrend_param_out_of_bounds_raises(bad):
    with pytest.raises(ValueError):
        supertrend_flip.compute(_up_flip(), bad)


# ---------------- goldens (spec §Prerequisite analytics hands) ----------------


@pytest.mark.parametrize(
    ("mod", "period"),
    [(ema_8, 8), (ema_21, 21), (ema_55, 55)],
    ids=["ema_8", "ema_21", "ema_55"],
)
def test_ema_flat_series_equals_price(mod, period):
    # Hand calc: seed=10, every step alpha*10 + (1-alpha)*10 == 10.0
    # (binary-verified exact for p=8/21/55 — scratch _m47_handcalc.py)
    assert mod.compute(_flat(period, 10.0), {}) == 10.0


def test_ema_period_one_is_last_close():
    # Hand calc: alpha = 2/(1+1) = 1 -> 1*9 + 0*5 = 9.0 exact
    assert ema_8.compute(_bars([(5, 5, 5, 5), (9, 9, 9, 9)]), {"period": 1.0}) == 9.0


def test_ema8_seed_recursion_hand():
    # Hand calc: seed=1 (first close), alpha=2/9 -> (2/9)*2 + (7/9)*1 = 11/9
    # (bit-exact vs literal 11/9 with the ema_20 recursion form — scratch-verified)
    assert ema_8.compute(_bars([(1, 1, 1, 1), (2, 2, 2, 2)]), {}) == 11 / 9


def test_last_close_hand():
    # Hand calc: last bar close 105.5
    assert last_close.compute(_bars([(100, 106, 99, 105.5)]), {}) == 105.5


def test_bar_low_hand():
    # Hand calc: last bar low 99.25
    assert bar_low.compute(_bars([(100, 106, 99.25, 105.5)]), {}) == 99.25


def test_close_position_hand():
    # Hand calc: (7-0)/(10-0) = 0.7 — single correctly-rounded division == 0.7
    assert close_position.compute(_bars([(10, 10, 0, 7)]), {}) == 0.7


def test_close_position_flat_range_canonical_half():
    # Canonical branch: high == low -> 0.5 (no divide-by-zero)
    assert close_position.compute(_bars([(5, 5, 5, 5)]), {}) == 0.5


def test_close_position_bounds_close_at_high_and_low():
    assert close_position.compute(_bars([(9, 10, 8, 10)]), {}) == 1.0
    assert close_position.compute(_bars([(9, 10, 8, 8)]), {}) == 0.0


def test_close_change_hand_signed():
    # Hand calc: 103 - 101 = +2.0 ; 101 - 103 = -2.0
    up = close_change.compute(_bars([(101, 101, 101, 101), (103, 103, 103, 103)]), {})
    down = close_change.compute(_bars([(103, 103, 103, 103), (101, 101, 101, 101)]), {})
    assert up == 2.0
    assert down == -2.0


def test_close_change_single_candle_returns_nan():
    # spec: minimum_history=2; 1 candle -> nan honest refusal
    assert math.isnan(close_change.compute(_bars([(101, 101, 101, 101)]), {}))


def test_rejection_bar_bullish_hand():
    # Hand calc: body |12-10|=2, lower wick 10-6=4 >= 2*2, close 12 >= 6+0.5*6.5
    assert rejection_bar.compute(_bars([(10, 12.5, 6, 12)]), {}) == 1.0


def test_rejection_bar_bearish_hand():
    # Hand calc: body |10-12|=2, upper wick 16-12=4 >= 2*2, close 10 <= 16-4
    assert rejection_bar.compute(_bars([(12, 16, 8, 10)]), {}) == -1.0


def test_rejection_bar_doji_returns_zero():
    # Hand calc: body 0 -> neither shape (doji), 0.0
    assert rejection_bar.compute(_bars([(10, 11, 9, 10)]), {}) == 0.0


def test_rejection_bar_normal_bar_returns_zero():
    # Hand calc: body 0.8; lower wick 1 < 1.6 and upper 0.2 < 1.6 -> neither
    assert rejection_bar.compute(_bars([(10, 11, 9, 10.8)]), {}) == 0.0


def test_rejection_bar_tie_prefers_bullish():
    # Hand calc: body 1; both wicks >= 2 AND close on both half-boundaries;
    # spec pins bullish checked first -> +1.0
    assert rejection_bar.compute(_bars([(12, 16, 10, 13)]), {}) == 1.0


def test_rejection_bar_extreme_scale_adversarial():
    # adversarial (not in spec): 1e12-scale OHLC still classifies by shape
    # Hand calc: body 1e11, lower wick 1e12 >= 2e11, close >= 0.5*h -> +1.0
    bars = _bars([(1e12, 1.1e12, 0.0, 1.1e12)])
    assert rejection_bar.compute(bars, {}) == 1.0


def test_sweep_high_sweep_bearish_confirm_is_short():
    assert sweep_reversal.compute(_sweep_minus(), {}) == -1.0


def test_sweep_low_sweep_bullish_confirm_is_long():
    assert sweep_reversal.compute(_sweep_plus(), {}) == 1.0


def test_sweep_confirm_side_mismatch_returns_zero():
    # bullish confirm demands a LOW sweep; only high sweeps exist -> 0.0
    # (bar21 low 98 equals the window min, so no low sweep anywhere)
    bars = _bars(
        [(99.0, 100.0, 98.0, 99.0)] * 20 + [(99.5, 101.0, 98.0, 99.0), (98.0, 100.5, 98.0, 99.5)]
    )
    assert sweep_reversal.compute(bars, {}) == 0.0


def test_sweep_same_bar_sweep_and_confirm():
    # 21 bars: bar20 sweeps AND is bar i; window bars0..19 is full (j == window)
    bars = _bars([(99.0, 100.0, 98.0, 99.0)] * 20 + [(99.5, 101.0, 98.0, 99.0)])
    assert sweep_reversal.compute(bars, {}) == -1.0


def test_sweep_no_event_returns_zero():
    # highs never exceed the prior max; last bar bearish but no sweep -> 0.0
    bars = _bars([(99.0, 100.0, 98.0, 99.0)] * 24 + [(99.0, 100.0, 98.0, 98.5)])
    assert sweep_reversal.compute(bars, {}) == 0.0


def test_sweep_insufficient_history_returns_nan():
    # i=19 < window=20 -> nan honest refusal (without the guard: j<window for
    # every candidate -> 0.0, so this test distinguishes the spec guard)
    assert math.isnan(sweep_reversal.compute(_flat(20, 99.0), {}))


def test_supertrend_up_flip_hand():
    # Hand calc (period=2, mult=1): TR=[2,3,2], atr=[2,2.5,2.25],
    # U=[11,11,13.25], L=[7,7.5,8.75], dir=[-1,-1,+1] -> fresh up-flip +1.0
    assert supertrend_flip.compute(_up_flip(), {"mult": 1.0, "period": 2.0}) == 1.0


def test_supertrend_down_flip_hand():
    # Hand calc: U=[11,10,9.25], L=[7,7,4.75] (basicL reset: close < L_prev),
    # dir=[+1,+1,-1] -> fresh down-flip -1.0
    assert supertrend_flip.compute(_down_flip(), {"mult": 1.0, "period": 2.0}) == -1.0


def test_supertrend_no_flip_returns_zero():
    # first two bars of the up-flip setup: dir=[-1,-1], no change -> 0.0
    bars = _up_flip()[:2]
    assert supertrend_flip.compute(bars, {"mult": 1.0, "period": 2.0}) == 0.0


def test_supertrend_single_bar_returns_zero():
    # spec: 1-bar input -> 0.0 (no previous bar to flip against)
    assert supertrend_flip.compute(_up_flip()[:1], {"mult": 1.0, "period": 2.0}) == 0.0


def test_supertrend_flat_defaults_never_flip():
    # defaults path (mult=3, period=10): flat series carries one dir -> 0.0
    assert supertrend_flip.compute(_flat(12, 10.0), {}) == 0.0


def test_supertrend_stale_flip_returns_zero():
    # spec "fresh = flip at the LAST bar": dir flips at bar1 (+1), bar2 carries
    # -> stale event -> 0.0 (a stale flip must not retrigger S5 every bar)
    assert supertrend_flip.compute(_stale_flip(), {"mult": 1.0, "period": 2.0}) == 0.0
