"""M2.13 — broker 1-minute candle ingest. TESTS WRITTEN BEFORE IMPLEMENTATION.

Spec: docs/specs/m2.13_broker_candle_ingest.spec.md

WHY these run against a module that does not exist yet: AGENTS.md 2 Step 3 requires the
tests be written against the SPEC, not fitted to code afterwards. The honest collection
state is `ModuleNotFoundError` - that is the RED, not a failure to fix.

The whole point of this slice is that the BROKER is the OHLC authority. So most assertions
here are "we reproduce exactly what the wire said" - a test that recomputes OHLC from ticks
would be testing the thing we deliberately removed.

Hand calcs are `Decimal` exact per AGENTS.md 3.1. NOTE: the frozen `Candle` contract types
prices as `float`, so a price hand-calc asserts `Decimal(str(x)) == Decimal("...")` and says
so in a comment - it does NOT weaken to approx, and the Decimal is the check.

A NOTE ON THE MIXED `Decimal(...)` STYLE, because ruff's FURB157 disagrees and ruff is wrong
here for the values that matter. Two cases, deliberately different:
  * NON-INTEGER money/price literals are written `Decimal("1.13399")` - from a STRING, always.
    AGENTS.md 3.1 forbids constructing a money Decimal from a float, and a string is the only
    form that cannot carry binary-float error in. Every price hand-calc below obeys this.
  * INTEGER literals appear as `Decimal(0)` / `Decimal(-1)`. int -> Decimal is EXACT (there is
    no float step), so this is not a weakening, and FURB157's autofix is safe on these.
The autofix was therefore applied to the integer literals ONLY, and never to a price.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from modules.m2_market_data.broker_candles import (
    BrokerCandleIngestor,
    parse_ohlc_frame,
)
from modules.m2_market_data.broker_feeds.deriv.stream import route_frame
from shared.contracts.drop import Drop
from shared.contracts.market_data import Candle

PROVIDER = "deriv"
INSTR = "CRYPTO.BTCUSD"
NATIVE = "cryBTCUSD"

#: A real, verified frame captured from the live socket 2026-09-29.
REAL_OHLC = {
    "msg_type": "ohlc",
    "open_time": 1790708400,
    "epoch": 1790708822,
    "granularity": 60,
    "open": "83654.088",
    "high": "83654.167",
    "low": "83654.088",
    "close": "83654.167",
    "pip_size": 3,
    "symbol": NATIVE,
    "id": "c8733459-e2e2-7049-4630-6b7ea719966a",
}

T0 = 1790708400  # 2026-09-29T20:20:00Z, minute-aligned


def _frame(**over: object) -> str:
    # WHY `default=str`: the bad-data cases pass Decimal objects as overrides (a zero price,
    # an inverted low). json.dumps refuses Decimal, so without this the helper itself raises
    # TypeError and the test errors instead of asserting the Drop it is actually about.
    return json.dumps({**REAL_OHLC, **over}, default=str)


def _ingestor() -> BrokerCandleIngestor:
    return BrokerCandleIngestor(instrument_id=INSTR, broker=PROVIDER)


def _at(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


# ============================ parsing =========================================
# Edge: OHLC arrive as JSON *strings* on the live feed. `parse_float=Decimal` does NOT
# cover this - a JSON string stays a str and would otherwise poison the money path.
def test_live_frame_ohlc_are_strings_and_parse_to_exact_decimal() -> None:
    parsed = parse_ohlc_frame(_frame())
    assert not isinstance(parsed, Drop)
    # Hand calc: the wire literal must survive verbatim, not via float.
    assert parsed["open"] == Decimal("83654.088")
    assert parsed["high"] == Decimal("83654.167")
    assert parsed["close"] == Decimal("83654.167")
    assert isinstance(parsed["open"], Decimal)


def test_history_candles_ohlc_are_numbers_and_also_parse_to_exact_decimal() -> None:
    """The history endpoint sends NUMBERS. Both wire shapes must land on the same Decimal."""
    history = json.dumps({
        "candles": [{"epoch": T0, "open": 83654.088, "high": 83654.167,
                     "low": 83654.088, "close": 83654.167}],
    })
    parsed = parse_ohlc_frame(history)
    assert not isinstance(parsed, Drop)
    # Hand calc: same value as the string form above - 83654.088 either way.
    assert parsed["open"] == Decimal("83654.088")


def test_wire_equality_is_decimal_never_string() -> None:
    """MEASURED 2026-09-29: live sent low="83589.140", history sent low="83589.14".
    Same number, different text. A string compare calls that a mismatch and would hold
    every broker bar for review forever."""
    assert Decimal("83589.140") == Decimal("83589.14")
    # WHY bound to names: written inline, ruff reads two differing literals as a constant
    # comparison and "simplifies" the guard away. The guard is the point of the test.
    live_text, history_text = "83589.140", "83589.14"
    assert live_text != history_text, "the trap this guards"


# ============================ bucket / finality ===============================
# Hand calc: open_time 1790708400 -> 19:00:00Z, interval_end 19:01:00Z.
def test_interval_start_and_end_derive_from_open_time_not_epoch() -> None:
    ing = _ingestor()
    emitted = ing.on_frame(_frame(), received_at=_at(T0 + 30))
    assert emitted is None  # first frame buffers, it does not emit
    candle = ing.close()
    assert candle is not None
    assert candle.interval_start == datetime(2026, 9, 29, 19, 0, tzinfo=UTC)
    assert candle.interval_end == datetime(2026, 9, 29, 19, 1, tzinfo=UTC)
    assert candle.expected_duration_ms == 60000
    # epoch was 1790708822 (19:07:02) - if we had keyed off it the bar would be wrong.
    assert candle.interval_start != _at(REAL_OHLC["epoch"])


def test_open_time_not_minute_aligned_is_dropped() -> None:
    ing = _ingestor()
    result = ing.on_frame(_frame(open_time=T0 + 37), received_at=_at(T0 + 40))
    assert isinstance(result, Drop)
    assert result.reason.value == "invalid_timestamp"


def test_future_open_time_is_dropped() -> None:
    ing = _ingestor()
    result = ing.on_frame(_frame(open_time=T0 + 600), received_at=_at(T0))
    assert isinstance(result, Drop)
    assert result.reason.value == "future_timestamp"


def test_first_frame_seen_mid_minute_is_accepted_not_backfilled() -> None:
    """We connected at :37. The venue's open is authoritative; we do NOT invent the
    missing 37 seconds. Any attempt to back-fill them would be fabricated OHLC.

    CORRECTED 2026-09-29: this originally passed `open_time=T0+30`, which is not
    minute-aligned and therefore tested MISALIGNMENT, not mid-minute arrival. "We joined
    late" is about WHEN the frame was received, not about a bucket key that is not on a
    minute boundary — that is a different, separately-tested edge.
    """
    ing = _ingestor()
    result = ing.on_frame(_frame(open_time=T0), received_at=_at(T0 + 37))
    assert result is None  # buffered, not dropped, despite arriving 37s into the minute
    candle = ing.close()
    assert candle is not None
    assert Decimal(str(candle.open)) == Decimal("83654.088")
    assert candle.interval_start == _at(T0)  # the FULL minute, not the part we witnessed


def test_two_consecutive_minutes_emits_the_earlier_and_buffers_the_later() -> None:
    ing = _ingestor()
    assert ing.on_frame(_frame(open_time=T0), received_at=_at(T0 + 5)) is None
    emitted = ing.on_frame(_frame(open_time=T0 + 60), received_at=_at(T0 + 65))
    assert emitted is not None, "crossing into a new minute must finalise the previous"
    assert emitted.interval_start == _at(T0)
    pending = ing.close()
    assert pending is not None
    assert pending.interval_start == _at(T0 + 60)


def test_frame_for_an_already_emitted_minute_is_ignored_and_counted() -> None:
    """A late/revised frame for a minute we already wrote. ON CONFLICT DO NOTHING would
    silently drop the rewrite, so accepting it would make the store lie."""
    ing = _ingestor()
    ing.on_frame(_frame(open_time=T0), received_at=_at(T0 + 5))
    ing.on_frame(_frame(open_time=T0 + 60), received_at=_at(T0 + 65))
    late = ing.on_frame(_frame(open_time=T0, close="99999.0"), received_at=_at(T0 + 70))
    assert late is None, "must not re-open or re-emit a closed bucket"
    assert ing.late_frame_count == 1


def test_out_of_order_older_minute_is_dropped_never_reopens_a_closed_bucket() -> None:
    ing = _ingestor()
    ing.on_frame(_frame(open_time=T0 + 60), received_at=_at(T0 + 65))
    result = ing.on_frame(_frame(open_time=T0), received_at=_at(T0 + 66))
    assert isinstance(result, Drop)
    # The only live bucket is still the 19:01 one - the late 20:20 frame must not have
    # evicted it or opened a second bucket behind it.
    pending = ing.close()
    assert pending is not None
    assert pending.interval_start == _at(T0 + 60)
    assert ing.close() is None, "a second close must not re-emit anything"


def test_close_with_nothing_buffered_returns_none_not_an_error() -> None:
    ing = _ingestor()
    assert ing.close() is None


# ============================ bad data ========================================
@pytest.mark.parametrize("bad_low,why", [
    (Decimal(99999), "inverted: low above high"),
    (Decimal(0), "zero price"),
    (Decimal(-1), "negative price"),
])
def test_inverted_or_non_positive_prices_are_dropped(bad_low: Decimal, why: str) -> None:
    ing = _ingestor()
    result = ing.on_frame(
        _frame(high=Decimal(1), low=bad_low, open=Decimal(1), close=Decimal(1)),
        received_at=_at(T0 + 5),
    )
    assert isinstance(result, Drop), why


def test_missing_open_time_is_dropped() -> None:
    frame = {k: v for k, v in REAL_OHLC.items() if k != "open_time"}
    result = _ingestor().on_frame(json.dumps(frame), received_at=_at(T0 + 5))
    assert isinstance(result, Drop)
    assert result.reason.value == "missing_field"


def test_missing_any_ohlc_field_is_dropped() -> None:
    for field in ("open", "high", "low", "close"):
        frame = {k: v for k, v in REAL_OHLC.items() if k != field}
        result = _ingestor().on_frame(json.dumps(frame), received_at=_at(T0 + 5))
        assert isinstance(result, Drop), f"missing {field} must drop"
        assert result.reason.value == "missing_field"


def test_missing_epoch_is_not_fatal_because_open_time_is_the_key() -> None:
    frame = {k: v for k, v in REAL_OHLC.items() if k != "epoch"}
    ing = _ingestor()
    assert ing.on_frame(json.dumps(frame), received_at=_at(T0 + 5)) is None
    candle = ing.close()
    assert candle is not None
    assert candle.interval_start == _at(T0)


# ============================ broker behaviour ================================
def test_quality_status_is_broker_not_complete() -> None:
    """COMPLETE previously meant 'our accumulation found no hole' and we PROVED it wrong -
    a bar with tick_count=0 still got labelled COMPLETE. 'BROKER' states the source."""
    ing = _ingestor()
    ing.on_frame(_frame(), received_at=_at(T0 + 5))
    candle = ing.close()
    assert candle is not None
    assert candle.quality_status == "BROKER"


def test_no_tick_rounding_is_applied_to_prices() -> None:
    """The tick_size 0.01-vs-0.001 defect existed because we second-guessed the venue.
    This slice must store the wire value verbatim, at full precision."""
    ing = _ingestor()
    ing.on_frame(_frame(close="83917.431"), received_at=_at(T0 + 5))
    candle = ing.close()
    assert candle is not None
    # Hand calc: 83917.431 must survive; 0.01-rounding would give 83917.43.
    assert Decimal(str(candle.close)) == Decimal("83917.431")
    assert Decimal(str(candle.close)) != Decimal("83917.43")


# ============================ spread (bid - ask, literal) ======================
def test_spread_is_ask_minus_bid_and_positive_for_a_normal_market() -> None:
    """LOCKED 2026-09-29: spread = ask - bid, POSITIVE on a normal market.

    This matches `normalize.py:114-115` and `candles.py:146`, which already compute it this
    way, so the live tick path, the legacy builder and this slice cannot disagree about the
    sign of the same number. Under the earlier `bid - ask` wording min/max and the
    crossed-quote test both inverted, and `spread > threshold` never fired.

    Hand calc: ask 1.13401 - bid 1.13399 = 0.00002.
    """
    ing = _ingestor()
    ing.on_frame(_frame(open_time=T0), received_at=_at(T0 + 5))   # opens minute T0
    ing.on_spread_sample(Decimal("1.13399"), Decimal("1.13401"), _at(T0 + 10))
    # The T0 candle is RETURNED by the boundary-crossing frame, not by close(): close()
    # would hand back T0+60, which never had a spread sample at all.
    candle = ing.on_frame(_frame(open_time=T0 + 60), received_at=_at(T0 + 65))
    assert candle is not None
    assert Decimal(str(candle.average_spread)) == Decimal("0.00002")
    assert Decimal(str(candle.min_spread)) == Decimal("0.00002")
    assert Decimal(str(candle.max_spread)) == Decimal("0.00002")
    assert Decimal(str(candle.average_spread)) > 0, "normal market spread is positive"


def test_spread_min_is_the_tightest_and_max_the_widest() -> None:
    """Consequence of the POSITIVE convention, stated so nobody assumes the opposite.

    Hand calc: ask 1.13400 - bid 1.13397 = 0.00003 (wide); 1.13400 - 1.13399 = 0.00001
    (narrow). min_spread = 0.00001 (tightest), max_spread = 0.00003 (widest).
    """
    ing = _ingestor()
    ing.on_frame(_frame(open_time=T0), received_at=_at(T0 + 5))
    ing.on_spread_sample(Decimal("1.13397"), Decimal("1.13400"), _at(T0 + 10))  # 0.00003
    ing.on_spread_sample(Decimal("1.13399"), Decimal("1.13400"), _at(T0 + 20))  # 0.00001
    candle = ing.on_frame(_frame(open_time=T0 + 60), received_at=_at(T0 + 65))
    assert candle is not None
    assert Decimal(str(candle.min_spread)) == Decimal("0.00001"), "min == tightest"
    assert Decimal(str(candle.max_spread)) == Decimal("0.00003"), "max == widest"
    assert Decimal(str(candle.average_spread)) == Decimal("0.00002")


def test_spread_is_all_or_none_when_any_tick_lacks_a_side() -> None:
    """All-or-none (candles.py:169-175 semantics). One valid tick must NOT produce a
    partial average - that would be a statistic over a sample we did not measure."""
    ing = _ingestor()
    ing.on_frame(_frame(open_time=T0), received_at=_at(T0 + 5))
    ing.on_spread_sample(Decimal("1.13399"), Decimal("1.13401"), _at(T0 + 10))
    ing.on_spread_sample(None, Decimal("1.13401"), _at(T0 + 20))
    candle = ing.on_frame(_frame(open_time=T0 + 60), received_at=_at(T0 + 65))
    assert candle is not None
    assert candle.interval_start == _at(T0), "this is minute T0, the one we sampled"
    assert candle.average_spread is None
    assert candle.min_spread is None
    assert candle.max_spread is None


def test_minute_with_no_ticks_has_none_spread_but_ohlc_is_still_written() -> None:
    """OHLC came from the broker; the absence of our own tick samples says nothing about
    whether the bar is real. Missing spread must not suppress the bar."""
    ing = _ingestor()
    ing.on_frame(_frame(), received_at=_at(T0 + 5))
    candle = ing.close()
    assert candle is not None
    assert candle.average_spread is None
    assert Decimal(str(candle.close)) == Decimal("83654.167")


def test_crossed_quote_is_dropped_and_never_yields_a_negative_spread() -> None:
    """Under ask - bid the GARBAGE case yields a NEGATIVE result, and a negative result is
    therefore the thing that must never be stored. So the validity test is `ask >= bid`."""
    ing = _ingestor()
    ing.on_frame(_frame(open_time=T0), received_at=_at(T0 + 5))
    ing.on_spread_sample(Decimal("1.13500"), Decimal("1.13300"), _at(T0 + 10))  # ask < bid
    candle = ing.on_frame(_frame(open_time=T0 + 60), received_at=_at(T0 + 65))
    assert candle is not None
    assert candle.interval_start == _at(T0), "this is minute T0, the one we sampled"
    assert candle.average_spread is None, "crossed quote is garbage, never a negative spread"


def test_zero_spread_bid_equals_ask_is_still_valid() -> None:
    """bid == ask is a locked market, not a crossed one. Hand calc: 1.13400-1.13400 = 0."""
    ing = _ingestor()
    ing.on_frame(_frame(open_time=T0), received_at=_at(T0 + 5))
    ing.on_spread_sample(Decimal("1.13400"), Decimal("1.13400"), _at(T0 + 10))
    candle = ing.on_frame(_frame(open_time=T0 + 60), received_at=_at(T0 + 65))
    assert candle is not None
    assert Decimal(str(candle.average_spread)) == Decimal(0)


# ============================ adversarial =====================================
def test_open_time_as_string_is_handled_never_crashes() -> None:
    ing = _ingestor()
    result = ing.on_frame(_frame(open_time=str(T0)), received_at=_at(T0 + 5))
    assert result is None or isinstance(result, Drop)


def test_open_time_zero_is_dropped_not_stored_as_a_1970_bar() -> None:
    result = _ingestor().on_frame(_frame(open_time=0), received_at=_at(T0 + 5))
    assert isinstance(result, Drop)


def test_absurd_open_time_drops_without_datetime_overflow() -> None:
    result = _ingestor().on_frame(_frame(open_time=2 ** 53), received_at=_at(T0 + 5))
    assert isinstance(result, Drop), "must be a typed Drop, never an OverflowError traceback"


@pytest.mark.parametrize("junk", ["Infinity", "NaN", "", "1e400", "abc", "1,2"])
def test_non_numeric_prices_are_dropped_never_coerced(junk: str) -> None:
    result = _ingestor().on_frame(_frame(close=junk), received_at=_at(T0 + 5))
    assert isinstance(result, Drop), f"close={junk!r} must drop, never coerce"


def test_wrong_granularity_is_dropped_not_stored_as_a_1m_bar() -> None:
    """A 5m bar in a 1m table is a silent data corruption nobody notices for months."""
    result = _ingestor().on_frame(_frame(granularity=300), received_at=_at(T0 + 5))
    assert isinstance(result, Drop)
    # CORRECTED 2026-09-29: this asserted `missing_field`, which is a lie - the field IS
    # present, its VALUE is wrong. AGENTS.md 3.5 requires asserting a *specific* ErrorCode,
    # so it is asserted accurately: a 5m bucket key cannot address a 1m bar.
    assert result.reason.value == "invalid_timestamp"


def test_wrong_typed_ohlc_container_is_dropped_never_raises() -> None:
    result = _ingestor().on_frame(_frame(ohlc=[1, 2, 3]), received_at=_at(T0 + 5))
    assert isinstance(result, Drop)


def test_malformed_json_drops_instead_of_crashing() -> None:
    assert isinstance(_ingestor().on_frame("{not json", received_at=_at(T0 + 5)), Drop)


def test_symbol_injection_string_is_never_interpolated_into_sql() -> None:
    """AGENTS.md 3.13: every external input is schema-validated before use. The native
    symbol is carried as DATA on the parsed bar and never concatenated into SQL.

    WHY asserted on `ParsedBar.symbol` and not on the `Candle`: `Candle 2.0.0` has no
    `provider_symbol` field and is `extra="forbid"`, so a test demanding one would be asking
    for a contract change. The security property under test is that the hostile string
    survives parsing as an inert string, which is exactly what the parser holds.
    """
    evil = "'; DROP TABLE candles_1m;--"
    parsed = parse_ohlc_frame(_frame(symbol=evil))
    assert not isinstance(parsed, Drop)
    assert parsed.symbol == evil, "carried verbatim as data, never executed or normalised"
    # ...and it survives the round trip into a Candle without becoming part of the bar's
    # identity: interval_start comes from open_time alone.
    ing = _ingestor()
    ing.on_frame(_frame(symbol=evil), received_at=_at(T0 + 5))
    candle = ing.close()
    assert candle is not None
    assert candle.interval_start == _at(T0)
    with pytest.raises(ValidationError):
        Candle(**{**candle.model_dump(), "provider_symbol": evil})  # extra="forbid"


# ============================ route_frame routing =============================
def test_route_frame_routes_ohlc_to_the_candle_callback() -> None:
    """MEASURED: the broker sends msg_type=="ohlc" on the live socket and route_frame
    currently DISCARDS every non-tick msg_type. This is the seam that was throwing it away."""
    got_candles: list[str] = []
    got_ticks: list[str] = []
    result = route_frame(
        _frame(), on_tick=got_ticks.append,
        on_candle=got_candles.append, on_drop=lambda _d: None,
    )
    assert result is True
    assert len(got_candles) == 1
    assert got_ticks == []


def test_route_frame_still_routes_ticks_and_ignores_ping() -> None:
    ticks: list[str] = []
    candles: list[str] = []
    route_frame(json.dumps({"msg_type": "tick", "tick": {"epoch": T0, "quote": "1.0"}}),
                on_tick=ticks.append, on_candle=candles.append, on_drop=lambda _d: None)
    route_frame(json.dumps({"msg_type": "ping"}),
                on_tick=ticks.append, on_candle=candles.append, on_drop=lambda _d: None)
    assert len(ticks) == 1
    assert candles == []


def test_market_is_closed_error_is_reported_not_retried() -> None:
    drops: list[Drop] = []
    route_frame(json.dumps({"msg_type": "ohlc", "error": {"code": "MarketIsClosed",
                                                           "message": "closed"}}),
                on_tick=lambda _r: None, on_candle=lambda _r: None,
                on_drop=drops.append)
    assert len(drops) == 1
    assert drops[0].reason.value in {"missing_field", "invalid_json"}


# ============================ contract surface ================================
def test_candle_carries_no_tick_count_after_the_2_0_0_bump() -> None:
    """AGENTS.md 5: a frozen contract change that is not bumped fails the snapshot test.
    This asserts the field is GONE from the model, which is the whole point of the slice -
    it used to default to 0 and be written as a fabricated 1 on every backfilled row."""
    ing = _ingestor()
    ing.on_frame(_frame(), received_at=_at(T0 + 5))
    candle = ing.close()
    assert candle is not None
    assert "tick_count" not in type(candle).model_fields
    assert "tick_count" not in candle.model_dump()


def test_candle_is_frozen_and_forbids_extra_fields() -> None:
    """AGENTS.md 3.5: a test may not assert a blind `Exception`. The specific pydantic error
    type is asserted, so this cannot pass on an unrelated crash during attribute assignment."""
    ing = _ingestor()
    ing.on_frame(_frame(), received_at=_at(T0 + 5))
    candle = ing.close()
    assert candle is not None
    with pytest.raises(ValidationError) as excinfo:
        candle.close = 1.0  # type: ignore[misc]
    assert excinfo.value.errors()[0]["type"] == "frozen_instance", (
        "frozen-ness must be enforced by the model, not by an incidental error"
    )
    # extra="forbid": an unknown field must be refused too, not silently absorbed.
    with pytest.raises(ValidationError):
        Candle(**{**candle.model_dump(), "not_a_real_field": 1})


# ================== REQUIRED INVARIANTS I-1 / I-2 =============================
# Added 2026-09-29 as condition 4 of the human's CONDITIONAL LGTM ("one particularly
# important test" + the duplicate-write guard). These are REQUIRED, not optional extras.
# Spec: docs/specs/m2.13_broker_candle_ingest.spec.md -> "## Required invariants I-1 / I-2"
#
# The module STILL DOES NOT EXIST, so this file is still honestly RED. Adding a test to a
# RED file is normal Step 3 work; making it pass is Step 5 and is blocked until the human
# reviews these two specifically.

def test_i1_live_and_history_ohlc_are_exactly_equal_after_decimal_normalisation() -> None:
    """I-1: ONE broker candle, TWO transports, EXACTLY equal OHLC after normalisation.

    WHY this is the invariant that justifies the entire slice: M2.13 exists because two
    producers disagreed on the same minute (measured 2026-09-29: 3 of 202 live-built bars).
    If live and history do not agree, the migration has only replaced one divergence with
    another - and G0 would then validate a market we do not actually trade.

    The asymmetry is the REAL case, not a contrived one: the live feed sent low="83589.140"
    where history sent low="83589.14". Same number, different text.
    """
    ot = 1790709000  # 2026-09-29T20:30:00Z, minute-aligned
    live = json.dumps({
        "msg_type": "ohlc", "open_time": ot, "epoch": ot + 59, "granularity": 60,
        "symbol": NATIVE, "pip_size": 3, "id": "c8733459-e2e2-7049-4630-6b7ea719966a",
        # trailing zeros exactly as the live feed spelled them
        "open": "83589.100", "high": "83589.240", "low": "83589.140", "close": "83589.230",
    })
    # History sends JSON NUMBERS, and none of them carries the trailing zero.
    history = json.dumps({
        "candles": [{"epoch": ot, "open": 83589.1, "high": 83589.24,
                     "low": 83589.14, "close": 83589.23}],
    })

    a = parse_ohlc_frame(live)
    b = parse_ohlc_frame(history)
    assert not isinstance(a, Drop)
    assert not isinstance(b, Drop)

    # NEGATIVE CONTROL FIRST. Without it this test could pass even against an implementation
    # that compares wire strings, provided the two feeds happened to spell a field the same.
    # This is the trap that produced two false "broker absent" audits earlier in the project.
    # Read back out of the two payloads above, so the premise is about THIS test's data
    # and not about a pair of literals that merely happen to differ.
    live_ohlc = json.loads(live)
    history_bar = json.loads(history)["candles"][0]
    for field in ("open", "low"):
        live_wire, history_wire = live_ohlc[field], str(history_bar[field])
        assert live_wire != history_wire, (
            f"premise: the transports must spell `{field}` differently as text, "
            f"otherwise this test proves nothing about normalisation"
        )

    for field in ("open", "high", "low", "close"):
        # Hand calc: Decimal("83589.140") == Decimal("83589.14") -> True, exactly.
        assert a[field] == b[field], f"{field} must be EXACTLY equal after normalisation"
        assert isinstance(a[field], Decimal), f"{field} from live must stay Decimal"
        assert isinstance(b[field], Decimal), f"{field} from history must stay Decimal"

    # Pinned values, so an implementation returning the same wrong thing on both paths
    # (e.g. None == None) cannot satisfy the loop above.
    assert a["low"] == Decimal("83589.140")
    assert b["low"] == Decimal("83589.14")
    assert a["close"] == b["close"] == Decimal("83589.230")


def test_i2_repeated_forming_frames_emit_exactly_one_candle() -> None:
    """I-2: a forming feed yields EXACTLY ONE durable candle, keyed on open_time = T.

    The broker re-sends the same `open_time` ~60x per minute (measured). Without an explicit
    collapse a naive implementation emits 60 duplicate rows for that minute - and
    ON CONFLICT DO NOTHING hides them, so the store looks right while the producer has no
    evidence the minute was ever finalised. Hence EXACTLY 1, not >= 1.
    """
    ing = _ingestor()
    emitted: list[Candle] = []

    # The forming minute T, sent twice, with a DIFFERENT close each time.
    assert ing.on_frame(_frame(open_time=T0, close="83654.100"),
                        received_at=_at(T0 + 5)) is None
    assert ing.on_frame(_frame(open_time=T0, close="83654.167"),
                        received_at=_at(T0 + 30)) is None

    # Crossing into T+60 finalises T.
    crossed = ing.on_frame(_frame(open_time=T0 + 60), received_at=_at(T0 + 65))
    assert crossed is not None
    emitted.append(crossed)

    assert len(emitted) == 1, "exactly one durable candle for minute T"
    # Hand calc: keyed on open_time = T (20:20:00Z), never on epoch (T0+59 -> 20:20:59Z).
    assert emitted[0].interval_start == _at(T0)
    assert emitted[0].interval_end == _at(T0 + 60)
    # The LAST forming value is the final one - not the first, and not an average.
    assert Decimal(str(emitted[0].close)) == Decimal("83654.167")
    assert ing.late_frame_count == 0, (
        "a repeat inside a STILL-OPEN minute is not a late frame; only a frame arriving "
        "AFTER emission is late. Counting these would report ~60 false lates per minute."
    )

    # close() emits the buffered T+60; a second close() must emit nothing.
    pending = ing.close()
    assert pending is not None
    assert pending.interval_start == _at(T0 + 60)
    assert ing.close() is None, "a second close must not re-emit the same minute"
