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
        # WHY far received: default received must not trip defensive future gate
        # (exchange can be BASE+50s in tests; received+5s must clear it).
        # Explicit received= still used by the future adversarial test.
        received_at=received if received is not None else BASE + timedelta(hours=1),
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


# --- Multi-interval support (1m,5m,15m,40m,1h,2h,4h,24h) ---

def test_supported_intervals_accepted() -> None:
    for iv in ("1m", "5m", "15m", "40m", "1h", "2h", "4h", "24h"):
        CandleBuilder(instrument_id="FX.EURUSD", broker="deriv", interval=iv)


def test_unsupported_interval_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unsupported interval"):
        CandleBuilder(instrument_id="FX.EURUSD", broker="deriv", interval="7m")


def test_40m_interval_bucket_boundary_and_duration() -> None:
    # 40m boundaries within a day: 00:00, 00:40, 01:20, ...
    # Hand calc: exchange 01:30 → floor to 01:20 (minutes_into_day=90, 90//40=2, 2*40=80 → 01:20)
    start = datetime(2026, 3, 22, 1, 30, 0, tzinfo=timezone.utc)
    b = CandleBuilder(instrument_id="FX.EURUSD", broker="deriv", interval="40m")
    b.on_tick(_md(price=1.08400, exchange=start + timedelta(seconds=10)))
    c = b.close()
    assert isinstance(c, Candle)
    assert c.interval == "40m"
    assert c.interval_start == datetime(2026, 3, 22, 1, 20, 0, tzinfo=timezone.utc)
    assert c.interval_end == datetime(2026, 3, 22, 2, 0, 0, tzinfo=timezone.utc)
    assert c.expected_duration_ms == 40 * 60 * 1000


def test_2h_interval_bucket_boundary_and_duration() -> None:
    # Hand calc: exchange 03:10 → floor to 02:00 (190//120=1 → 120 min = 02:00)
    start = datetime(2026, 3, 22, 3, 10, 0, tzinfo=timezone.utc)
    b = CandleBuilder(instrument_id="FX.EURUSD", broker="deriv", interval="2h")
    b.on_tick(_md(price=1.08400, exchange=start + timedelta(seconds=10)))
    c = b.close()
    assert isinstance(c, Candle)
    assert c.interval_start == datetime(2026, 3, 22, 2, 0, 0, tzinfo=timezone.utc)
    assert c.interval_end == datetime(2026, 3, 22, 4, 0, 0, tzinfo=timezone.utc)
    assert c.expected_duration_ms == 120 * 60 * 1000


def test_4h_interval_bucket_boundary_and_duration() -> None:
    # Hand calc: exchange 05:00 → floor to 04:00 (300//240=1 → 240 min = 04:00)
    start = datetime(2026, 3, 22, 5, 0, 30, tzinfo=timezone.utc)
    b = CandleBuilder(instrument_id="FX.EURUSD", broker="deriv", interval="4h")
    b.on_tick(_md(price=1.08400, exchange=start))
    c = b.close()
    assert isinstance(c, Candle)
    assert c.interval_start == datetime(2026, 3, 22, 4, 0, 0, tzinfo=timezone.utc)
    assert c.interval_end == datetime(2026, 3, 22, 8, 0, 0, tzinfo=timezone.utc)
    assert c.expected_duration_ms == 240 * 60 * 1000


def test_24h_interval_bucket_boundary_and_duration() -> None:
    # Hand calc: exchange 13:00 on day → floor to 00:00 same day (780//1440=0 → 0 min)
    start = datetime(2026, 3, 22, 13, 0, 0, tzinfo=timezone.utc)
    b = CandleBuilder(instrument_id="FX.EURUSD", broker="deriv", interval="24h")
    b.on_tick(_md(price=1.08400, exchange=start + timedelta(seconds=10)))
    c = b.close()
    assert isinstance(c, Candle)
    assert c.interval_start == datetime(2026, 3, 22, 0, 0, 0, tzinfo=timezone.utc)
    assert c.interval_end == datetime(2026, 3, 23, 0, 0, 0, tzinfo=timezone.utc)
    assert c.expected_duration_ms == 1440 * 60 * 1000


def test_multi_minute_interval_stays_open_across_minute_boundary() -> None:
    # 5m bucket: ticks at 19:46 and 19:47 must NOT close — same 5m bucket
    # floor: 19:45 for both (minutes_into_day//5)
    b = CandleBuilder(instrument_id="FX.EURUSD", broker="deriv", interval="5m")
    r1 = b.on_tick(_md(price=1.08400, exchange=datetime(2026, 3, 22, 19, 46, 0, tzinfo=timezone.utc)))
    r2 = b.on_tick(_md(price=1.08410, exchange=datetime(2026, 3, 22, 19, 47, 0, tzinfo=timezone.utc)))
    assert r1 is None and r2 is None  # still open
    c = b.close()
    assert isinstance(c, Candle)
    assert c.tick_count == 2
    assert c.interval_start == datetime(2026, 3, 22, 19, 45, 0, tzinfo=timezone.utc)
    assert c.interval_end == datetime(2026, 3, 22, 19, 50, 0, tzinfo=timezone.utc)


# --- Spread stats (great addition from review — Candle contract fields) ---

def test_spread_stats_present_when_bid_ask() -> None:
    # Hand calc: spreads = ask-bid = 0.00010, 0.00020, 0.00030
    # min=0.00010 max=0.00030 avg=(0.00010+0.00020+0.00030)/3=0.00020
    b = _builder()
    b.on_tick(_md(price=1.08400, bid=1.08395, ask=1.08405, exchange=BASE + timedelta(seconds=5)))  # spread 0.00010
    b.on_tick(_md(price=1.08410, bid=1.08400, ask=1.08420, exchange=BASE + timedelta(seconds=15)))  # spread 0.00020
    b.on_tick(_md(price=1.08420, bid=1.08400, ask=1.08430, exchange=BASE + timedelta(seconds=25)))  # spread 0.00030
    c = b.close()
    assert isinstance(c, Candle)
    assert c.min_spread is not None and abs(c.min_spread - 0.00010) < 1e-9
    assert c.max_spread is not None and abs(c.max_spread - 0.00030) < 1e-9
    assert c.average_spread is not None and abs(c.average_spread - 0.00020) < 1e-9
    assert c.tick_count == 3


def test_spread_stats_none_when_any_bid_or_ask_missing() -> None:
    b = _builder()
    b.on_tick(_md(price=1.08400, bid=1.08395, ask=1.08405, exchange=BASE + timedelta(seconds=5)))
    # second tick missing bid → all-or-none → all three None
    b.on_tick(_md(price=1.08410, bid=None, ask=1.08420, exchange=BASE + timedelta(seconds=15)))
    c = b.close()
    assert isinstance(c, Candle)
    assert c.average_spread is None
    assert c.min_spread is None
    assert c.max_spread is None
    assert c.tick_count == 2
