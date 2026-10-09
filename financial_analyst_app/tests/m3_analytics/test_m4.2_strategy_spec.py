"""WHY: M4.2 Step 3 — tests for the 7 strategy files, written against
docs/specs/m4.2_strategy_files.spec.md BEFORE any strategies/ impl exists
(blanket human LGTM 2026-09-25). RED at collection: ModuleNotFoundError on the
strategies/ imports — honest tests-before-code, same discipline as P18.
"""
from __future__ import annotations

import inspect
import math
import pkgutil
import re
from pathlib import Path

import pytest
import yaml

import modules.m3_analytics.strategies as strategies_pkg
from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.strategies import (
    donchian_breakout,
    mean_reversion,
    microstructure_flow,
    pullback,
    regime_based,
    rsi_overbought_oversold,
    trend_following,
)

MODULES = (
    trend_following,
    rsi_overbought_oversold,
    mean_reversion,
    donchian_breakout,
    pullback,
    regime_based,
    microstructure_flow,
)
BY_NAME = {mod.NAME: mod for mod in MODULES}
SLOT_OF = {mod.NAME: next(iter(mod.FEATURE_SLOTS)) for mod in MODULES}
VALID = {
    "trend_following": {"fast": 101.0, "slow": 100.0, "rsi": 65.0},
    "rsi_overbought_oversold": {"rsi": 85.0},
    "mean_reversion": {"z": 3.0},
    "donchian_breakout": {"chan": 150.0, "chop": 20.0},
    "pullback": {"trend": 30.0, "entry": -2.0},
    "regime_based": {"chop": 30.0, "hurst": 0.7},
    "microstructure_flow": {"imb": 1.0},
}
CONFIG = Path(__file__).resolve().parents[2] / "config" / "capabilities.yaml"


def _load_manifest() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["strategies"]


# --- contract: package shape, names, signature, defaults, slots, isolation ---

def test_discovery_exactly_twelve_strategy_modules():
    # m4.7 spec DECLARED AMENDMENT 3 (2026-10-02): 7 legacy + 5 Tier-1 cards,
    # same corrected sorted list as amendment 1 (trend_following sorts first of
    # the trend_* names: 'f' 0x66 < 'p' 0x70).
    names = sorted(
        info.name for info in pkgutil.iter_modules(strategies_pkg.__path__) if not info.ispkg
    )
    assert names == [
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
    assert set(BY_NAME) <= set(names)  # legacy 7 all still discovered


def test_contract_name_snake_case_and_capabilities_key():
    manifest = _load_manifest()
    for mod in MODULES:
        assert re.fullmatch(r"[a-z][a-z0-9_]*", mod.NAME)
        assert mod.NAME in manifest


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


def test_contract_no_isolation_attribute_leak():
    for mod in MODULES:
        for attr in ("provider", "clock", "as_of", "candles"):
            assert not hasattr(mod, attr)


def test_evaluate_deterministic_same_inputs_twice():
    for mod in MODULES:
        features = VALID[mod.NAME]
        first = mod.evaluate(features, {})
        assert mod.evaluate(features, {}) == first


# --- input contract: missing/NaN slot, params=None, unknown keys ---

def test_missing_slot_feature_valueerror_names_slot():
    for mod in MODULES:
        with pytest.raises(ValueError, match=SLOT_OF[mod.NAME]):
            mod.evaluate({}, {})


def test_nan_slot_feature_valueerror():
    slot = SLOT_OF["trend_following"]
    with pytest.raises(ValueError, match=slot):
        trend_following.evaluate({"fast": float("nan"), "slow": 100.0, "rsi": 65.0}, {})


def test_inf_slot_feature_valueerror():
    with pytest.raises(ValueError, match="imb"):
        microstructure_flow.evaluate({"imb": float("inf")}, {})


def test_params_none_valueerror():
    with pytest.raises(ValueError, match="params"):
        rsi_overbought_oversold.evaluate({"rsi": 50.0}, None)  # type: ignore[arg-type]


def test_params_empty_uses_defaults():
    # Hand calc trend: fast=101 slow=100 gap 1% = full (div 1.0%) -> progress 1.0
    # -> strength 0.5 + 0.5*1 = 1.0 exact; rsi 65 >= threshold 60 -> LONG.
    assert trend_following.evaluate({"fast": 101.0, "slow": 100.0, "rsi": 65.0}, {}) == 1.0


def test_unknown_param_keys_ignored():
    # Hand calc mean-rev: z=3, entry 2 -> progress (3-2)/2 = 0.5 -> 0.5+0.25 = 0.75 short
    features = {"z": 3.0}
    baseline = mean_reversion.evaluate(features, {})
    assert mean_reversion.evaluate(features, {"bogus": 123.0, "entry": 2.0}) == baseline


def test_params_bounds_valueerror():
    trend_features = {"fast": 101.0, "slow": 100.0, "rsi": 65.0}
    with pytest.raises(ValueError, match="rsi_threshold"):
        trend_following.evaluate(trend_features, {"rsi_threshold": 50.0})  # must be > 50
    with pytest.raises(ValueError, match="rsi_threshold"):
        trend_following.evaluate(trend_features, {"rsi_threshold": float("nan")})
    with pytest.raises(ValueError, match="chop_max"):
        donchian_breakout.evaluate({"chan": 150.0, "chop": 20.0}, {"chop_max": 150.0})
    with pytest.raises(ValueError, match="entry"):
        mean_reversion.evaluate({"z": 3.0}, {"entry": 0.0})
    with pytest.raises(ValueError, match="long"):
        microstructure_flow.evaluate({"imb": 0.8}, {"long": 1.0})
    with pytest.raises(ValueError, match="hurst_trend_min"):
        regime_based.evaluate(
            {"chop": 30.0, "hurst": 0.7},
            {"hurst_trend_min": 0.3, "hurst_chop_max": 0.5},  # zones must not overlap
        )
    with pytest.raises(ValueError, match="oversold"):
        rsi_overbought_oversold.evaluate(
            {"rsi": 85.0}, {"overbought": 70.0, "oversold": 70.0}
        )
    with pytest.raises(ValueError, match="adx_min"):
        pullback.evaluate({"trend": 30.0, "entry": -2.0}, {"adx_min": 101.0})
    with pytest.raises(ValueError, match="pullback"):
        pullback.evaluate({"trend": 30.0, "entry": -2.0}, {"pullback": 0.0})


# --- per-strategy goldens (house ramp 0.5 + 0.5*min(1, progress)) ---

def test_trend_following_goldens():
    # Hand calc: fast 101 > slow 100 gap 1% = full (divergence_full_pct 1.0) -> +1.0
    assert trend_following.evaluate({"fast": 101.0, "slow": 100.0, "rsi": 65.0}, {}) == 1.0
    # Hand calc: fast 99 < slow 100 gap 1% full, rsi 35 <= 100-60=40 -> -1.0
    assert trend_following.evaluate({"fast": 99.0, "slow": 100.0, "rsi": 35.0}, {}) == -1.0
    # Hand calc: gap 0.5% of full 1% -> progress 0.5 -> strength 0.5+0.25 = 0.75 exact
    assert trend_following.evaluate({"fast": 100.5, "slow": 100.0, "rsi": 65.0}, {}) == 0.75
    # no signal: rsi 50 below long threshold 60
    assert trend_following.evaluate({"fast": 101.0, "slow": 100.0, "rsi": 50.0}, {}) == 0.0
    # no signal: flat gap fast == slow
    assert trend_following.evaluate({"fast": 100.0, "slow": 100.0, "rsi": 65.0}, {}) == 0.0


def test_rsi_overbought_oversold_goldens():
    # Hand calc: rsi 85, ob 70 -> progress (85-70)/(100-70) = 0.5 -> 0.5+0.25 = -0.75
    assert rsi_overbought_oversold.evaluate({"rsi": 85.0}, {}) == -0.75
    # Hand calc: rsi 15, os 30 -> progress (30-15)/30 = 0.5 -> +0.75
    assert rsi_overbought_oversold.evaluate({"rsi": 15.0}, {}) == 0.75
    # Hand calc: rsi 100 -> progress 30/30 = 1.0 -> -1.0; rsi 0 -> +1.0
    assert rsi_overbought_oversold.evaluate({"rsi": 100.0}, {}) == -1.0
    assert rsi_overbought_oversold.evaluate({"rsi": 0.0}, {}) == 1.0
    assert rsi_overbought_oversold.evaluate({"rsi": 50.0}, {}) == 0.0


def test_mean_reversion_goldens():
    # Hand calc: z=3 entry 2 -> fade over-extension: progress (3-2)/2 = 0.5 -> -0.75
    assert mean_reversion.evaluate({"z": 3.0}, {}) == -0.75
    assert mean_reversion.evaluate({"z": -3.0}, {}) == 0.75
    # Hand calc: z=4 -> progress (4-2)/2 = 1.0 -> -1.0 exact
    assert mean_reversion.evaluate({"z": 4.0}, {}) == -1.0
    # threshold floor: z=2 -> progress 0 -> 0.5 (trigger, not flat)
    assert mean_reversion.evaluate({"z": 2.0}, {}) == -0.5
    assert mean_reversion.evaluate({"z": 1.0}, {}) == 0.0


def test_donchian_breakout_goldens():
    # Hand calc: chan 150, chop 20 < 38 trending -> progress (150-100)/100 = 0.5 -> 0.75
    assert donchian_breakout.evaluate({"chan": 150.0, "chop": 20.0}, {}) == 0.75
    # chop regime filters: chop 50 >= 38 -> 0
    assert donchian_breakout.evaluate({"chan": 150.0, "chop": 50.0}, {}) == 0.0
    # Hand calc: chan -50 -> progress (0-(-50))/100 = 0.5 -> -0.75
    assert donchian_breakout.evaluate({"chan": -50.0, "chop": 20.0}, {}) == -0.75
    # inside band -> no breakout
    assert donchian_breakout.evaluate({"chan": 50.0, "chop": 20.0}, {}) == 0.0
    # Hand calc: chan 200 -> progress 100/100 = 1.0 -> +1.0 exact
    assert donchian_breakout.evaluate({"chan": 200.0, "chop": 20.0}, {}) == 1.0


def test_pullback_goldens_long_only():
    # Hand calc: adx 30 >= 25, z=-2 pullback 1 -> progress (2-1)/1 = 1.0 -> +1.0
    assert pullback.evaluate({"trend": 30.0, "entry": -2.0}, {}) == 1.0
    # Hand calc: z=-1 at threshold -> progress 0 -> 0.5 floor
    assert pullback.evaluate({"trend": 30.0, "entry": -1.0}, {}) == 0.5
    # no trend: adx 20 < 25 -> 0 even with deep pullback
    assert pullback.evaluate({"trend": 20.0, "entry": -3.0}, {}) == 0.0
    # day-1 long-only: extension z=+3 -> 0 (no short)
    assert pullback.evaluate({"trend": 30.0, "entry": 3.0}, {}) == 0.0
    # boundary: adx == adx_min passes (strict < fails)
    assert pullback.evaluate({"trend": 25.0, "entry": -1.0}, {}) == 0.5


def test_regime_based_goldens():
    # trend regime: chop 30 < 38 and hurst 0.7 > 0.5 -> +1.0 categorical
    assert regime_based.evaluate({"chop": 30.0, "hurst": 0.7}, {}) == 1.0
    # chop regime: chop 70 > 61 and hurst 0.3 < 0.5 -> -1.0
    assert regime_based.evaluate({"chop": 70.0, "hurst": 0.3}, {}) == -1.0
    # hurst disagrees -> no regime
    assert regime_based.evaluate({"chop": 30.0, "hurst": 0.4}, {}) == 0.0
    assert regime_based.evaluate({"chop": 50.0, "hurst": 0.7}, {}) == 0.0
    # boundaries are NOT inside zones (strict < / >)
    assert regime_based.evaluate({"chop": 38.0, "hurst": 0.7}, {}) == 0.0
    assert regime_based.evaluate({"chop": 61.0, "hurst": 0.3}, {}) == 0.0


def test_microstructure_flow_goldens():
    # Hand calc: imb 0.8, long thr 0.6 -> progress (0.8-0.6)/(1-0.6) = 0.5 -> 0.75
    assert microstructure_flow.evaluate({"imb": 0.8}, {}) == pytest.approx(0.75, abs=1e-12)
    # Hand calc: imb 1.0 -> progress (1-0.6)/(1-0.6) = 1.0 -> +1.0 exact
    assert microstructure_flow.evaluate({"imb": 1.0}, {}) == 1.0
    # Hand calc: imb -0.8, short thr -0.6 -> progress (-0.6+0.8)/(1-0.6) = 0.5 -> -0.75
    assert microstructure_flow.evaluate({"imb": -0.8}, {}) == pytest.approx(-0.75, abs=1e-12)
    # threshold floor: imb exactly at long -> 0.5; inside zone -> 0
    assert microstructure_flow.evaluate({"imb": 0.6}, {}) == 0.5
    assert microstructure_flow.evaluate({"imb": 0.0}, {}) == 0.0


# --- range invariant + adversarial ---

def test_range_invariant_extreme_inputs():
    extreme = {
        "trend_following": {"fast": 1e12, "slow": 1.0, "rsi": 65.0},
        "rsi_overbought_oversold": {"rsi": 1e9},
        "mean_reversion": {"z": -1e6},
        "donchian_breakout": {"chan": 1e9, "chop": 20.0},
        "pullback": {"trend": 1e9, "entry": -1e6},
        "regime_based": {"chop": 0.0, "hurst": 1.0},
        "microstructure_flow": {"imb": 1e9},
    }
    for mod in MODULES:
        value = mod.evaluate(extreme[mod.NAME], {})
        assert -1.0 <= value <= 1.0


def test_adversarial_features_none_valueerror():
    with pytest.raises(ValueError):
        mean_reversion.evaluate(None, {})  # type: ignore[arg-type]


def test_adversarial_extra_feature_keys_ignored():
    features = {"chan": 150.0, "chop": 20.0, "injected": -99.0, "rsi": 1.0}
    assert donchian_breakout.evaluate(features, {}) == 0.75


def test_adversarial_valueerror_does_not_poison_next_call():
    with pytest.raises(ValueError, match="chan"):
        donchian_breakout.evaluate({"chop": 20.0}, {})
    assert donchian_breakout.evaluate({"chan": 150.0, "chop": 20.0}, {}) == 0.75


# --- real capabilities.yaml manifest + deriv eligibility ---

def test_manifest_requires_match_slot_unions():
    manifest = _load_manifest()
    # m4.7 spec DECLARED AMENDMENT 3 (2026-10-02): manifest now holds 12 keys —
    # legacy m4.2 names must be a SUBSET (union match per legacy module unchanged).
    assert set(BY_NAME) <= set(manifest)
    for mod in MODULES:
        slot_union = {name for name, _ in mod.FEATURE_SLOTS.values()}
        assert set(manifest[mod.NAME]["requires_analytics"]) == slot_union


def test_deriv_eligibility_six_true_microstructure_false():
    registry = CapabilityRegistry.from_yaml(CONFIG)
    expected = {name: True for name in BY_NAME}
    expected["microstructure_flow"] = False  # needs order_book_imbalance UNAVAILABLE@deriv
    for strategy_id, want in expected.items():
        eligible, reason = registry.is_strategy_eligible(strategy_id, "deriv")
        assert eligible is want, f"{strategy_id}: {reason}"
    if not registry.is_strategy_eligible("microstructure_flow", "deriv")[1].startswith(
        "needs order_book_imbalance"
    ):
        pytest.fail("microstructure reason must name the unavailable feature")
