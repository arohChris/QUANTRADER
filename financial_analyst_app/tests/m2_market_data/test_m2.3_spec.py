"""
P10 / M2.3 — Validation (MarketData quality bouncer) — Spec tests (WRITTEN BEFORE CODE).
WHY: AGENTS.md §2 Step 3 — tests exist first against docs/specs/m2.3_validation.spec.md
(LGTM with 4 minors 2026-09-22). Implementation imported only via
modules.m2_market_data.validate — collection RED until Step 5.

Contracts only until Step 5: MarketData, Tick, Candle, Drop/DropReason 1.5.0.
No clock / reference / watermark_registry — purity asserted by absence of deps.

Gate: pure validate(md) → same md | Drop; future > received+5s; non-finite|<=0;
bid>ask; quality gapped not cleared; no watermark side effect.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

# Direct impl import ONLY — fails collection until Step 5 (honest RED).
from modules.m2_market_data.validate import validate  # noqa: F401

from shared.contracts.drop import Drop, DropReason
from shared.contracts.market_data import Candle, DataQuality, MarketData, Tick

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 1, 1, 12, 0, 0)


def _md(
    *,
    price: float = 1.08400,
    bid: float | None = 1.08395,
    ask: float | None = 1.08405,
    exchange: datetime | None = None,
    received: datetime | None = None,
    quality: DataQuality = DataQuality.pristine,
    kind: str = "tick",
    provider: str | None = "deriv",
    broker: str = "deriv",
    watermark: int = 1,
) -> MarketData:
    payload: Tick | Candle
    if kind == "tick":
        payload = Tick(price=price, bid=bid, ask=ask, spread=(ask - bid) if (bid is not None and ask is not None) else None)
    else:
        payload = Candle(
            open=1.0,
            high=1.1,
            low=0.9,
            close=1.05,
            interval="1m",
            interval_start=BASE,
            interval_end=BASE + timedelta(minutes=1),
        )
    return MarketData(
        instrument_id="FX.EURUSD",
        symbol="EURUSD",
        broker=broker,
        kind=kind,  # type: ignore[arg-type]
        payload=payload,
        quality=quality,
        watermark=watermark,
        exchange_timestamp=exchange if exchange is not None else BASE,
        received_at=received if received is not None else BASE,
        provider=provider,
        provider_symbol="frxEURUSD",
        user_id=None,
        schema_version="canonical_quote_v1",
    )


def _as_md(result) -> MarketData:
    assert isinstance(result, MarketData), f"expected MarketData, got {type(result).__name__}: {result!r}"
    return result


def _as_drop(result) -> Drop:
    assert isinstance(result, Drop), f"expected Drop, got {type(result).__name__}: {result!r}"
    return result


# --- Edges: accept paths ---

def test_valid_eurUSD_tick_identity_pass_through() -> None:
    # Spec Edges: valid tick → same md instance returned, quality unchanged
    md = _md()
    out = validate(md)
    assert out is md
    assert out.quality == DataQuality.pristine


def test_future_boundary_exactly_plus_5s_accepts() -> None:
    # Spec Edges: exchange = received + 5s exactly → accept (boundary is >, not >=)
    received = BASE
    exchange = BASE + timedelta(seconds=5)
    md = _md(exchange=exchange, received=received)
    assert validate(md) is md


def test_past_or_equal_exchange_timestamp_accepts() -> None:
    # Spec Edges: exchange = received or past → accept
    for delta in (timedelta(0), timedelta(seconds=-1), timedelta(days=-1)):
        md = _md(exchange=BASE + delta, received=BASE)
        assert validate(md) is md


def test_positive_tiny_price_0_00001_accepts() -> None:
    # Spec Edges: price = 0.00001 positive tiny → accept
    # Hand calc: Decimal(str(0.00001)) = 0.00001 > 0 → pass price gate
    md = _md(price=0.00001, bid=0.00001, ask=0.00001)
    assert validate(md) is md


def test_zero_spread_equal_bid_ask_accepts() -> None:
    # Spec Correct: inverted is strict bid > ask only; equal bid/ask / zero spread valid
    md = _md(price=1.084, bid=1.084, ask=1.084)
    assert validate(md) is md


def test_bid_none_skips_inverted_check() -> None:
    # Spec Edges: bid=None or ask=None → skip inverted; other gates still apply
    md = _md(bid=None, ask=1.08405, price=1.084)
    assert validate(md) is md


def test_ask_none_skips_inverted_check() -> None:
    md = _md(bid=1.08395, ask=None, price=1.084)
    assert validate(md) is md


def test_quality_gapped_pass_through_not_cleared() -> None:
    # Spec Edges: quality=gapped that passes numeric gates → still gapped
    md = _md(quality=DataQuality.gapped)
    out = validate(md)
    assert out is md
    assert out.quality == DataQuality.gapped


def test_input_stays_frozen_after_validate() -> None:
    # Spec Edges: same instance still frozen — watermark assign raises
    md = _md()
    out = validate(md)
    assert out is md
    with pytest.raises(Exception):
        md.watermark = 999  # type: ignore[misc]


# --- Edges: reject paths ---

def test_future_timestamp_plus_6s_drops() -> None:
    # Spec Edges: exchange = received + 6s → Drop(future_timestamp)
    md = _md(exchange=BASE + timedelta(seconds=6), received=BASE)
    d = _as_drop(validate(md))
    assert d.reason == DropReason.future_timestamp
    assert d.provider == "deriv"


def test_price_zero_drops_negative_price() -> None:
    # Spec Edges: price = 0 → Drop(negative_price)
    # Hand calc: Decimal(str(0.0)) == 0 → not > 0 → reject
    md = _md(price=0.0, bid=0.0, ask=0.0)
    d = _as_drop(validate(md))
    assert d.reason == DropReason.negative_price


def test_price_negative_drops_negative_price() -> None:
    # Spec Edges: price = -0.01 → Drop(negative_price)
    # Hand calc: Decimal(str(-0.01)) = -0.01 <= 0 → reject
    md = _md(price=-0.01, bid=-0.02, ask=-0.005)
    d = _as_drop(validate(md))
    assert d.reason == DropReason.negative_price


def test_inverted_bid_gt_ask_drops() -> None:
    # Spec Edges: bid=1.09, ask=1.08 → Drop(inverted_quote)
    # Hand calc: 1.09 > 1.08 → inverted (M2.2 emitted; M2.3 bounces)
    md = _md(price=1.085, bid=1.09, ask=1.08)
    d = _as_drop(validate(md))
    assert d.reason == DropReason.inverted_quote


def test_kind_not_tick_drops_missing_field() -> None:
    # Spec Edges: kind != "tick" → Drop(missing_field) this slice
    md = _md(kind="candle")
    d = _as_drop(validate(md))
    assert d.reason == DropReason.missing_field


def test_naive_exchange_timestamp_drops_invalid_timestamp() -> None:
    # Spec Edges: naive exchange_timestamp → Drop(invalid_timestamp)
    md = _md(exchange=NAIVE, received=BASE)
    d = _as_drop(validate(md))
    assert d.reason == DropReason.invalid_timestamp


def test_naive_received_at_drops_invalid_timestamp() -> None:
    # Spec Edges: naive received_at → Drop(invalid_timestamp)
    md = _md(exchange=BASE, received=NAIVE)
    d = _as_drop(validate(md))
    assert d.reason == DropReason.invalid_timestamp


# --- Adversarial (not in edges) ---

def test_adversarial_candle_payload_while_kind_tick_drops_missing_field() -> None:
    # Spec Adversarial: payload Candle while kind=tick → Drop(missing_field), never crash
    md = _md(kind="tick")
    # corrupt union: force Candle payload with kind="tick"
    object.__setattr__(md, "payload", Candle(
        open=1.0, high=1.1, low=0.9, close=1.05,
        interval="1m", interval_start=BASE, interval_end=BASE + timedelta(minutes=1),
    ))
    d = _as_drop(validate(md))
    assert d.reason == DropReason.missing_field


def test_adversarial_epoch_1970_past_accepts() -> None:
    # Spec Adversarial: year 1970 exchange with received 2026 → accept (past fine)
    md = _md(exchange=datetime(1970, 1, 1, tzinfo=timezone.utc), received=BASE)
    assert validate(md) is md


def test_adversarial_nan_price_drops_negative_price_not_pass() -> None:
    # Spec Outputs exact form: not math.isfinite(price) or Decimal(str(price)) <= 0
    # Hand calc: float("nan") isfinite=False → reject (bare nan<=0 would be False — non-compliant)
    md = _md(price=float("nan"), bid=1.0, ask=1.01)
    d = _as_drop(validate(md))
    assert d.reason == DropReason.negative_price


def test_adversarial_provider_none_falls_back_to_broker() -> None:
    # Spec Outputs: provider=md.provider or md.broker — required Drop.provider str
    md = _md(exchange=BASE + timedelta(seconds=6), received=BASE, provider=None, broker="deriv")
    d = _as_drop(validate(md))
    assert d.reason == DropReason.future_timestamp
    assert d.provider == "deriv"
    assert d.provider != ""


def test_no_clock_or_registry_deps_in_signature() -> None:
    # Spec Inputs: no clock, no reference, no watermark_registry
    import inspect

    sig = inspect.signature(validate)
    params = set(sig.parameters)
    assert "clock" not in params
    assert "reference" not in params
    assert "watermark_registry" not in params
    assert params == {"md"}


def test_drop_reason_1_5_members_present() -> None:
    # Contract notes: Drop 1.5.0 full additive set
    assert DropReason.future_timestamp.value == "future_timestamp"
    assert DropReason.negative_price.value == "negative_price"
    assert DropReason.inverted_quote.value == "inverted_quote"
    # existing members still present (no removal)
    assert DropReason.timestamp_ahead.value == "timestamp_ahead"
    assert DropReason.invalid_price.value == "invalid_price"
