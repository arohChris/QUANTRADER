"""
P12 / M2.6 — Data Quality (exactly-once dedup + quality classifier) — Spec tests (WRITTEN BEFORE CODE).
WHY: AGENTS.md §2 Step 3 — tests first against docs/specs/m2.6_data_quality.spec.md
(LGTM on spec for M2.6 + 4 minors, 2026-09-23). Implementation imported only via
modules.m2_market_data.quality — collection RED until Step 5.

Contracts only until Step 5: MarketData, Tick, DataQuality, Drop/DropReason 1.5.0.
No clock / reference / watermark_registry — purity + no watermark side effect asserted.

Gate: QualityGate.on_md(md) → same|copy MarketData | Drop; sei+instrument exactly-once
else fingerprint; gapped/suspect never clear/promote; local 5% spike → suspect;
never writes watermark; never mutates frozen input.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

# Direct impl import ONLY — fails collection until Step 5 (honest RED).
from modules.m2_market_data.quality import QualityGate
from shared.contracts.drop import Drop, DropReason
from shared.contracts.market_data import DataQuality, MarketData, Tick

BASE = datetime(2026, 9, 23, 14, 2, 0, tzinfo=UTC)
NAIVE = datetime(2026, 9, 23, 14, 2, 0)  # noqa: DTZ001 - deliberate: this IS the bad input under test


def _md(
    *,
    price: float = 1.10000,
    bid: float | None = None,
    ask: float | None = None,
    exchange: datetime | None = None,
    quality: DataQuality = DataQuality.pristine,
    kind: str = "tick",
    provider: str | None = "deriv",
    broker: str = "deriv",
    watermark: int = 42,
    instrument_id: str = "FX.EURUSD",
    symbol: str = "EURUSD",
    source_event_id: str | None = None,
) -> MarketData:
    payload = Tick(
        price=price,
        bid=bid,
        ask=ask,
        spread=(ask - bid) if (bid is not None and ask is not None) else None,
    )
    return MarketData(
        instrument_id=instrument_id,
        symbol=symbol,
        broker=broker,
        kind=kind,  # type: ignore[arg-type]
        payload=payload,
        quality=quality,
        watermark=watermark,
        exchange_timestamp=exchange if exchange is not None else BASE,
        received_at=BASE,
        provider=provider,
        provider_symbol="frxEURUSD" if symbol == "EURUSD" else symbol,
        source_event_id=source_event_id,
        reference_snapshot_id="REF-2026-09-23-M26",
    )


def _gate(**kwargs) -> QualityGate:
    return QualityGate(**kwargs)


def _as_md(result) -> MarketData:
    assert isinstance(result, MarketData), f"expected MarketData, got {type(result).__name__}: {result!r}"
    return result


def _as_drop(result) -> Drop:
    assert isinstance(result, Drop), f"expected Drop, got {type(result).__name__}: {result!r}"
    return result


# --- Dedup (exactly-once) ---

def test_first_tick_accepted_and_key_stored() -> None:
    # Spec Correct 1: fresh gate, first md accept, key stored
    g = _gate()
    out = g.on_md(_md(source_event_id="evt-1"))
    assert _as_md(out).source_event_id == "evt-1"
    # second sighting on same gate must drop → proves key was stored
    assert _as_drop(g.on_md(_md(source_event_id="evt-1"))).reason == DropReason.duplicate_tick


def test_same_sei_same_instrument_duplicate_drops() -> None:
    # Spec Correct 2 + Dedup key: same sei + instrument_id → Drop(duplicate_tick)
    g = _gate()
    g.on_md(_md(source_event_id="evt-dup", instrument_id="FX.EURUSD"))
    out = g.on_md(_md(source_event_id="evt-dup", instrument_id="FX.EURUSD"))
    d = _as_drop(out)
    assert d.reason == DropReason.duplicate_tick
    assert d.provider == "deriv"


def test_same_sei_different_instrument_both_accept() -> None:
    # Spec Correct 3: instrument_id in key — same sei two instruments → both accept
    g = _gate()
    a = _as_md(g.on_md(_md(source_event_id="evt-x", instrument_id="FX.EURUSD", symbol="EURUSD")))
    b = _as_md(g.on_md(_md(source_event_id="evt-x", instrument_id="FX.XAUUSD", symbol="XAUUSD")))
    assert a.instrument_id == "FX.EURUSD"
    assert b.instrument_id == "FX.XAUUSD"


def test_missing_sei_fingerprint_duplicate_drops() -> None:
    # Spec Correct 4: source_event_id None → fingerprint; exact same fp twice → drop
    g = _gate()
    g.on_md(_md(source_event_id=None, price=1.10000, exchange=BASE))
    out = g.on_md(_md(source_event_id=None, price=1.10000, exchange=BASE))
    assert _as_drop(out).reason == DropReason.duplicate_tick


def test_missing_sei_different_fingerprint_both_accept() -> None:
    # Spec Correct 4 / Adversarial: different price → different fp → both accept
    g = _gate()
    _as_md(g.on_md(_md(source_event_id=None, price=1.10000)))
    _as_md(g.on_md(_md(source_event_id=None, price=1.10001)))


def test_empty_sei_treated_as_missing_uses_fingerprint() -> None:
    # Spec Adversarial: source_event_id = "" → fingerprint path, not sei key
    g = _gate()
    g.on_md(_md(source_event_id="", price=1.10000))
    out = g.on_md(_md(source_event_id="", price=1.10000))
    assert _as_drop(out).reason == DropReason.duplicate_tick
    # "" must NOT share sei-key with a real id that happens to be absent differently:
    g2 = _gate()
    _as_md(g2.on_md(_md(source_event_id="", price=1.10001)))
    _as_md(g2.on_md(_md(source_event_id="evt-real-different-fp", price=1.10002)))


def test_duplicate_wins_over_quality_rewrite() -> None:
    # Spec Edges: identity first — second sighting with suspect quality still Drop
    g = _gate()
    g.on_md(_md(source_event_id="evt-q", quality=DataQuality.pristine))
    out = g.on_md(_md(source_event_id="evt-q", quality=DataQuality.suspect))
    assert _as_drop(out).reason == DropReason.duplicate_tick


# --- Classifier ---

def test_input_gapped_never_cleared() -> None:
    # Spec Correct 5 / classifier 1: gapped → gapped (never clear)
    g = _gate()
    out = _as_md(g.on_md(_md(source_event_id="evt-g", quality=DataQuality.gapped)))
    assert out.quality == DataQuality.gapped


def test_input_suspect_stays_suspect() -> None:
    # Spec Correct 6 / classifier 2: suspect never promoted to pristine
    g = _gate()
    out = _as_md(g.on_md(_md(source_event_id="evt-s", quality=DataQuality.suspect)))
    assert out.quality == DataQuality.suspect


def test_pristine_stays_pristine_small_move() -> None:
    # Spec Correct 8: 1% move < 5% spike_ratio → still pristine
    # Hand calc: prev=1.10000, new=1.11100, Δ=0.011, 0.011/1.1=0.01 < 0.05 → pristine
    g = _gate()
    _as_md(g.on_md(_md(source_event_id="evt-p1", price=1.10000, bid=None, ask=None)))
    out = _as_md(g.on_md(_md(source_event_id="evt-p2", price=1.11100, bid=None, ask=None)))
    assert out.quality == DataQuality.pristine


def test_pristine_spike_promotes_suspect() -> None:
    # Spec Correct 7 / classifier 4: 6% jump → suspect
    # Hand calc: prev mid 1.10000, new 1.16600, Δ=0.066, 0.066/1.10000=0.06 > 0.05 → suspect
    g = _gate()
    _as_md(g.on_md(_md(source_event_id="evt-sp1", price=1.10000, bid=None, ask=None)))
    out = _as_md(g.on_md(_md(source_event_id="evt-sp2", price=1.16600, bid=None, ask=None)))
    assert out.quality == DataQuality.suspect


def test_first_tick_no_prev_no_spike_check() -> None:
    # Spec Correct 9 / Adversarial hostile huge first price: no prev → no spike
    g = _gate()
    out = _as_md(g.on_md(_md(source_event_id="evt-huge", price=999999.0, bid=None, ask=None)))
    assert out.quality == DataQuality.pristine


def test_prev_mid_zero_skips_spike_rule() -> None:
    # Spec Edges: prev_mid == 0 → skip spike (no ZeroDivisionError / no false suspect)
    g = _gate()
    _as_md(g.on_md(_md(source_event_id="evt-z1", price=0.0, bid=None, ask=None)))
    out = _as_md(g.on_md(_md(source_event_id="evt-z2", price=1.16600, bid=None, ask=None)))
    # skip rule → leave quality as classifier steps 1–3 left it (still pristine)
    assert out.quality == DataQuality.pristine


def test_output_watermark_unchanged() -> None:
    # Spec Correct 10 / P12 gate: never writes watermark
    g = _gate()
    out = _as_md(g.on_md(_md(source_event_id="evt-w", watermark=777)))
    assert out.watermark == 777


def test_does_not_mutate_frozen_input() -> None:
    # Spec Outputs: never mutate frozen input — model_copy when quality changes
    g = _gate()
    prev = _md(source_event_id="evt-m1", price=1.10000, bid=None, ask=None, watermark=9)
    _as_md(g.on_md(prev))
    nxt = _md(source_event_id="evt-m2", price=1.16600, bid=None, ask=None, watermark=9)
    out = _as_md(g.on_md(nxt))
    assert out is not nxt
    assert nxt.quality == DataQuality.pristine  # input untouched
    assert out.quality == DataQuality.suspect
    assert out.watermark == nxt.watermark


def test_spike_ratio_invalid_constructor_valueerror() -> None:
    # Spec Edges/Adversarial: 0 < spike_ratio < 1 else ValueError at init
    with pytest.raises(ValueError):
        _gate(spike_ratio=Decimal(0))
    with pytest.raises(ValueError):
        _gate(spike_ratio=Decimal(1))


# --- Defensive / adversarial ---

def test_non_tick_payload_drops_missing_field() -> None:
    # Spec Edges: kind != tick → Drop(missing_field) (defensive; M2.3 should have)
    g = _gate()
    # kind mismatch: MarketData accepts kind="candle" only with Candle — force kind lie
    md = _md(source_event_id="evt-nt").model_copy(update={"kind": "candle"})
    out = _as_drop(g.on_md(md))
    assert out.reason == DropReason.missing_field


def test_naive_timestamp_drops_invalid_timestamp() -> None:
    # Spec Edges: naive exchange_timestamp → Drop(invalid_timestamp)
    g = _gate()
    md = _md(source_event_id="evt-naive").model_copy(update={"exchange_timestamp": NAIVE})
    out = _as_drop(g.on_md(md))
    assert out.reason == DropReason.invalid_timestamp


def test_adversarial_bid_none_vs_value_not_false_duplicate() -> None:
    # Spec Adversarial: bid=None vs bid=1.0 → different fingerprint keys → both accept
    g = _gate()
    _as_md(g.on_md(_md(source_event_id=None, price=1.10000, bid=None, ask=None, exchange=BASE)))
    _as_md(
        g.on_md(
            _md(source_event_id=None, price=1.10000, bid=1.0, ask=1.1, exchange=BASE)
        )
    )


# --- DEFECT HUNT 2026-09-27 (human: "any error is prohibited") ---
# Found while wiring this gate into the live pipeline. The gate had been implemented and
# unit-tested but NEVER CALLED, so both of these had no failing test anywhere.

def test_constant_source_event_id_with_different_epochs_all_accepted() -> None:
    """CRITICAL: on Deriv `tick.id` is the SUBSCRIPTION id, constant for a stream's life.
    Keying dedup on source_event_id ALONE made every tick after the first look like a
    duplicate, so the gate would have dropped essentially the entire feed. Invisible only
    because nothing called it. M2.1's own ingest dedup already pairs (id, epoch); this
    pins the gate to the same identity."""
    g = _gate()
    accepted = 0
    for i in range(5):
        md = _md(
            source_event_id="sub-abc123",  # constant, as Deriv sends it
            price=1.10000 + i * 0.00001,
            exchange=BASE + timedelta(seconds=i),
            watermark=100 + i,
        )
        out = g.on_md(md)
        assert isinstance(out, MarketData), f"tick {i} wrongly dropped: {out}"
        accepted += 1
    assert accepted == 5


def test_same_source_event_id_AND_same_epoch_is_still_deduped() -> None:
    """The fix must not weaken genuine exactly-once: identical (id, epoch) is a replay."""
    g = _gate()
    first = g.on_md(_md(source_event_id="sub-x", exchange=BASE, watermark=1))
    second = g.on_md(_md(source_event_id="sub-x", exchange=BASE, watermark=1))
    assert isinstance(first, MarketData)
    assert isinstance(second, Drop)
    assert second.reason == DropReason.duplicate_tick


def test_watermark_jump_marks_the_tick_gapped() -> None:
    """THE MISSING DETECTION: a dropped socket leaves a hole in `watermark`. The gate must
    downgrade the tick so CandleBuilder stamps GAP_AFFECTED and M3 skips the bar. Before
    this, gap status came only from the broker volunteering a "gapped" marker, so a lost
    tick produced a COMPLETE candle spanning missing minutes."""
    g = _gate()
    g.on_md(_md(exchange=BASE, watermark=100))
    out = g.on_md(_md(exchange=BASE + timedelta(seconds=1), watermark=110))
    assert isinstance(out, MarketData)
    assert out.quality == DataQuality.gapped
    assert g.watermark_gaps, "the hole must be recorded for audit/recovery"
    assert g.watermark_gaps[0][2:] == (101, 109)


def test_contiguous_watermark_is_not_gapped() -> None:
    """The common case must stay pristine - a gapped flag here would disable trading."""
    g = _gate()
    for i in range(6):
        out = g.on_md(
            _md(source_event_id=f"e{i}", exchange=BASE + timedelta(seconds=i), watermark=500 + i)
        )
        assert isinstance(out, MarketData)
        assert out.quality == DataQuality.pristine, f"false gap at i={i}"
    assert g.watermark_gaps == []


def test_watermark_gap_never_promotes_a_repaired_bar() -> None:
    """A healthy tick arriving AFTER a hole stays gapped. Silently repairing the bar is
    precisely the failure the whole mechanism exists to prevent (AGENTS.md 3.3)."""
    g = _gate()
    g.on_md(_md(exchange=BASE, watermark=10))
    jumped = g.on_md(_md(exchange=BASE + timedelta(seconds=1), watermark=20))
    assert isinstance(jumped, MarketData)
    assert jumped.quality == DataQuality.gapped
    after = g.on_md(_md(exchange=BASE + timedelta(seconds=2), watermark=21))
    assert isinstance(after, MarketData)
    assert after.quality == DataQuality.gapped
