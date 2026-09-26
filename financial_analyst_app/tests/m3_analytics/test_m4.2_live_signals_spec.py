"""WHY: M4.2 Live Signals Console Step 3 — tests written against
docs/specs/m4.2_live_signals.spec.md BEFORE scripts/verify_m4.2_live_signals.py
exists (human directive ACTION 57, 2026-09-26, blanket covering this observation
tool end-to-end; no new calculations — pure conversion/composition helpers only).

RED at collection: FileNotFoundError on the script import — honest
tests-before-code, same discipline as test_m4.1_spec.py.
"""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_m4.2_live_signals.py"
_spec = importlib.util.spec_from_file_location("verify_m42_live_signals", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

from modules.m3_analytics.strategies import (  # script bootstraps sys.path
    mean_reversion,
    rsi_overbought_oversold,
    trend_following,
)

HistoryResponseError = mod.HistoryResponseError
FeatureUnionError = mod.FeatureUnionError

E1, E2, E3 = 1_767_225_600, 1_767_225_660, 1_767_225_720  # 2026-01-01 00:00/01/02 UTC


def _row(**kw: object) -> dict:
    base: dict = {
        "epoch": E1,
        "open": 1.08540,
        "high": 1.08600,
        "low": 1.08500,
        "close": 1.08570,
        "symbol": "frxEURUSD",
    }
    base.update(kw)
    return base


# --- history_candles_from_response: edges --------------------------------------


def test_history_three_candles_roundtrip_sorted_and_fields():
    msg = {"msg_type": "candles", "candles": [_row(epoch=E3), _row(epoch=E1), _row(epoch=E2)]}
    candles = mod.history_candles_from_response(msg)
    assert [c.interval_start for c in candles] == [
        datetime.fromtimestamp(E1, tz=UTC),
        datetime.fromtimestamp(E2, tz=UTC),
        datetime.fromtimestamp(E3, tz=UTC),
    ]
    first = candles[0]
    assert first.interval == "1m"
    assert first.interval_end == datetime.fromtimestamp(E1 + 60, tz=UTC)
    assert first.open == 1.08540 and first.close == 1.08570
    assert first.high == 1.08600 and first.low == 1.08500
    assert first.tick_count == 0
    assert first.quality_status == "COMPLETE"


def test_history_volume_int_becomes_tick_count():
    candles = mod.history_candles_from_response(
        {"candles": [_row(epoch=E1, volume=12), _row(epoch=E2, volume=3.7)]}
    )
    assert candles[0].tick_count == 12
    assert candles[1].tick_count == 0  # non-int volume → 0, never coerced


def test_history_rejects_non_dict_msg():
    for bad in (None, [], "candles", 7):
        with pytest.raises(HistoryResponseError):
            mod.history_candles_from_response(bad)  # type: ignore[arg-type]


def test_history_rejects_missing_candles_key():
    with pytest.raises(HistoryResponseError, match="candles"):
        mod.history_candles_from_response({"msg_type": "candles"})


def test_history_rejects_empty_candles_seed_never_passes():
    # E3 mirror: an empty seed must never pass — the console exits 1 on it.
    with pytest.raises(HistoryResponseError, match="empty"):
        mod.history_candles_from_response({"candles": []})


def test_history_rejects_candles_not_a_list():
    for bad in ("junk", {"a": 1}):
        with pytest.raises(HistoryResponseError):
            mod.history_candles_from_response({"candles": bad})


def test_history_skips_invalid_rows_keeps_valid():
    msg = {
        "candles": [
            _row(epoch=E1),
            _row(epoch="abc"),  # bad epoch → skipped
            _row(epoch=True),  # bool epoch → skipped (adversarial)
            _row(epoch=E1 + 300, close="abc"),  # non-numeric OHLC → skipped
            _row(epoch=E2 + 300),
        ]
    }
    candles = mod.history_candles_from_response(msg)
    assert [c.interval_start.timestamp() for c in candles] == [float(E1), float(E2 + 300)]


def test_history_all_rows_invalid_raises():
    msg = {"candles": [_row(epoch=None), _row(epoch=E2, high="x")]}
    with pytest.raises(HistoryResponseError, match="invalid"):
        mod.history_candles_from_response(msg)


# --- feature_union: dedup, first-seen order, fail-safe -------------------------


def test_feature_union_real_modules_ordered_dedup():
    # Hand union: trend(ema_20,sma_20,rsi_14) + rsi_ob(rsi_14 dup) + mean_rev(zscore_20)
    assert mod.feature_union(
        (trend_following, rsi_overbought_oversold, mean_reversion)
    ) == ("ema_20", "sma_20", "rsi_14", "zscore_20")


def test_feature_union_empty_input():
    assert mod.feature_union(()) == ()


def test_feature_union_module_without_slots_named_in_error():
    stray = SimpleNamespace(NAME="stray_strategy")
    with pytest.raises(FeatureUnionError, match="stray_strategy"):
        mod.feature_union((stray,))


# --- slot_features: composition + fail-safe missing ----------------------------


def test_slot_features_complete_then_evaluate_matches_m4_2_golden():
    analytics = {"ema_20": 101.0, "sma_20": 100.0, "rsi_14": 65.0}
    mapped, missing = mod.slot_features(trend_following, analytics)
    assert mapped == {"fast": 101.0, "slow": 100.0, "rsi": 65.0}
    assert missing == []
    # Hand calc (m4.2 golden): fast 101 > slow 100, gap = 1.0% >= full 1.0%,
    # rsi 65 >= threshold 60 → full long strength 1.0
    assert trend_following.evaluate(mapped, dict(trend_following.PARAM_DEFAULTS)) == 1.0


def test_slot_features_missing_analytic_reported_not_raised():
    mapped, missing = mod.slot_features(
        trend_following, {"ema_20": 101.0, "rsi_14": 65.0}
    )
    assert "slow" not in mapped
    assert missing == ["slow=sma_20"]
    assert mapped == {"fast": 101.0, "rsi": 65.0}


def test_slot_features_none_value_treated_as_missing():
    mapped, missing = mod.slot_features(
        trend_following, {"ema_20": 101.0, "sma_20": 100.0, "rsi_14": None}
    )
    assert "rsi" not in mapped
    assert missing == ["rsi=rsi_14"]


def test_slot_features_adversarial_none_analytics_all_missing():
    mapped, missing = mod.slot_features(mean_reversion, None)
    assert mapped == {}
    assert missing == ["z=zscore_20"]


# --- verdict: sign mapping + honest out-of-range -------------------------------


def test_verdict_direction_goldens():
    # Hand calc: m4.2 strength contract — +long, -short, 0 none, boundaries inclusive
    assert mod.verdict(0.75) == ("LONG", "0.75")
    assert mod.verdict(-0.5) == ("SHORT", "-0.5")
    assert mod.verdict(0.0) == ("NONE", "0.0")
    assert mod.verdict(1.0) == ("LONG", "1.0")
    assert mod.verdict(-1.0) == ("SHORT", "-1.0")


def test_verdict_adversarial_out_of_range_flagged_never_clamped():
    direction, note = mod.verdict(1.5)
    assert direction == "LONG"
    assert "out-of-range" in note
    direction2, note2 = mod.verdict(-2.0)
    assert direction2 == "SHORT"
    assert "out-of-range" in note2
