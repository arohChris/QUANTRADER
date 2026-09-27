"""WHY: M4.3 Signal Validation Step 3 — tests written against
docs/specs/m4.3_signal_validation.spec.md BEFORE modules/m4_alpha/signal/
exists (human directive ACTION 64 "LGTM" = spec approved 2026-09-26).

RED at collection: ModuleNotFoundError on 'modules.m4_alpha.signal' — honest
tests-before-code, same discipline as test_m4.6_spec.py.
Edges E1-E15 + ADV1-ADV3 map 1:1 to the spec; HC-1..HC-4 timing hand-calcs are
the correctness anchors (no money math in this slice).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from modules.m4_alpha.signal import (  # does not exist yet — honest RED
    BatchResult,
    SignalRejection,
    SignalRejectReason,
    SignalValidator,
)
from shared.contracts.signal import Signal

NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=UTC)
INST = "CRYPTO.BTCUSD"
BROKER = "deriv"
WINDOW = timedelta(seconds=60)


def _sig(**kw: object) -> Signal:
    base: dict[str, object] = {
        "signal_id": uuid4(),
        "strategy_id": "trend_following::ema_trend",
        "version": "2.1",
        "symbol": INST,
        "broker": BROKER,
        "direction": "long",
        "strength": 0.51,
        "timeframe": "1m",
        "valid_until": NOW + WINDOW,
        "timestamp": NOW,
    }
    base.update(kw)
    return Signal(**base)  # type: ignore[arg-type]


def _reason(out: object) -> object:
    assert isinstance(out, SignalRejection), f"expected rejection, got {out!r}"
    return out.reason


# --- E1-E4: pass path, shadow, freshness --------------------------------------

def test_e1_fresh_unique_valid_signal_passes_identity():
    v = SignalValidator()
    sig = _sig()
    out = v.validate(sig, now=NOW)
    assert out is sig  # same object — never mutated, never copied
    assert out.signal_id == sig.signal_id


def test_e2_shadow_beats_stale_tag_checked_first():
    # Both defects present: shadow=True AND expired — first-match-wins pin
    out = SignalValidator().validate(
        _sig(shadow=True, valid_until=NOW - timedelta(seconds=1)), now=NOW)
    assert _reason(out) == SignalRejectReason.shadow


def test_e3_expired_signal_rejected_stale():
    out = SignalValidator().validate(
        _sig(valid_until=NOW - timedelta(microseconds=1)), now=NOW)
    rej = out if isinstance(out, SignalRejection) else None
    assert rej is not None and rej.reason == SignalRejectReason.stale
    assert rej.signal_id and rej.strategy_id == "trend_following::ema_trend"
    assert rej.rejected_at == NOW  # rejected_at = caller's now, not a hidden clock


def test_e3b_boundary_now_equals_valid_until_passes():
    out = SignalValidator().validate(_sig(valid_until=NOW), now=NOW)
    assert isinstance(out, Signal)


def test_e3_rejection_record_is_frozen():
    out = SignalValidator().validate(_sig(shadow=True), now=NOW)
    assert isinstance(out, SignalRejection)
    with pytest.raises(ValidationError):
        out.reason = SignalRejectReason.stale  # type: ignore[misc]


def test_e4_naive_valid_until_rejected_invalid_timestamp_no_crash():
    out = SignalValidator().validate(
        _sig(valid_until=datetime(2026, 9, 26, 12, 1, 0)),  # noqa: DTZ001 — naive INTENDED (edge)
        now=NOW)
    assert _reason(out) == SignalRejectReason.invalid_timestamp


# --- E5-E7: latency budget ----------------------------------------------------

def test_e5_age_1us_over_budget_rejected_latency_exceeded():
    out = SignalValidator().validate(
        _sig(timestamp=NOW - timedelta(seconds=60, microseconds=1)), now=NOW)
    assert _reason(out) == SignalRejectReason.latency_exceeded


def test_e5b_boundary_age_exactly_budget_passes_hand_calc_hc4():
    # HC-4: age = 60.000000 exactly vs budget 60.0 → NOT exceeded (strict >)
    out = SignalValidator().validate(
        _sig(timestamp=NOW - WINDOW), now=NOW)
    assert isinstance(out, Signal)


def test_e5c_custom_micro_budget_honored():
    v = SignalValidator(latency_budget_seconds=0.1)
    over = v.validate(
        _sig(timestamp=NOW - timedelta(milliseconds=150)), now=NOW)
    assert _reason(over) == SignalRejectReason.latency_exceeded
    under = v.validate(
        _sig(strategy_id="mean_reversion::zscore",
             timestamp=NOW - timedelta(milliseconds=90)), now=NOW)
    assert isinstance(under, Signal)


def test_e6_future_stamped_signal_rejected_invalid_timestamp():
    out = SignalValidator().validate(_sig(timestamp=NOW + timedelta(seconds=1)), now=NOW)
    assert _reason(out) == SignalRejectReason.invalid_timestamp


def test_e7_naive_timestamp_rejected_invalid_timestamp_no_crash():
    out = SignalValidator().validate(
        _sig(timestamp=datetime(2026, 9, 26, 11, 59, 0)),  # noqa: DTZ001 — naive INTENDED (edge)
        now=NOW)
    assert _reason(out) == SignalRejectReason.invalid_timestamp


# --- E8: tradable via reference.exists ---------------------------------------

def test_e8_unknown_symbol_rejected():
    out = SignalValidator().validate(_sig(symbol="FAKE.NOPES"), now=NOW)
    assert _reason(out) == SignalRejectReason.unknown_symbol


def test_e8b_known_instrument_wrong_broker_rejected():
    # CRYPTO.BTCUSD lists [deriv, mt5] — headway is known but not mapped
    out = SignalValidator().validate(_sig(broker="headway"), now=NOW)
    assert _reason(out) == SignalRejectReason.unknown_symbol


def test_e8c_real_instrument_and_broker_passes():
    out = SignalValidator().validate(_sig(symbol=INST, broker=BROKER), now=NOW)
    assert isinstance(out, Signal)


# --- E10-E12: dedup window ----------------------------------------------------

def test_e10_duplicate_within_window_hand_calc_hc1():
    # HC-1: record NOW; identical key at NOW+60.000000 → age 60.0 ≤ 60 → duplicate
    v = SignalValidator()
    assert isinstance(v.validate(_sig(), now=NOW), Signal)
    out = v.validate(_sig(), now=NOW + WINDOW)
    assert _reason(out) == SignalRejectReason.duplicate


def test_e10b_same_key_after_window_passes_hand_calc_hc2():
    # HC-2: age 60.000001 > 60 → record pruned → passes.
    # Latency budget raised to ISOLATE dedup: with the default 60s budget the
    # latency step (spec order 4) would fire before dedup (order 6) — pinned here.
    v = SignalValidator(latency_budget_seconds=3600.0)
    assert isinstance(v.validate(_sig(), now=NOW), Signal)
    out = v.validate(
        _sig(valid_until=NOW + timedelta(seconds=600)),  # fresh past its own window
        now=NOW + WINDOW + timedelta(microseconds=1))
    assert isinstance(out, Signal)


def test_e11_key_is_strategy_symbol_direction_user():
    v = SignalValidator()
    assert isinstance(v.validate(_sig(), now=NOW), Signal)
    assert isinstance(v.validate(_sig(direction="short"), now=NOW), Signal)
    assert isinstance(
        v.validate(_sig(strategy_id="mean_reversion::zscore"), now=NOW), Signal)
    assert isinstance(v.validate(_sig(user_id="42"), now=NOW), Signal)


def test_e12_rejected_duplicate_does_not_refresh_record_hand_calc_hc3():
    # HC-3: record NOW; dup rejected at NOW+30 (record stays NOW); resend at
    # NOW+61 → age 61 > 60 → passes (rejections never refresh the window).
    # Latency budget raised to ISOLATE dedup (spec order 4 would fire at age 61).
    v = SignalValidator(latency_budget_seconds=3600.0)
    assert isinstance(v.validate(_sig(), now=NOW), Signal)
    assert _reason(v.validate(_sig(), now=NOW + timedelta(seconds=30))) == (
        SignalRejectReason.duplicate)
    out = v.validate(
        _sig(valid_until=NOW + timedelta(seconds=600)),  # fresh past its own window
        now=NOW + timedelta(seconds=61))
    assert isinstance(out, Signal)


# --- E13-E15: batch, no-record-on-reject, naive now ---------------------------

def test_e13_batch_preserves_order_splits_and_empty():
    v = SignalValidator()
    a, b = _sig(), _sig(strategy_id="pullback::fib")
    shadow = _sig(strategy_id="regime_based::trend", shadow=True)
    res = v.validate_batch([a, shadow, b], now=NOW)
    assert isinstance(res, BatchResult)
    assert res.validated == (a, b)  # order preserved within each side
    assert [r.reason for r in res.rejected] == [SignalRejectReason.shadow]
    empty = v.validate_batch([], now=NOW)
    assert empty.validated == () and empty.rejected == ()


def test_e14_rejected_signal_never_recorded_in_dedup():
    v = SignalValidator()
    stale = _sig(valid_until=NOW - timedelta(seconds=1))
    assert _reason(v.validate(stale, now=NOW)) == SignalRejectReason.stale
    fresh = _sig(valid_until=NOW + WINDOW)  # same key — passes ONLY if the
    # stale reject left no dedup record (spec: only full passes are recorded)
    assert isinstance(v.validate(fresh, now=NOW), Signal)


def test_e15_naive_now_raises_value_error():
    with pytest.raises(ValueError, match="now"):
        SignalValidator().validate(
            _sig(), now=datetime(2026, 9, 26, 12, 0))  # noqa: DTZ001 — naive INTENDED (edge)


# --- ADV: adversarial ----------------------------------------------------------

def test_adv1_non_signal_payload_raises_typeerror_naming_type():
    v = SignalValidator()
    with pytest.raises(TypeError, match="NoneType"):
        v.validate(None, now=NOW)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="dict"):
        v.validate({"signal_id": "x"}, now=NOW)  # type: ignore[arg-type]


def test_adv2_sql_injection_symbol_rejected_unknown_symbol():
    out = SignalValidator().validate(
        _sig(symbol="'; DROP TABLE instruments;--"), now=NOW)
    assert _reason(out) == SignalRejectReason.unknown_symbol


def test_adv3_negative_budget_or_window_value_error_at_startup():
    with pytest.raises(ValueError, match="budget"):
        SignalValidator(latency_budget_seconds=-1)
    with pytest.raises(ValueError, match="window"):
        SignalValidator(dedup_window_seconds=-0.5)
