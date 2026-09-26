"""M2.1-live capture — spec tests (WRITTEN BEFORE CODE, Step 3).

Spec: docs/specs/m2.1_live_capture.spec.md (edges E1-E11 + adversarial + hand calcs).
Honest RED expected at collection: modules/m2_market_data/capture.py not yet written.
No sockets ever — pytest policy: network only in the verify script (Step 7).
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from modules.m2_market_data import capture


def _frame(epoch: int, quote: str, symbol: str = "cryBTCUSD") -> dict:
    """Raw Deriv tick frame as captured off the A41-signed endpoint (real 2026-09-25 shape)."""
    return {
        "msg_type": "tick",
        "tick": {"epoch": epoch, "quote": quote, "symbol": symbol, "pip_size": 3},
        "captured_at": "2026-09-25T00:00:00+00:00",
    }


def _row(epoch: int, price, snapshot: str = "REF-2026-09-25-001") -> dict:
    return {"epoch": epoch, "price": price, "reference_snapshot_id": snapshot}


# --- E10 + adversarial: capture frame ---------------------------------------

def test_frame_keys_exact_no_app_id() -> None:  # E10
    data = json.loads(capture.build_capture_frame("cryBTCUSD"))
    assert set(data.keys()) == {"ticks", "subscribe"}
    assert data["ticks"] == "cryBTCUSD"
    assert data["subscribe"] == 1
    assert "app_id" not in data


def test_frame_symbol_opaque_injection_stays_valid_json() -> None:  # adversarial
    data = json.loads(capture.build_capture_frame("'; DROP TABLE ticks; --"))
    assert data["ticks"] == "'; DROP TABLE ticks; --"
    assert set(data.keys()) == {"ticks", "subscribe"}


# --- E9: argument validation -----------------------------------------------

@pytest.mark.parametrize("n_ticks", [0, -1])
def test_validate_args_rejects_non_positive_ticks(n_ticks: int) -> None:  # E9
    with pytest.raises(ValueError):
        capture.validate_args("cryBTCUSD", n_ticks)


@pytest.mark.parametrize("symbol", ["", "   "])
def test_validate_args_rejects_blank_symbol(symbol: str) -> None:  # E9
    with pytest.raises(ValueError):
        capture.validate_args(symbol, 5)


def test_validate_args_ok_returns_normalized_pair() -> None:  # E9
    assert capture.validate_args("cryBTCUSD", 50) == ("cryBTCUSD", 50)


# --- E2: response classification -------------------------------------------

def test_classify_tick() -> None:  # E2 shape
    assert capture.classify(_frame(1, "83668.324")) == "tick"


def test_classify_market_closed_response() -> None:  # E2 real endpoint frame 2026-09-25
    closed = {
        "msg_type": "tick",
        "tick": {
            "echo_req": {"subscribe": 1, "ticks": "frxEURUSD"},
            "error": {
                "code": "MarketIsClosed",
                "code_args": ["2026-09-28 00:00:00"],
                "message": "This market is presently closed.",
            },
        },
    }
    assert capture.classify(closed) == "closed"


def test_classify_generic_error() -> None:
    err = {"msg_type": "error", "error": {"code": "InputValidationFailed"}}
    assert capture.classify(err) == "error"


def test_classify_heartbeat_other() -> None:
    assert capture.classify({"msg_type": "heartbeat"}) == "other"


# --- E11: JSONL roundtrip ----------------------------------------------------

def test_jsonl_roundtrip_preserves_quote_string_exactly() -> None:  # E11
    line = capture.to_jsonl_line(_frame(1790371322, "83668.324"))
    back = capture.from_jsonl_line(line)
    assert back["tick"]["quote"] == "83668.324"
    assert isinstance(back["tick"]["quote"], str)


def test_jsonl_invalid_line_raises_capture_error() -> None:  # E11 edge
    with pytest.raises(capture.CaptureError):
        capture.from_jsonl_line("}{ not json")


# --- E1: connect retry (flaky local DNS) -------------------------------------

def test_connect_retry_recovers_after_transient_failures() -> None:  # E1
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError("getaddrinfo failed")
        return "WS"

    result = capture.connect_with_retry(flaky, attempts=8, sleep=sleeps.append)
    assert result == "WS"
    assert calls["n"] == 3
    assert len(sleeps) == 2


def test_connect_retry_exhausts_raises_after_attempts() -> None:  # E1
    def dead() -> str:
        raise OSError("timed out during opening handshake")

    with pytest.raises(capture.CaptureError) as ei:
        capture.connect_with_retry(dead, attempts=3, sleep=lambda _s: None)
    assert "3" in str(ei.value)


# --- E5: this-run isolation in shared daily partition ------------------------

def test_filter_run_rows_keeps_only_this_run_epochs() -> None:  # E5
    rows = [_row(100, "1.00001"), _row(200, "1.00002"), _row(300, "1.00003")]
    out = capture.filter_run_rows(rows, {200, 300})
    assert [r["epoch"] for r in out] == [200, 300]


def test_filter_run_rows_empty_intersection() -> None:  # E5 edge
    assert capture.filter_run_rows([_row(1, "1.00001")], {999}) == []


# --- E3: empty run never passes ---------------------------------------------

def test_empty_capture_never_passes() -> None:  # E3
    with pytest.raises(capture.CaptureMismatchError):
        capture.assert_readback(
            captured=[], rows=[], drops=[], tick_size=Decimal("0.01")
        )


# --- E4 reconcile + report ----------------------------------------------------

def test_assert_readback_happy_path_report_counts_and_prices() -> None:  # E4 ok
    # Hand calc: 83668.324 / 0.01 = 8366832.4 -> HALF_UP 8366832 -> 83668.32
    # Hand calc: 83668.355 / 0.01 = 8366835.5 -> HALF_UP 8366836 -> 83668.36
    captured = [_frame(1, "83668.324"), _frame(2, "83668.355")]
    rows = [_row(1, 83668.32), _row(2, 83668.36)]
    rep = capture.assert_readback(
        captured=captured, rows=rows, drops=[], tick_size=Decimal("0.01")
    )
    assert rep.captured == 2
    assert rep.accepted == 2
    assert rep.dropped == 0
    assert Decimal(str(rep.first_price)) == Decimal("83668.32")
    assert Decimal(str(rep.last_price)) == Decimal("83668.36")


def test_reconcile_count_mismatch_raises() -> None:  # E4
    captured = [_frame(1, "1.00001"), _frame(2, "1.00001")]
    rows = [_row(1, 1.00001)]
    with pytest.raises(capture.CaptureMismatchError) as ei:
        capture.assert_readback(
            captured=captured, rows=rows, drops=[], tick_size=Decimal("0.00001")
        )
    assert "reconcile" in str(ei.value)


def test_reconcile_with_drops_balances() -> None:  # E4
    captured = [_frame(1, "83668.324"), _frame(2, "83668.324")]
    rows = [_row(1, 83668.32)]
    drops = [{"reason": "bad_frame"}]
    rep = capture.assert_readback(
        captured=captured, rows=rows, drops=drops, tick_size=Decimal("0.01")
    )
    assert rep.captured == 2
    assert rep.accepted == 1
    assert rep.dropped == 1


# --- E6: price equality (Decimal only) ---------------------------------------

def test_price_mismatch_raises_naming_epoch_and_values() -> None:  # E6
    captured = [_frame(1790371322, "83668.324")]
    rows = [_row(1790371322, 83668.99)]
    with pytest.raises(capture.CaptureMismatchError) as ei:
        capture.assert_readback(
            captured=captured, rows=rows, drops=[], tick_size=Decimal("0.01")
        )
    msg = str(ei.value)
    assert "price" in msg
    assert "1790371322" in msg
    assert "83668.32" in msg
    assert "83668.99" in msg


# --- E7: chronology -----------------------------------------------------------

def test_non_monotonic_epochs_raise() -> None:  # E7
    captured = [_frame(200, "1.00001"), _frame(100, "1.00001")]
    rows = [_row(200, 1.00001), _row(100, 1.00001)]
    with pytest.raises(capture.CaptureMismatchError) as ei:
        capture.assert_readback(
            captured=captured, rows=rows, drops=[], tick_size=Decimal("0.00001")
        )
    assert "chronology" in str(ei.value)


def test_equal_epochs_allowed() -> None:  # E7 edge — sub-second feeds share epochs
    captured = [_frame(100, "1.00001"), _frame(100, "1.00002")]
    rows = [_row(100, 1.00001), _row(100, 1.00002)]
    rep = capture.assert_readback(
        captured=captured, rows=rows, drops=[], tick_size=Decimal("0.00001")
    )
    assert rep.accepted == 2


# --- E8: lineage mandatory ----------------------------------------------------

@pytest.mark.parametrize("snap", ["", None])
def test_missing_lineage_raises(snap) -> None:  # E8
    captured = [_frame(1, "1.00001")]
    rows = [{"epoch": 1, "price": 1.00001, "reference_snapshot_id": snap}]
    with pytest.raises(capture.CaptureMismatchError) as ei:
        capture.assert_readback(
            captured=captured, rows=rows, drops=[], tick_size=Decimal("0.00001")
        )
    assert "lineage" in str(ei.value)


# --- hand calcs: rounding rule (single source, HALF_UP) -----------------------

def test_price_identity_forex_tick_size() -> None:
    # Hand calc: 1.12345 / 0.00001 = 112345 exact -> 1.12345 (identity, no rounding)
    captured = [_frame(1, "1.12345")]
    rows = [_row(1, 1.12345)]
    rep = capture.assert_readback(
        captured=captured, rows=rows, drops=[], tick_size=Decimal("0.00001")
    )
    assert rep.accepted == 1


def test_round_to_tick_half_up_documented_cases() -> None:
    # Hand calc (M2.2 Option B): 1.085405 / 0.00001 = 108540.5 -> HALF_UP -> 108541 -> 1.08541
    # Hand calc: 83668.355 / 0.01 = 8366835.5 -> HALF_UP -> 8366836 -> 83668.36
    assert capture.round_to_tick(Decimal("1.085405"), Decimal("0.00001")) == Decimal("1.08541")
    assert capture.round_to_tick(Decimal("83668.355"), Decimal("0.01")) == Decimal("83668.36")


# --- adversarial (not in spec): hostile quotes never silently pass ------------

@pytest.mark.parametrize("bad_quote", ["NaN", "1e12"])
def test_adversarial_quote_never_silently_passes(bad_quote: str) -> None:  # adversarial
    captured = [_frame(1, bad_quote)]
    rows = [_row(1, 83668.32)]
    with pytest.raises(capture.CaptureMismatchError):
        capture.assert_readback(
            captured=captured, rows=rows, drops=[], tick_size=Decimal("0.01")
        )
