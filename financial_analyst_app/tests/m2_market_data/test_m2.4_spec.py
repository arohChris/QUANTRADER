"""
P11 / M2.4 — Transformation (Tick → Candle 1m) — Spec tests (WRITTEN BEFORE CODE).
WHY: AGENTS.md §2 Step 3 — tests first against docs/specs/m2.4_transformation.spec.md
(LGTM @you 2026-09-23). Implementation imported only via
modules.m2_market_data.candles — collection RED until Step 5.

Contracts only until Step 5: MarketData, Tick, Candle.
No clock — bucket from md.exchange_timestamp only (purity asserted).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

# Direct impl import ONLY — fails collection until Step 5 (honest RED).
from modules.m2_market_data.candles import CandleBuilder  # noqa: F401

from shared.contracts.drop import Drop, DropReason
from shared.contracts.market_data import Candle, DataQuality, MarketData, Tick

BASE = datetime(2026, 3, 22, 19, 46, 0, tzinfo=timezone.utc)
NAIVE = datetime(2026, 3, 22, 19, 46, 0)


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
    instrument_id: str = "FX.EURUSD",
    symbol: str = "EURUSD",
) -> MarketData:
    payload: Tick
    if kind == "tick":
        payload = Tick(
            price=price,
            bid=bid,
            ask=ask,
            spread=(ask - bid) if (bid is not None and ask is not None) else None,
        )
    else:
        # non-tick path still needs a payload for MarketData; use Tick shape but kind mismatch
        payload = Tick(price=price, bid=bid, ask=ask, spread=None)
    return MarketData(
        instrument_id=instrument_id,
        symbol=symbol,
        broker=broker,
        kind=kind,  # type: ignore[arg-type]
        payload=payload,
        quality=quality,
        watermark=watermark,
        exchange_timestamp=exchange if exchange is not None else BASE,
        received_at=received if received is not None else BASE + timedelta(seconds=1),
        provider=provider,
        provider_symbol="frxEURUSD" if symbol == "EURUSD" else symbol,
        reference_snapshot_id="REF-2026-09-22-VERIFY",
    )


def _builder() -> CandleBuilder:
    return CandleBuilder(instrument_id="FX.EURUSD", broker="deriv", interval="1m")


# --- Edges from spec ---

def test_single_tick_opens_bucket_returns_none() -> None:
    b = _builder()
    r = b.on_tick(_md(price=1.08400, exchange=BASE + timedelta(seconds=10)))
    assert r is None  # open bucket, not yet closed


def test_close_single_tick_candle_ohlc_equal() -> None:
    # Hand calc: one tick price=1.08400 → O=H=L=C=1.08400, tick_count=1
    b = _builder()
    b.on_tick(_md(price=1.08400, exchange=BASE + timedelta(seconds=10)))
    c = b.close()
    assert isinstance(c, Candle)
    assert c.open == c.high == c.low == c.close == 1.08400
    assert c.tick_count == 1
    assert c.interval == "1m"
    assert c.interval_start == BASE
    assert c.interval_end == BASE + timedelta(minutes=1)


def test_same_price_multiple_ticks_tick_count_n() -> None:
    # Hand calc: 3 ticks all 1.08400 → O=H=L=C=1.08400, tick_count=3
    b = _builder()
    for s in (5, 15, 25):
        b.on_tick(_md(price=1.08400, exchange=BASE + timedelta(seconds=s)))
    c = b.close()
    assert isinstance(c, Candle)
    assert c.open == c.high == c.low == c.close == 1.08400
    assert c.tick_count == 3


def test_ohlc_from_multiple_prices() -> None:
    # Hand calc: 1.08400, 1.08450, 1.08350 → O=1.08400 H=1.08450 L=1.08350 C=1.08350, count=3
    b = _builder()
    b.on_tick(_md(price=1.08400, exchange=BASE + timedelta(seconds=5)))
    b.on_tick(_md(price=1.08450, exchange=BASE + timedelta(seconds=15)))
    b.on_tick(_md(price=1.08350, exchange=BASE + timedelta(seconds=25)))
    c = b.close()
    assert isinstance(c, Candle)
    assert c.open == 1.08400
    assert c.high == 1.08450
    assert c.low == 1.08350
    assert c.close == 1.08350
    assert c.tick_count == 3


def test_out_of_order_within_open_bucket_updates_ohlc() -> None:
    b = _builder()
    b.on_tick(_md(price=1.08400, exchange=BASE + timedelta(seconds=30)))
    # late but same open bucket — lower low arrives after
    r = b.on_tick(_md(price=1.08350, exchange=BASE + timedelta(seconds=10)))
    assert r is None  # still open
    c = b.close()
    assert isinstance(c, Candle)
    assert c.open == 1.08400  # first tick chronologically? No — open is first received, not min ts
    # Spec: O is first tick that opened the bucket (by arrival order for open), H/L update
    # First tick was 1.08400 at t=30, then 1.08350 at t=10 same bucket → O stays 1.08400 (opened with it)
    assert c.low == 1.08350
    assert c.tick_count == 2


def test_late_tick_prior_closed_bucket_drops_invalid_timestamp() -> None:
    b = _builder()
    b.on_tick(_md(price=1.08400, exchange=BASE + timedelta(seconds=10)))
    closed = b.close()
    assert isinstance(closed, Candle)
    # tick from previous minute
    r = b.on_tick(_md(price=1.08410, exchange=BASE - timedelta(seconds=5)))
    assert isinstance(r, Drop)
    assert r.reason == DropReason.invalid_timestamp


def test_boundary_cross_closes_prior_opens_new() -> None:
    b = _builder()
    b.on_tick(_md(price=1.08400, exchange=BASE + timedelta(seconds=50)))
    # exact interval_end
    next_start = BASE + timedelta(minutes=1)
    r = b.on_tick(_md(price=1.08420, exchange=next_start))
    # boundary cross: prior should be returned as closed Candle, new opened (None)
    # Spec: "tick at exact interval_end → closes prior, opens new"
    # Implementation choice: on_tick returns the closed Candle when crossing
    assert isinstance(r, Candle)
    assert r.close == 1.08400
    assert r.tick_count == 1
    assert r.interval_start == BASE
    # new bucket is open
    c2 = b.close()
    assert isinstance(c2, Candle)
    assert c2.open == 1.08420
    assert c2.interval_start == next_start


def test_empty_builder_close_returns_none() -> None:
    b = _builder()
    assert b.close() is None


def test_gapped_quality_inherits_gap_affected() -> None:
    b = _builder()
    b.on_tick(
        _md(
            price=1.08400,
            exchange=BASE + timedelta(seconds=10),
            quality=DataQuality.gapped,
        )
    )
    c = b.close()
    assert isinstance(c, Candle)
    assert c.quality_status == "GAP_AFFECTED"


def test_non_tick_kind_drops_missing_field() -> None:
    b = _builder()
    r = b.on_tick(_md(kind="candle", price=1.08400))
    assert isinstance(r, Drop)
    assert r.reason == DropReason.missing_field


def test_naive_exchange_timestamp_drops_invalid_timestamp() -> None:
    b = _builder()
    md = _md(price=1.08400)
    # force naive
    object.__setattr__(md, "exchange_timestamp", NAIVE)
    r = b.on_tick(md)
    assert isinstance(r, Drop)
    assert r.reason == DropReason.invalid_timestamp


def test_none_exchange_timestamp_drops_invalid_timestamp() -> None:
    b = _builder()
    md = _md(price=1.08400)
    object.__setattr__(md, "exchange_timestamp", None)
    r = b.on_tick(md)
    assert isinstance(r, Drop)
    assert r.reason == DropReason.invalid_timestamp


# --- Adversarial (not all in spec Edges list explicitly) ---

def test_adversarial_non_positive_price_drops_negative_price() -> None:
    b = _builder()
    r = b.on_tick(_md(price=0.0, exchange=BASE + timedelta(seconds=10)))
    assert isinstance(r, Drop)
    assert r.reason == DropReason.negative_price


def test_adversarial_negative_price_drops_negative_price() -> None:
    b = _builder()
    r = b.on_tick(_md(price=-1.0, exchange=BASE + timedelta(seconds=10)))
    assert isinstance(r, Drop)
    assert r.reason == DropReason.negative_price


def test_adversarial_future_beyond_5s_drops_future_timestamp() -> None:
    b = _builder()
    received = BASE + timedelta(seconds=1)
    exchange = received + timedelta(seconds=10)  # > received + 5s
    r = b.on_tick(_md(price=1.08400, exchange=exchange, received=received))
    assert isinstance(r, Drop)
    assert r.reason == DropReason.future_timestamp


def test_adversarial_many_ticks_exact_count() -> None:
    # Hand calc: 10000 ticks → tick_count=10000 exact
    b = _builder()
    for i in range(10000):
        b.on_tick(
            _md(price=1.08400, exchange=BASE + timedelta(milliseconds=i))
        )
    c = b.close()
    assert isinstance(c, Candle)
    assert c.tick_count == 10000


def test_identity_multi_instrument_isolated_builders() -> None:
    b1 = CandleBuilder(instrument_id="FX.EURUSD", broker="deriv", interval="1m")
    b2 = CandleBuilder(instrument_id="CRYPTO.BTCUSD", broker="deriv", interval="1m")
    b1.on_tick(_md(price=1.08400, instrument_id="FX.EURUSD", symbol="EURUSD"))
    b2.on_tick(_md(price=64248.0, instrument_id="CRYPTO.BTCUSD", symbol="BTCUSD"))
    c1 = b1.close()
    c2 = b2.close()
    assert isinstance(c1, Candle) and isinstance(c2, Candle)
    assert c1.open == 1.08400
    assert c2.open == 64248.0
