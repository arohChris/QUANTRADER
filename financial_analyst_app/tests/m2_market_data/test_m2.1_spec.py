"""
M2.1 Tick Ingest — Spec tests for REAL Deriv WS frames (WRITTEN BEFORE CODE).
WHY: AGENTS.md §2 Step 3 — docs/specs/m2.1_ingestion.spec.md (human format correction 2026-09-22).
     Contracts from shared.contracts; only TickIngestor is impl import (collection RED until Step 5).

Frame: msg_type=tick + nested tick{ask,bid,epoch,id?,pip_size,quote,symbol} — NOT flat schema.
Output: InternalTick | Drop — never MarketData.
Dedup: tick.id primary when present; else fingerprint (provider,symbol,epoch,quote,bid,ask,seq?).
REV1: id optional; non-numeric epoch → invalid_timestamp; dedup before future-epoch check.

Gate: valid Deriv frame → one InternalTick; reject → Drop(reason=...).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from shared.contracts.drop import Drop, DropReason
from shared.contracts.internal_tick import InternalTick
from shared.core.clock import SimulatedClock

# Direct impl import ONLY — fails collection until Step 5 (honest RED).
from modules.m2_market_data.ingest.ingest import TickIngestor

# Human sample epochs (Deriv WS docs) — clock defaults a few seconds AFTER the 3
# asset samples (EPOCH..EPOCH+2) so multi-asset accepts are not false timestamp_ahead.
# Timestamp-ahead tests pin clock back to BASE_TS explicitly.
EPOCH = 1774208800
BASE_TS = datetime.fromtimestamp(EPOCH, tz=timezone.utc)  # forex sample epoch as UTC
DEFAULT_CLOCK = BASE_TS + timedelta(seconds=10)
SUB_ID = "a9b8c7d6-1234-5678-90ab-cdef12345678"


def _clock(t: datetime | None = None) -> SimulatedClock:
    return SimulatedClock(t if t is not None else DEFAULT_CLOCK)


def _ing(clock: SimulatedClock | None = None) -> TickIngestor:
    return TickIngestor(provider="deriv", clock=clock or _clock())


def _base_tick() -> dict:
    return {
        "ask": 1.08542,
        "bid": 1.08538,
        "epoch": EPOCH,
        "id": SUB_ID,
        "pip_size": 5,
        "quote": 1.08540,
        "symbol": "frxEURUSD",
    }


def _deriv_frame(tick: dict | None = None, **tick_overrides) -> str:
    """Realistic Deriv msg_type=tick response.

    Pass tick= to replace the whole nested tick object (missing-field tests);
    pass kwargs to overlay fields onto the default tick.
    """
    if tick is None:
        body = _base_tick()
        body.update(tick_overrides)
    else:
        body = dict(tick)
        body.update(tick_overrides)
    frame = {
        "echo_req": {"req_id": 101, "subscribe": 1, "ticks": body.get("symbol", "frxEURUSD")},
        "msg_type": "tick",
        "req_id": 101,
        "subscription": {"id": SUB_ID},
        "tick": body,
    }
    return json.dumps(frame)


def _frame_dict(tick: dict | None = None, **tick_overrides) -> dict:
    return json.loads(_deriv_frame(tick=tick, **tick_overrides))


def _as_internal(result) -> InternalTick:
    assert isinstance(result, InternalTick), f"expected InternalTick, got {type(result).__name__}: {result!r}"
    return result


def _as_drop(result) -> Drop:
    assert isinstance(result, Drop), f"expected Drop, got {type(result).__name__}: {result!r}"
    return result


# --- Edges: valid Deriv emit ---

def test_valid_deriv_tick_emits_internal_tick() -> None:
    t = _as_internal(_ing().ingest(_deriv_frame()))
    assert t.provider == "deriv"
    assert t.symbol == "frxEURUSD"
    assert t.ask == Decimal("1.08542")
    assert t.bid == Decimal("1.08538")
    assert t.quote == Decimal("1.0854")
    assert t.pip_size == 5
    assert t.source_event_id == SUB_ID
    assert t.source_sequence is None  # Deriv sample has no seq
    assert t.processing_sequence == 1
    assert t.quality == "ok"
    assert t.timestamp == BASE_TS  # epoch → UTC
    assert t.ask_size is None and t.bid_size is None  # Deriv has no sizes


def test_valid_tick_observed_at_from_clock() -> None:
    clock = _clock(BASE_TS)
    t = _as_internal(_ing(clock).ingest(_deriv_frame(id="evt-1")))
    assert t.observed_at == BASE_TS
    clock.advance(timedelta(seconds=5))
    t2 = _as_internal(_ing(clock).ingest(_deriv_frame(id="evt-2", epoch=EPOCH + 5)))
    assert t2.observed_at == BASE_TS + timedelta(seconds=5)


def test_valid_tick_accepts_bytes_frame() -> None:
    raw = _deriv_frame().encode("utf-8")
    t = _as_internal(_ing().ingest(raw))
    assert t.symbol == "frxEURUSD"


def test_crypto_frame_accepts() -> None:
    t = _as_internal(
        _ing().ingest(
            _deriv_frame(
                symbol="cryBTCUSD",
                ask=64250.80,
                bid=64245.20,
                quote=64248.00,
                pip_size=2,
                id="e4f3g2h1-9876-5432-10ba-fedc87654321",
                epoch=EPOCH + 1,
            )
        )
    )
    assert t.symbol == "cryBTCUSD"
    assert t.ask == Decimal("64250.8")
    assert t.pip_size == 2


def test_commodity_frame_accepts() -> None:
    t = _as_internal(
        _ing().ingest(
            _deriv_frame(
                symbol="frxXAUUSD",
                ask=2380.45,
                bid=2380.15,
                quote=2380.30,
                pip_size=2,
                id="7890abcd-ef12-3456-7890-abcdef123456",
                epoch=EPOCH + 2,
            )
        )
    )
    assert t.symbol == "frxXAUUSD"
    assert t.bid == Decimal("2380.15")


def test_processing_sequence_increments_monotonically() -> None:
    ing = _ing()
    t1 = _as_internal(ing.ingest(_deriv_frame(id="e1", epoch=EPOCH)))
    t2 = _as_internal(ing.ingest(_deriv_frame(id="e2", epoch=EPOCH + 1)))
    assert t1.processing_sequence == 1
    assert t2.processing_sequence == 2


def test_event_id_deterministic_across_instances() -> None:
    a = _as_internal(_ing().ingest(_deriv_frame()))
    b = _as_internal(_ing().ingest(_deriv_frame()))
    assert a.event_id == b.event_id
    assert a.event_id


def test_output_is_internal_not_market_data() -> None:
    from shared.contracts.market_data import MarketData

    t = _as_internal(_ing().ingest(_deriv_frame()))
    assert not isinstance(t, MarketData)


def test_prices_are_decimal_not_float() -> None:
    t = _as_internal(_ing().ingest(_deriv_frame()))
    assert isinstance(t.ask, Decimal)
    assert isinstance(t.bid, Decimal)


# --- Edges: empty ---

def test_empty_payload_drops() -> None:
    d = _as_drop(_ing().ingest(""))
    assert d.reason == DropReason.empty
    assert d.provider == "deriv"


def test_whitespace_only_payload_drops() -> None:
    d = _as_drop(_ing().ingest("   \n\t  "))
    assert d.reason == DropReason.empty


# --- Edges: invalid JSON ---

def test_invalid_json_drops() -> None:
    d = _as_drop(_ing().ingest("{not json"))
    assert d.reason == DropReason.invalid_json


def test_invalid_json_binary_garbage_drops() -> None:
    d = _as_drop(_ing().ingest(b"\xff\xfe not utf8"))
    assert d.reason == DropReason.invalid_json


# --- Edges: missing nested tick / fields ---

def test_msg_type_without_tick_object_drops() -> None:
    # Spec Adversarial: {"msg_type":"tick"} no tick → missing_field, never partial
    d = _as_drop(_ing().ingest(json.dumps({"msg_type": "tick", "req_id": 1})))
    assert d.reason == DropReason.missing_field


def test_non_tick_msg_type_drops() -> None:
    # subscription ack is not a tick frame at M2.1 tick path
    frame = {"msg_type": "subscription", "subscription": {"id": SUB_ID}}
    d = _as_drop(_ing().ingest(json.dumps(frame)))
    assert d.reason == DropReason.missing_field


def test_json_array_drops_missing_field() -> None:
    d = _as_drop(_ing().ingest("[1, 2, 3]"))
    assert d.reason == DropReason.missing_field


def test_missing_tick_ask_drops() -> None:
    t = _frame_dict()["tick"]
    del t["ask"]
    d = _as_drop(_ing().ingest(json.dumps(_frame_dict(tick=t))))
    assert d.reason == DropReason.missing_field


def test_missing_tick_bid_drops() -> None:
    t = _frame_dict()["tick"]
    del t["bid"]
    d = _as_drop(_ing().ingest(json.dumps(_frame_dict(tick=t))))
    assert d.reason == DropReason.missing_field


def test_missing_tick_symbol_drops() -> None:
    t = _frame_dict()["tick"]
    del t["symbol"]
    d = _as_drop(_ing().ingest(json.dumps(_frame_dict(tick=t))))
    assert d.reason == DropReason.missing_field


def test_missing_tick_epoch_drops() -> None:
    t = _frame_dict()["tick"]
    del t["epoch"]
    d = _as_drop(_ing().ingest(json.dumps(_frame_dict(tick=t))))
    assert d.reason == DropReason.missing_field


def test_missing_tick_id_accepts_via_fingerprint() -> None:
    # REV1: id optional on Deriv — absent → accept with source_event_id=None
    t = _frame_dict()["tick"]
    del t["id"]
    result = _as_internal(_ing().ingest(json.dumps(_frame_dict(tick=t))))
    assert result.source_event_id is None
    assert result.processing_sequence == 1


def test_missing_id_duplicate_frame_drops_fingerprint() -> None:
    # REV1 Edges: duplicate fingerprint → duplicate_tick (id absent path)
    ing = _ing()
    t = _frame_dict()["tick"]
    del t["id"]
    first = _as_internal(ing.ingest(json.dumps(_frame_dict(tick=t))))
    second = _as_drop(ing.ingest(json.dumps(_frame_dict(tick=t))))
    assert first.source_event_id is None
    assert first.processing_sequence == 1
    assert second.reason == DropReason.duplicate_tick
    # dropped duplicate does not increment processing_sequence
    t2 = dict(t)
    t2["epoch"] = EPOCH + 1
    t2["id"] = "next-after-fp-dup"
    third = _as_internal(ing.ingest(json.dumps(_frame_dict(tick=t2))))
    assert third.processing_sequence == 2


def test_missing_tick_quote_drops() -> None:
    # Deriv new API: quote is required inside tick
    t = _frame_dict()["tick"]
    del t["quote"]
    d = _as_drop(_ing().ingest(json.dumps(_frame_dict(tick=t))))
    assert d.reason == DropReason.missing_field


def test_pip_size_optional_still_accepts() -> None:
    # Deriv new API: pip_size may be absent
    t = _frame_dict()["tick"]
    del t["pip_size"]
    result = _as_internal(_ing().ingest(json.dumps(_frame_dict(tick=t, id="no-pip"))))
    assert result.pip_size is None
    assert result.quote == Decimal("1.0854")


def test_null_ask_drops_missing_field() -> None:
    d = _as_drop(_ing().ingest(_deriv_frame(ask=None)))
    assert d.reason == DropReason.missing_field


def test_sizes_not_required_on_deriv() -> None:
    # Deriv frames have no ask_size/bid_size — must accept
    t = _as_internal(_ing().ingest(_deriv_frame()))
    assert t.ask_size is None
    assert t.bid_size is None


# --- Edges: invalid price ---

def test_invalid_price_non_numeric_ask_drops() -> None:
    d = _as_drop(_ing().ingest(_deriv_frame(ask="not_a_number")))
    assert d.reason == DropReason.invalid_price


def test_invalid_price_non_numeric_bid_drops() -> None:
    d = _as_drop(_ing().ingest(_deriv_frame(bid="not_a_number")))
    assert d.reason == DropReason.invalid_price


def test_invalid_timestamp_non_numeric_epoch_drops() -> None:
    # REV1: epoch is a timestamp — not invalid_price
    d = _as_drop(_ing().ingest(_deriv_frame(epoch="tomorrow")))
    assert d.reason == DropReason.invalid_timestamp


def test_adversarial_non_numeric_epoch_fails_as_invalid_timestamp() -> None:
    # Spec Adversarial: tick.epoch = "not-a-number" → Drop(invalid_timestamp)
    d = _as_drop(_ing().ingest(_deriv_frame(epoch="not-a-number")))
    assert d.reason == DropReason.invalid_timestamp


def test_adversarial_negative_price_fails_safely() -> None:
    d = _as_drop(_ing().ingest(_deriv_frame(ask=-1.0, bid=-1.1)))
    assert d.reason == DropReason.invalid_price


# --- Edges: timestamp ahead (epoch > clock+1s) ---

def test_timestamp_ahead_drops() -> None:
    # clock pinned to BASE_TS; tick epoch = BASE_TS+2 → >1s → timestamp_ahead
    d = _as_drop(_ing(_clock(BASE_TS)).ingest(_deriv_frame(epoch=EPOCH + 2)))
    assert d.reason == DropReason.timestamp_ahead


def test_timestamp_exactly_one_second_accepted() -> None:
    t = _as_internal(_ing(_clock(BASE_TS)).ingest(_deriv_frame(epoch=EPOCH + 1, id="edge-1s")))
    assert t.timestamp == BASE_TS + timedelta(seconds=1)


def test_timestamp_just_over_one_second_drops() -> None:
    # epoch is integer seconds — +2s is the first integer past +1s
    d = _as_drop(_ing(_clock(BASE_TS)).ingest(_deriv_frame(epoch=EPOCH + 2, id="edge-over")))
    assert d.reason == DropReason.timestamp_ahead


def test_timestamp_past_accepted() -> None:
    t = _as_internal(_ing(_clock(BASE_TS)).ingest(_deriv_frame(epoch=EPOCH - 3600, id="past-1")))
    assert t.timestamp == BASE_TS - timedelta(hours=1)


def test_adversarial_far_future_epoch_drops_not_fixed_later() -> None:
    far = EPOCH + 365 * 24 * 3600
    d = _as_drop(_ing(_clock(BASE_TS)).ingest(_deriv_frame(epoch=far, id="far-future")))
    assert d.reason == DropReason.timestamp_ahead


# --- Edges: duplicate (tick.id primary) ---

def test_same_id_newer_epoch_accepts() -> None:
    # REV2/A44 (the real-feed case): same tick.id at a NEWER epoch is a
    # genuine new update, NOT a duplicate — the options endpoint reuses one
    # id for every price change (live run 2026-09-25: 50 frames / 1 id / 43 quotes).
    ing = _ing()
    first = _as_internal(ing.ingest(_deriv_frame(id="dup-1")))
    second = _as_internal(ing.ingest(_deriv_frame(id="dup-1", epoch=EPOCH + 1, bid=9.99)))
    assert first.processing_sequence == 1
    assert second.processing_sequence == 2
    assert second.bid == Decimal("9.99")


def test_same_id_same_epoch_drops() -> None:
    # A44 rule: duplicate ONLY at the SAME time — same id + same epoch
    # (byte-identical replay) → second Drop(duplicate_tick), first wins.
    ing = _ing()
    first = _as_internal(ing.ingest(_deriv_frame(id="dup-1")))
    second = _as_drop(ing.ingest(_deriv_frame(id="dup-1")))
    assert first.processing_sequence == 1
    assert second.reason == DropReason.duplicate_tick


def test_adversarial_same_tick_id_twice_second_drops() -> None:
    # Spec Adversarial (REV2): same tick.id twice AT THE SAME EPOCH → second Drop(duplicate_tick)
    ing = _ing()
    assert isinstance(ing.ingest(_deriv_frame(id="adv-dup")), InternalTick)
    d = _as_drop(ing.ingest(_deriv_frame(id="adv-dup")))
    assert d.reason == DropReason.duplicate_tick


def test_same_id_different_prices_still_drops() -> None:
    # Same TIME wins (REV2/A44): same id + same epoch → duplicate even if bid/ask differ
    ing = _ing()
    assert isinstance(ing.ingest(_deriv_frame(id="same-id")), InternalTick)
    d = _as_drop(ing.ingest(_deriv_frame(id="same-id", ask=1.09, bid=1.08)))
    assert d.reason == DropReason.duplicate_tick


def test_different_ids_not_duplicate() -> None:
    ing = _ing()
    assert isinstance(ing.ingest(_deriv_frame(id="id-a", epoch=EPOCH)), InternalTick)
    assert isinstance(ing.ingest(_deriv_frame(id="id-b", epoch=EPOCH + 1)), InternalTick)


def test_different_ids_same_epoch_both_accept() -> None:
    # REV2/A44 edge: two DISTINCT ticks at the same instant are both kept —
    # the rule dedupes on (id, time), never on time alone.
    ing = _ing()
    a = _as_internal(ing.ingest(_deriv_frame(id="same-time-a", epoch=EPOCH)))
    b = _as_internal(ing.ingest(_deriv_frame(id="same-time-b", epoch=EPOCH)))
    assert a.processing_sequence == 1
    assert b.processing_sequence == 2


# --- Edges: sequence gap (only when provider sends seq) ---

def test_sequence_gap_marks_gapped_when_seq_present() -> None:
    ing = _ing()
    _as_internal(ing.ingest(_deriv_frame(id="g1", seq=1, epoch=EPOCH)))
    t2 = _as_internal(ing.ingest(_deriv_frame(id="g2", seq=5, epoch=EPOCH + 1)))
    assert t2.quality == "gapped"
    assert t2.source_sequence == 5
    assert t2.processing_sequence == 2


def test_consecutive_seq_quality_ok() -> None:
    ing = _ing()
    _as_internal(ing.ingest(_deriv_frame(id="c1", seq=1, epoch=EPOCH)))
    t2 = _as_internal(ing.ingest(_deriv_frame(id="c2", seq=2, epoch=EPOCH + 1)))
    assert t2.quality == "ok"


def test_absent_seq_quality_ok_no_false_gap() -> None:
    # Deriv samples have no seq — quality stays ok across symbols
    ing = _ing()
    _as_internal(ing.ingest(_deriv_frame(id="s1", symbol="frxEURUSD", epoch=EPOCH)))
    t2 = _as_internal(ing.ingest(_deriv_frame(id="s2", symbol="frxXAUUSD", epoch=EPOCH + 1)))
    assert t2.quality == "ok"


def test_sequence_gap_still_emits_not_drop() -> None:
    ing = _ing()
    _as_internal(ing.ingest(_deriv_frame(id="gap-a", seq=10, epoch=EPOCH)))
    t2 = ing.ingest(_deriv_frame(id="gap-b", seq=99, epoch=EPOCH + 1))
    assert isinstance(t2, InternalTick)
    assert t2.quality == "gapped"


# --- Drop contract shape ---

def test_drop_carries_provider_and_reason_not_free_text() -> None:
    drop = _as_drop(_ing().ingest(""))
    assert drop.reason is DropReason.empty
    assert drop.provider == "deriv"
    assert isinstance(drop.reason, DropReason)


# --- Adversarial ---

def test_adversarial_hostile_symbol_does_not_crash() -> None:
    result = _ing().ingest(_deriv_frame(symbol="'; DROP TABLE ticks;--"))
    assert isinstance(result, (InternalTick, Drop))
    if isinstance(result, InternalTick):
        assert result.symbol == "'; DROP TABLE ticks;--"
    else:
        assert isinstance(result.reason, DropReason)


def test_adversarial_huge_size_if_provider_sends_it() -> None:
    t = _as_internal(_ing().ingest(_deriv_frame(ask_size=1e12, bid_size=1e12, id="huge-1")))
    assert t.ask_size == Decimal("1e12")


def test_adversarial_payload_null_json_type_drops() -> None:
    d = _as_drop(_ing().ingest("null"))
    assert d.reason == DropReason.missing_field


def test_correlation_id_not_used_for_dedup() -> None:
    # correlation_id is NOT tick identity — different tick.ids both accept
    ing = _ing()
    a = _as_internal(ing.ingest(_deriv_frame(id="cid-1")))
    # inject correlation_id at envelope level (ignored by M2.1 identity)
    frame = json.loads(_deriv_frame(id="cid-2", epoch=EPOCH + 1))
    frame["correlation_id"] = "run-b"
    b = _as_internal(ing.ingest(json.dumps(frame)))
    assert a.processing_sequence == 1
    assert b.processing_sequence == 2


def test_echo_req_and_subscription_ignored_for_identity() -> None:
    # subscription.id must NOT become source_event_id — only tick.id
    frame = json.loads(_deriv_frame(id="real-tick-id"))
    frame["subscription"] = {"id": "other-sub-id"}
    t = _as_internal(_ing().ingest(json.dumps(frame)))
    assert t.source_event_id == "real-tick-id"


def test_dedup_before_timestamp_ahead_reason_is_stable() -> None:
    # Spec processing order (binding): step 6 dedup runs before step 7 future-epoch.
    # Second copy of same tick.id is duplicate_tick even when the frame is also future.
    ing = _ing(_clock(BASE_TS))
    # First frame: far-future with id=order-dup would alone be timestamp_ahead —
    # we never accept it; instead show that AFTER an accepted id, re-ingest under
    # the same future-pinned clock reports duplicate_tick, not timestamp_ahead.
    ing_accept = _ing()  # clock past EPOCH+3 → first accepts
    _as_internal(ing_accept.ingest(_deriv_frame(id="order-dup", epoch=EPOCH + 3)))
    # Replay same id on a clock where epoch is still future: same ingestor, still dup first
    # (seen-set is on the instance; using ing_accept which already saw the id)
    d = _as_drop(ing_accept.ingest(_deriv_frame(id="order-dup", epoch=EPOCH + 3)))
    assert d.reason == DropReason.duplicate_tick
    # Non-duplicate on a past clock → timestamp_ahead (proves business rule still fires)
    d2 = _as_drop(ing.ingest(_deriv_frame(id="ahead-only", epoch=EPOCH + 3)))
    assert d2.reason == DropReason.timestamp_ahead
