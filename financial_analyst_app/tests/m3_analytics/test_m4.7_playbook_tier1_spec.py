"""WHY: M4.7 Step 3 — tests for the 5 playbook Tier-1 strategy files, written
against docs/specs/m4.7_playbook_tier1.spec.md BEFORE any m4.7 strategy impl
exists (Slice 5b; the 10 prerequisite analytics of Slice 5a are already LGTM'd
and implemented). RED at collection: ModuleNotFoundError on the 5 new
strategies/ imports — honest tests-before-code, same house discipline as
test_m4.2_strategy_spec.py / P18 (AGENTS §2 Step 3, no importorskip).

Covers spec §Strategies (per-card gates/trigger/ramp hands with `# Hand calc:`
comments; exact `==` on dyadic values, `pytest.approx(abs=1e-12)` only where
binary representation demands — D1 60.0 and S5 30.0 hands), §Edges (one per
edge: missing/NaN/inf slot, params-None, defaults, unknown keys, per-file
bounds, range invariant, features=None, poison-free recovery), the 12-name
package discovery + real manifest + deriv eligibility (amendments 1/3 lists),
and the human-pinned single-context atomicity test
`test_strategy_slots_share_one_request_context` (spec §Invariants 2026-10-02:
one FeatureRequest -> ONE provider call, ONE timeframe, every slot of A1's
11-slot set derived from the SAME candle list / final bar).

No money path in evaluate (signed strength only) — hand calcs are exact
float strengths per the m4.2 house ramp, no Decimal/ledger math required.
"""
from __future__ import annotations

import inspect
import math
import pkgutil
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

import modules.m3_analytics.strategies as strategies_pkg
from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.feature_engine import (
    FeatureEngine,
    FeatureRequest,
    StaticCandleProvider,
)
from modules.m3_analytics.engine.profiles import Profiles

# Direct impl import — fails collection until Step 5 (honest RED).
from modules.m3_analytics.strategies import (
    failed_breakout_sweep_reversal,
    n_bar_donchian_breakout,
    supertrend_adx_filter,
    trend_pullback_ema_stack_rsi,
    trend_pullback_to_dynamic_support,
)
from shared.contracts.market_data import Candle, CapabilityStatus

A1 = trend_pullback_to_dynamic_support
B1 = n_bar_donchian_breakout
D1 = failed_breakout_sweep_reversal
S1 = trend_pullback_ema_stack_rsi
S5 = supertrend_adx_filter
MODULES = (A1, B1, D1, S1, S5)
BY_NAME = {mod.NAME: mod for mod in MODULES}
CONFIG = Path(__file__).resolve().parents[2] / "config" / "capabilities.yaml"
ASSETS_DIR = Path(__file__).resolve().parents[2] / "config" / "assets"

# STEP-3 CORRECTION (spec amendment 1, 2026-10-02): `trend_following` sorts
# BEFORE `trend_pullback_*` — `f` (0x66) < `p` (0x70); verified with sorted().
_EXPECTED_12 = [
    "donchian_breakout",
    "failed_breakout_sweep_reversal",
    "mean_reversion",
    "microstructure_flow",
    "n_bar_donchian_breakout",
    "pullback",
    "regime_based",
    "rsi_overbought_oversold",
    "supertrend_adx_filter",
    "trend_following",
    "trend_pullback_ema_stack_rsi",
    "trend_pullback_to_dynamic_support",
]

# Complete finite feature dicts — one golden path per card (all gates + trigger
# hold unless a test overrides one leg).
_A1_FULL = {
    "state": 1.0,
    "adx": 50.0,
    "er": 0.5,
    "r2": 0.8,
    "atr": 1.0,
    "ema": 100.0,
    "rsi": 60.0,
    "swlow": 95.0,
    "close": 100.0,
    "low": 96.0,
    "cpos": 0.7,
}
_B1 = {"atr_rk": 40.0, "chan": 125.0, "er": 0.5}
_D1 = {"sweep": 1.0, "atr_rk": 75.0}
_S1_LONG = {
    "adx": 30.0,
    "e8": 101.0,
    "e21": 100.5,
    "e55": 100.0,
    "rsi": 45.0,
    "atr": 1.0,
    "close": 100.6,
    "chg": 0.5,
    "rej": 0.0,
}
_S1_SHORT = {
    "adx": 30.0,
    "e8": 99.0,
    "e21": 99.5,
    "e55": 100.0,
    "rsi": 50.0,
    "atr": 1.0,
    "close": 99.0,
    "chg": -0.3,
    "rej": 0.0,
}
_S1_NEAR = {
    "adx": 30.0,
    "e8": 100.25,
    "e21": 100.1,
    "e55": 100.0,
    "rsi": 60.0,
    "atr": 1.0,
    "close": 100.3,
    "chg": -0.2,
    "rej": 1.0,
}
_S5 = {"flip": 1.0, "adx": 30.0}

VALID = {
    A1.NAME: _A1_FULL,
    B1.NAME: _B1,
    D1.NAME: _D1,
    S1.NAME: _S1_LONG,
    S5.NAME: _S5,
}
SLOT_OF = {mod.NAME: next(iter(mod.FEATURE_SLOTS)) for mod in MODULES}


# --- contract: package shape, names, signature, defaults, slots, isolation ---


def test_discovery_exactly_twelve_strategy_modules():
    names = sorted(
        info.name for info in pkgutil.iter_modules(strategies_pkg.__path__) if not info.ispkg
    )
    assert names == _EXPECTED_12


def test_contract_name_snake_case_and_stem_match():
    for mod in MODULES:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", mod.NAME)  # registry _SNAKE, no change
        assert mod.__name__.rsplit(".", 1)[-1] == mod.NAME  # NAME == module stem


def test_contract_evaluate_signature_exact():
    for mod in MODULES:
        assert list(inspect.signature(mod.evaluate).parameters) == ["features", "params"]


def test_contract_param_defaults_finite_nonempty_floats():
    for mod in MODULES:
        assert mod.PARAM_DEFAULTS
        for key, value in mod.PARAM_DEFAULTS.items():
            assert isinstance(key, str) and key
            assert isinstance(value, float) and math.isfinite(value)


def test_contract_feature_slots_distinct_analytics():
    for mod in MODULES:
        assert mod.FEATURE_SLOTS
        analytics: list[str] = []
        for slot, spec in mod.FEATURE_SLOTS.items():
            assert isinstance(slot, str) and slot
            assert isinstance(spec, tuple) and len(spec) == 2
            name, overrides = spec
            assert isinstance(name, str) and name
            assert isinstance(overrides, dict)
            analytics.append(name)
        assert len(analytics) == len(set(analytics))  # engine: one value per analytic


def test_feature_slots_exact_spec_mapping():
    expected = {
        A1.NAME: {
            "state": "market_state",
            "adx": "adx_14",
            "er": "efficiency_ratio",
            "r2": "ols_r_squared",
            "atr": "atr_14",
            "ema": "ema_20",
            "rsi": "rsi_14",
            "swlow": "swing_low",
            "close": "last_close",
            "low": "bar_low",
            "cpos": "close_position",
        },
        B1.NAME: {
            "atr_rk": "atr_percentile_rank",
            "chan": "donchian_20",
            "er": "efficiency_ratio",
        },
        D1.NAME: {
            "sweep": "sweep_reversal",
            "atr_rk": "atr_percentile_rank",
        },
        S1.NAME: {
            "adx": "adx_14",
            "e8": "ema_8",
            "e21": "ema_21",
            "e55": "ema_55",
            "rsi": "rsi_14",
            "atr": "atr_14",
            "close": "last_close",
            "chg": "close_change",
            "rej": "rejection_bar",
        },
        S5.NAME: {"flip": "supertrend_flip", "adx": "adx_14"},
    }
    for mod in MODULES:
        got = {slot: spec[0] for slot, spec in mod.FEATURE_SLOTS.items()}
        assert got == expected[mod.NAME]
        for spec in mod.FEATURE_SLOTS.values():
            assert spec[1] == {}  # no per-slot overrides: analytics defaults are right


def test_contract_no_isolation_attribute_leak():
    for mod in MODULES:
        for attr in ("provider", "clock", "as_of", "candles"):
            assert not hasattr(mod, attr)


def test_evaluate_deterministic_same_inputs_twice():
    for mod in MODULES:
        features = VALID[mod.NAME]
        first = mod.evaluate(features, {})
        assert mod.evaluate(features, {}) == first


# --- input contract: missing/NaN/inf slot, params=None, defaults, unknown ---


def test_missing_each_slot_valueerror_names_slot():
    for mod in MODULES:
        full = VALID[mod.NAME]
        for slot in mod.FEATURE_SLOTS:
            partial = {key: value for key, value in full.items() if key != slot}
            with pytest.raises(ValueError, match=slot):
                mod.evaluate(partial, {})


def test_nan_slot_valueerror_every_slot():
    for mod in MODULES:
        for slot in mod.FEATURE_SLOTS:
            bad = {**VALID[mod.NAME], slot: float("nan")}
            with pytest.raises(ValueError, match=slot):
                mod.evaluate(bad, {})


def test_inf_slot_valueerror_every_slot():
    for mod in MODULES:
        for slot in mod.FEATURE_SLOTS:
            bad = {**VALID[mod.NAME], slot: float("inf")}
            with pytest.raises(ValueError, match=slot):
                mod.evaluate(bad, {})


def test_string_slot_valueerror_every_slot():
    # Spec test plan "missing/nan/inf/string slot" — hostile type must fail
    # typed, never coerce (ValueError before any float() conversion).
    for mod in MODULES:
        for slot in mod.FEATURE_SLOTS:
            bad = {**VALID[mod.NAME], slot: "boom"}
            with pytest.raises(ValueError, match=slot):
                mod.evaluate(bad, {})


def test_features_none_valueerror():
    for mod in MODULES:
        with pytest.raises(ValueError, match="features"):
            mod.evaluate(None, {})  # type: ignore[arg-type]


def test_params_none_valueerror():
    for mod in MODULES:
        with pytest.raises(ValueError, match="params"):
            mod.evaluate(VALID[mod.NAME], None)  # type: ignore[arg-type]


def test_params_empty_equals_explicit_defaults():
    for mod in MODULES:
        assert mod.evaluate(VALID[mod.NAME], {}) == mod.evaluate(
            VALID[mod.NAME], dict(mod.PARAM_DEFAULTS)
        )


def test_param_defaults_match_spec_pins():
    assert A1.PARAM_DEFAULTS == {
        "adx_min": 25.0,
        "er_min": 0.3,
        "r2_min": 0.6,
        "touch_atr": 0.25,
        "cpos_min": 0.4,
        "rsi_min": 40.0,
    }
    assert B1.PARAM_DEFAULTS == {"atr_rk_min": 30.0, "er_min": 0.25}
    assert D1.PARAM_DEFAULTS == {"atr_rk_min": 50.0}
    assert S1.PARAM_DEFAULTS == {
        "adx_min": 25.0,
        "rsi_lo": 40.0,
        "rsi_hi": 55.0,
        "short_offset": 5.0,
        "near_atr": 0.3,
        "stack_full_pct": 0.5,
    }
    assert S5.PARAM_DEFAULTS == {"adx_min": 25.0, "adx_full": 50.0}


def test_unknown_param_keys_ignored():
    for mod in MODULES:
        baseline = mod.evaluate(VALID[mod.NAME], {})
        assert mod.evaluate(VALID[mod.NAME], {"bogus_key": 123.0}) == baseline


# --- per-file out-of-bounds params -> ValueError naming the param ---


def test_a1_params_out_of_bounds_valueerror():
    for bad in (
        {"adx_min": -1.0},
        {"adx_min": 101.0},
        {"adx_min": float("nan")},
        {"er_min": -0.1},
        {"er_min": 1.0},
        {"r2_min": -0.1},
        {"r2_min": 1.0},
        {"touch_atr": 0.0},
        {"touch_atr": -0.5},
        {"cpos_min": -0.1},
        {"cpos_min": 1.5},
        {"rsi_min": -1.0},
        {"rsi_min": 101.0},
    ):
        with pytest.raises(ValueError, match=next(iter(bad))):
            A1.evaluate(dict(_A1_FULL), bad)


def test_b1_params_out_of_bounds_valueerror():
    for bad in (
        {"atr_rk_min": -1.0},
        {"atr_rk_min": 101.0},
        {"er_min": -0.1},
        {"er_min": 1.0},
    ):
        with pytest.raises(ValueError, match=next(iter(bad))):
            B1.evaluate(dict(_B1), bad)


def test_d1_params_out_of_bounds_valueerror():
    for bad in ({"atr_rk_min": -1.0}, {"atr_rk_min": 101.0}):
        with pytest.raises(ValueError, match="atr_rk_min"):
            D1.evaluate(dict(_D1), bad)


def test_s1_params_out_of_bounds_valueerror():
    for bad in (
        {"adx_min": -1.0},
        {"adx_min": 101.0},
        {"rsi_lo": -1.0},
        {"rsi_lo": 101.0},
        {"rsi_hi": 40.0},  # must be > rsi_lo (default 40)
        {"rsi_hi": 101.0},
        {"short_offset": -1.0},
        {"rsi_hi": 60.0, "short_offset": 50.0},  # rsi_hi + offset = 110 > 100
        {"near_atr": 0.0},
        {"stack_full_pct": 0.0},
    ):
        with pytest.raises(ValueError, match=next(iter(bad))):
            S1.evaluate(dict(_S1_LONG), bad)


def test_s5_params_out_of_bounds_valueerror():
    for bad in (
        {"adx_min": -1.0},
        {"adx_min": 101.0},
        {"adx_full": 25.0},  # must be > adx_min (default 25)
        {"adx_full": 101.0},
        {"adx_min": 60.0},  # >= default adx_full 50 -> cross-check names adx_full
    ):
        with pytest.raises(ValueError, match=next(iter(bad))):
            S5.evaluate(dict(_S5), bad)


# --- A1 goldens + gates + trigger legs ---


def test_a1_goldens():
    # Hand calc: all gates pass, close==ema -> dist 0 -> progress 1.0
    # -> +1 * (0.5 + 0.5*1) = +1.0 exact
    assert A1.evaluate(dict(_A1_FULL), {}) == 1.0
    # Hand calc: state -1, dist 0 -> progress 1.0 -> -1.0 exact (sign mirror)
    assert A1.evaluate({**_A1_FULL, "state": -1.0}, {}) == -1.0
    # Hand calc: dist = touch_atr*atr = 0.25 -> progress 1 - min(1, 0.25/0.25)
    # = 0 -> strength 0.5 + 0 = +0.5 exact (touch exactly at threshold)
    assert A1.evaluate({**_A1_FULL, "close": 100.25}, {}) == 0.5
    # Hand calc: dist = 0.125*atr -> progress 1 - 0.125/0.25 = 0.5
    # -> 0.5 + 0.25 = +0.75 exact
    assert A1.evaluate({**_A1_FULL, "close": 100.125}, {}) == 0.75
    # Hand calc: atr=0 + close==ema -> exact touch, dist==0 special case
    # (no 0/0) -> progress 1.0 -> +1.0 exact
    assert A1.evaluate({**_A1_FULL, "atr": 0.0}, {}) == 1.0


def test_a1_gate_strict_boundaries_fail():
    # Boundary equals fail everywhere the gate is strict (spec §A1).
    assert A1.evaluate({**_A1_FULL, "state": 0.0}, {}) == 0.0  # flat state
    assert A1.evaluate({**_A1_FULL, "adx": 25.0}, {}) == 0.0  # adx == adx_min
    assert A1.evaluate({**_A1_FULL, "er": 0.3}, {}) == 0.0  # er == er_min (hand)
    assert A1.evaluate({**_A1_FULL, "r2": 0.6}, {}) == 0.0  # r2 == r2_min


def test_a1_trigger_legs_are_and():
    # touch miss: dist 0.5*atr > 0.25*atr -> no signal even with the rest held
    assert A1.evaluate({**_A1_FULL, "close": 100.5}, {}) == 0.0
    # reversal leg fails: cpos 0.3 < cpos_min 0.4 (hand)
    assert A1.evaluate({**_A1_FULL, "cpos": 0.3}, {}) == 0.0
    # reversal leg fails: low == swlow is NOT > (strict)
    assert A1.evaluate({**_A1_FULL, "low": 95.0}, {}) == 0.0
    # rsi leg fails: rsi == rsi_min is NOT > (hand)
    assert A1.evaluate({**_A1_FULL, "rsi": 40.0}, {}) == 0.0


# --- B1 goldens + gates ---


def test_b1_goldens():
    # Hand calc: atr_rk 40>30, er 0.5>0.25, chan 125 -> progress (125-100)/100
    # = 0.25 -> 0.5 + 0.125 = +0.625 exact
    assert B1.evaluate(dict(_B1), {}) == 0.625
    # Hand calc: chan 150 -> progress 0.5 -> 0.75 exact
    assert B1.evaluate({**_B1, "chan": 150.0}, {}) == 0.75
    # Hand calc: chan 200 -> progress 1.0 -> +1.0 exact
    assert B1.evaluate({**_B1, "chan": 200.0}, {}) == 1.0
    # Hand calc: chan -50 -> short progress (0-(-50))/100 = 0.5 -> -0.75 exact
    assert B1.evaluate({**_B1, "chan": -50.0}, {}) == -0.75
    # Hand calc: chan -100 -> short progress 1.0 -> -1.0 exact
    assert B1.evaluate({**_B1, "chan": -100.0}, {}) == -1.0


def test_b1_gate_strict_boundaries_fail():
    assert B1.evaluate({**_B1, "atr_rk": 30.0}, {}) == 0.0  # atr_rk == min (hand)
    assert B1.evaluate({**_B1, "er": 0.25}, {}) == 0.0  # er == er_min (hand)
    assert B1.evaluate({**_B1, "chan": 100.0}, {}) == 0.0  # inside: not > 100 (hand)
    assert B1.evaluate({**_B1, "chan": 0.0}, {}) == 0.0  # inside: not < 0
    assert B1.evaluate({**_B1, "chan": 50.0}, {}) == 0.0  # deep inside the channel


# --- D1 goldens + gates ---


def test_d1_goldens():
    # Hand calc: atr_rk 75>50, sweep +1 -> progress (75-50)/(100-50) = 0.5
    # -> 0.5 + 0.25 = +0.75 exact
    assert D1.evaluate(dict(_D1), {}) == 0.75
    # Hand calc: atr_rk 100 -> progress 50/50 = 1.0 -> +1.0 exact
    assert D1.evaluate({**_D1, "atr_rk": 100.0}, {}) == 1.0
    # Hand calc: atr_rk 60 -> progress 10/50 = 0.2 -> 0.5 + 0.1 = 0.6;
    # 0.2 is not dyadic -> approx (spec test plan allows where binary demands)
    assert D1.evaluate({**_D1, "atr_rk": 60.0}, {}) == pytest.approx(0.6, abs=1e-12)
    # Hand calc: short mirror atr_rk 75, sweep -1 -> -0.75 exact
    assert D1.evaluate({**_D1, "sweep": -1.0}, {}) == -0.75


def test_d1_gate_and_sweep_none():
    assert D1.evaluate({**_D1, "atr_rk": 50.0}, {}) == 0.0  # boundary (hand)
    assert D1.evaluate({**_D1, "sweep": 0.0}, {}) == 0.0  # no event (hand)
    # Just above the strict gate: progress (51-50)/50 = 0.02 -> ~0.51
    assert D1.evaluate({**_D1, "atr_rk": 51.0}, {}) == pytest.approx(0.51, abs=1e-12)


# --- S1 goldens + gates + trigger ---


def test_s1_goldens_long_short_and_near_path():
    # Hand calc: long full stack 101/100.5/100, rsi 45 in [40,55], chg 0.5
    # > 0 -> spread 0.01 / (0.5/100) = 2 -> clamp 1.0 -> +1.0 exact
    assert S1.evaluate(dict(_S1_LONG), {}) == 1.0
    # Hand calc: near path — rsi 60 outside [40,55] but close 100.3 <=
    # e21 100.1 + 0.3*atr; chg -0.2 fails tick but rej +1 holds;
    # spread 0.0025 / (0.5/100) = 0.5 -> 0.5 + 0.25 = +0.75 exact
    assert S1.evaluate(dict(_S1_NEAR), {}) == 0.75
    # Hand calc: short stack 99/99.5/100, rsi 50 in [45,60], chg -0.3 < 0
    # -> spread 0.01 / 0.005 = 2 -> clamp -> -1.0 exact
    assert S1.evaluate(dict(_S1_SHORT), {}) == -1.0


def test_s1_rsi_band_edges_inclusive():
    # Band is closed [rsi_lo, rsi_hi] / [lo+off, hi+off] (spec §S1).
    assert S1.evaluate({**_S1_LONG, "rsi": 40.0}, {}) == 1.0  # long lower edge
    assert S1.evaluate({**_S1_LONG, "rsi": 55.0}, {}) == 1.0  # long upper edge
    assert S1.evaluate({**_S1_SHORT, "rsi": 45.0}, {}) == -1.0  # short lower edge
    assert S1.evaluate({**_S1_SHORT, "rsi": 60.0}, {}) == -1.0  # short upper edge


def test_s1_gate_adx_and_stack():
    assert S1.evaluate({**_S1_LONG, "adx": 25.0}, {}) == 0.0  # adx == adx_min
    # Neither stack: e21 above both ends -> no long, no short -> 0.0
    assert S1.evaluate({**_S1_LONG, "e8": 100.0, "e21": 100.5}, {}) == 0.0
    # Mixed short stack but positive-price long shape mismatch -> 0.0
    assert S1.evaluate({**_S1_LONG, "e8": 100.0, "e21": 100.5, "e55": 99.5}, {}) == 0.0


def test_s1_trigger_legs_are_and():
    # No trigger: rsi 70 outside band, close 110 far above e21+0.3*atr,
    # chg 0 (no tick), rej 0 (no rejection) -> 0.0
    no_trigger = {**_S1_LONG, "rsi": 70.0, "close": 110.0, "chg": 0.0, "rej": 0.0}
    assert S1.evaluate(no_trigger, {}) == 0.0
    # Tick leg alone without near/pullback is still 0.0 (leg 1 fails)
    no_near = {**_S1_LONG, "rsi": 70.0, "close": 110.0, "chg": 0.5, "rej": 0.0}
    assert S1.evaluate(no_near, {}) == 0.0


def test_s1_e55_zero_valueerror():
    # Degenerate price: progress divides by |e55| -> ValueError, never a crash.
    with pytest.raises(ValueError, match="e55"):
        S1.evaluate({**_S1_LONG, "e55": 0.0}, {})


# --- S5 goldens + gates ---


def test_s5_goldens():
    # Hand calc: flip +1, adx 30>25 -> progress (30-25)/(50-25) = 0.2
    # -> 0.5 + 0.1 = 0.6; 0.2 not dyadic -> approx
    assert S5.evaluate(dict(_S5), {}) == pytest.approx(0.6, abs=1e-12)
    # Hand calc: adx 50 -> progress 25/25 = 1.0 -> +1.0 exact
    assert S5.evaluate({**_S5, "adx": 50.0}, {}) == 1.0
    # Hand calc: adx 75 -> progress 2.0 -> clamped -> +1.0 exact
    assert S5.evaluate({**_S5, "adx": 75.0}, {}) == 1.0
    # Hand calc: flip -1, adx 30 -> -0.6 (same non-dyadic progress, short)
    assert S5.evaluate({**_S5, "flip": -1.0}, {}) == pytest.approx(-0.6, abs=1e-12)


def test_s5_gate_and_flip_boundaries():
    assert S5.evaluate({**_S5, "adx": 25.0}, {}) == 0.0  # adx == adx_min (hand)
    assert S5.evaluate({**_S5, "flip": 0.0}, {}) == 0.0  # no fresh flip (hand)


# --- range invariant + adversarial ---


def test_range_invariant_extreme_inputs():
    extreme = {
        A1.NAME: {
            "state": 1.0,
            "adx": 1e9,
            "er": 1e9,
            "r2": 1e9,
            "atr": 1e9,
            "ema": 1e12,
            "rsi": 1e9,
            "swlow": -1e9,
            "close": 1e12,
            "low": 1e9,
            "cpos": 1e9,
        },
        B1.NAME: {"atr_rk": 1e9, "chan": 1e9, "er": 1e9},
        D1.NAME: {"sweep": 1.0, "atr_rk": 1e9},
        S1.NAME: {
            "adx": 1e9,
            "e8": 1e12,
            "e21": 5e11,
            "e55": 1.0,
            "rsi": 45.0,
            "atr": 1e9,
            "close": 1e12,
            "chg": 1e9,
            "rej": 0.0,
        },
        S5.NAME: {"flip": 1.0, "adx": 1e9},
    }
    for mod in MODULES:
        value = mod.evaluate(extreme[mod.NAME], {})
        assert -1.0 <= value <= 1.0, f"{mod.NAME}: {value!r} out of range"


def test_adversarial_extra_feature_keys_ignored():
    features = {**_A1_FULL, "injected": -99.0, "unrelated_slot": 42.0}
    assert A1.evaluate(features, {}) == A1.evaluate(dict(_A1_FULL), {})


def test_adversarial_valueerror_does_not_poison_next_call():
    for mod in MODULES:
        with pytest.raises(ValueError):
            mod.evaluate({}, {})
        assert -1.0 <= mod.evaluate(VALID[mod.NAME], {}) <= 1.0


# --- real capabilities.yaml manifest + deriv eligibility ---


def test_manifest_strategies_exactly_twelve_keys():
    manifest = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["strategies"]
    assert sorted(manifest) == _EXPECTED_12


def test_manifest_requires_match_slot_unions_new_five():
    manifest = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["strategies"]
    for mod in MODULES:
        slot_union = {name for name, _ in mod.FEATURE_SLOTS.values()}
        assert set(manifest[mod.NAME]["requires_analytics"]) == slot_union


def test_deriv_eligibility_all_five_true():
    registry = CapabilityRegistry.from_yaml(CONFIG)
    for mod in MODULES:
        eligible, reason = registry.is_strategy_eligible(mod.NAME, "deriv")
        assert eligible is True, f"{mod.NAME}: {reason}"
        assert reason == "eligible"


def test_unknown_strategy_still_typed_false():
    registry = CapabilityRegistry.from_yaml(CONFIG)
    eligible, reason = registry.is_strategy_eligible("no_such_strategy", "deriv")
    assert eligible is False
    assert reason.startswith("unknown_strategy")


# --- single-context atomicity (human 2026-10-02, "the most important thing") ---


def _context_candles(count: int) -> list[Candle]:
    """Deterministic 1m walk (sine + drift): pivots, TR, RSI and Syy all stay
    non-degenerate so every A1 slot computes a finite value on 300 bars AND on
    the engine's shared 50-bar window (ols_r_squared min history drives it) —
    cycle /3 (~19 bars) guarantees >=2 confirmed strict pivot highs AND lows
    inside that window for market_state, else it honestly returns nan."""
    end = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    out: list[Candle] = []
    for i in range(count):
        close = 100.0 + 2.0 * math.sin(i / 3.0) + 0.02 * i
        start = end - timedelta(minutes=count - i)
        out.append(
            Candle(
                open=close,
                high=close + 0.4,
                low=close - 0.4,
                close=close,
                interval="1m",
                interval_start=start,
                interval_end=start + timedelta(minutes=1),
            )
        )
    return out


class _CountingProvider:
    """CandleProvider wrapper recording every get_candles call — the
    single-context proof (exactly ONE call for the whole slot set)."""

    def __init__(self, inner: StaticCandleProvider) -> None:
        self._inner = inner
        self.calls: list[tuple] = []

    async def get_candles(
        self,
        instrument_id: str,
        timeframe: str,
        window_start: datetime,
        window_end: datetime,
        as_of: datetime,
        broker: str,
    ) -> list[Candle]:
        self.calls.append((instrument_id, timeframe, window_start, window_end, as_of, broker))
        return await self._inner.get_candles(
            instrument_id, timeframe, window_start, window_end, as_of, broker
        )


async def test_strategy_slots_share_one_request_context():
    # One FeatureRequest -> ONE resolved TF + ONE window + ONE candle fetch,
    # and every A1 slot value derives from that identical list / final bar
    # (feature_engine.py: resolve once :201-204, single fetch :208-213,
    # same list per slot :247, resolved_tf stamped :265, as_of in cache :325).
    candles = _context_candles(300)
    counting = _CountingProvider(StaticCandleProvider(candles))
    engine = FeatureEngine(
        provider=counting,
        capability_registry=CapabilityRegistry.from_yaml(CONFIG),
        registry=None,
        resolver=None,
        cache=None,
        profiles=Profiles.from_dir(ASSETS_DIR),
    )
    request = FeatureRequest(
        instrument_id="FX.EURUSD",
        broker="deriv",
        timeframe="1m",
        features=tuple(name for name, _ in A1.FEATURE_SLOTS.values()),
        as_of=candles[-1].interval_end,
        asset_class="forex",
    )
    res = await engine.get_features(request)

    assert len(counting.calls) == 1, f"per-slot fetch would break atomicity: {counting.calls}"
    instrument_id, tf, _window_start, _window_end, req_as_of, _broker = counting.calls[0]
    assert instrument_id == "FX.EURUSD"
    assert tf == "1m" == res.timeframe  # ONE resolved timeframe everywhere
    assert req_as_of == request.as_of  # ONE PIT instant for every slot
    assert res.capability_status is CapabilityStatus.FULLY_SUPPORTED
    assert "INSUFFICIENT" not in (res.capability_warning or "")
    slot_values = {name for name, _ in A1.FEATURE_SLOTS.values()}
    assert set(res.features) == slot_values  # all 11 slots, same request

    # Same-final-bar proof: three slot analytics keyed to bar i = -1 agree
    # exactly with that one candle (a per-slot window would desynchronize them).
    last = candles[-1]
    assert res.features["last_close"] == last.close
    assert res.features["bar_low"] == last.low
    assert res.features["close_position"] == (last.close - last.low) / (last.high - last.low)
