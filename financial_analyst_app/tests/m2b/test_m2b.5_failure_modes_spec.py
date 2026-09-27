"""
M2b.5/M2b.6 (part 4) - the UNCOVERED failure-mode branches.

Human directive 2026-09-27: rigorous testing is mandated before coding, and every possible
failure should be tested. A coverage run showed every uncovered line in the new modules was a
FAILURE-MODE branch - an oracle that raised, a calendar that raised, a malformed resolver, a
window past the ceiling, the human reset action. Those are exactly the paths that must never
be wrong and are the easiest to get wrong, because nothing exercises them until they matter.

Each test below is named for the failure it exercises and asserts a SPECIFIC state, never
"no exception".
"""
from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from modules.m2_market_data.coverage import (
    GapLedger,
    GapRun,
    GapState,
    SessionEvidence,
    classify_window,
)
from modules.reference.calendar import TradingCalendar
from modules.reference.reference import UnknownInstrumentError
from modules.reference.standards import AssetStandardsError, load_standards, standards

MON = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
SAT = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
DERIV = "deriv"
CRYPTO, FOREX = "CRYPTO.BTCUSD", "FX.EURUSD"


def _resolver(master: dict[str, Any]):
    return lambda i: master[i]


# ==================================================== oracle blows up
def test_an_oracle_that_raises_is_treated_as_unverifiable_not_as_closed() -> None:
    """THE critical safety branch. A broker adapter that throws must not be laundered into
    'the market was shut' - that would permanently hide a real gap."""
    class Exploding:
        def evidence(self, *a, **k):
            raise ConnectionError("broker unreachable")

    rows = classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=1), oracle=Exploding())
    assert not any(r.state is GapState.CLOSED for r in rows), (
        "an exception is UNKNOWN, never CLOSED"
    )
    assert any(r.state is GapState.FILLING for r in rows)
    assert all(r.needs_verification for r in rows), "unverified gap must be escalated"
    assert all(r.evidence is not SessionEvidence.BROKER for r in rows)


def test_an_oracle_returning_none_is_treated_as_unverifiable() -> None:
    class ReturnsNone:
        def evidence(self, *a, **k) -> Any:
            return None  # type: ignore[override]

    rows = classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=1), oracle=ReturnsNone())
    assert any(r.state is GapState.FILLING for r in rows)
    assert not any(r.state is GapState.CLOSED for r in rows)


def test_a_partially_malformed_oracle_response_keeps_the_good_entries() -> None:
    """One bad tuple must not discard a whole window of good broker evidence."""
    class PartlyBad:
        def evidence(self, instrument_id, broker, start, end):
            return [
                (start, end, "not-an-enum", "maybe"),      # junk
                (None, None, SessionEvidence.BROKER, True),  # bad bounds
                (start, end, SessionEvidence.BROKER, True),  # good
                (start, end, SessionEvidence.BROKER, "yes"), # non-bool flag
            ]

    rows = classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=1), oracle=PartlyBad())
    assert any(r.evidence is SessionEvidence.BROKER for r in rows), (
        "the one well-formed entry must still be honoured"
    )
    assert any(r.state is GapState.FILLING for r in rows)


# ==================================================== calendar blows up
def test_a_calendar_that_raises_falls_back_to_the_last_resort() -> None:
    """No broker and no usable calendar: assume OPEN, never assume shut."""
    class BrokenCalendar:
        def open_intervals(self, *a, **k):
            raise RuntimeError("reference data unavailable")

    rows = classify_window(FOREX, DERIV, MON, MON + timedelta(hours=1),
                           calendar=BrokenCalendar())
    assert all(r.evidence is SessionEvidence.DEFAULT for r in rows)
    assert any(r.state is GapState.FILLING for r in rows), (
        "with no evidence at all the safe answer is 'we may have lost data'"
    )
    assert not any(r.state is GapState.CLOSED for r in rows)


def test_default_evidence_on_crypto_and_on_a_session_asset() -> None:
    """Both classes resolve to 'assume open' under DEFAULT - the asymmetry is deliberate."""
    class BrokenCalendar:
        def open_intervals(self, *a, **k):
            raise RuntimeError("down")

    for inst in (CRYPTO, FOREX):
        rows = classify_window(inst, DERIV, MON, MON + timedelta(hours=1),
                               calendar=BrokenCalendar())
        assert any(r.state is GapState.FILLING for r in rows), inst
        assert not any(r.state is GapState.CLOSED for r in rows), inst


# ==================================================== resolver shape
def test_classify_window_rejects_a_resolver_returning_a_non_mapping() -> None:
    with pytest.raises(UnknownInstrumentError, match="must return a mapping"):
        classify_window("FX.X", DERIV, MON, MON + timedelta(hours=1),
                        class_resolver=lambda i: "forex")


def test_classify_window_rejects_an_instrument_with_no_asset_class() -> None:
    with pytest.raises(UnknownInstrumentError, match="no asset_class"):
        classify_window("FX.X", DERIV, MON, MON + timedelta(hours=1),
                        class_resolver=lambda i: {"tick_size": Decimal("0.00001")})


# ==================================================== window bounds
def test_a_window_beyond_the_ceiling_is_refused() -> None:
    start = datetime(1000, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="exceeds"):
        classify_window(CRYPTO, DERIV, start, start + timedelta(days=4000))


def test_a_window_exactly_at_the_ceiling_is_accepted() -> None:
    """Boundary: the ceiling must reject MORE than it accepts, not off by one."""
    end = datetime(2026, 1, 1, tzinfo=UTC)
    start = end - timedelta(days=3660)
    rows = classify_window(CRYPTO, DERIV, start, end)
    assert rows, "a legal max-size window must still classify"


# ==================================================== interval merging edges
def test_zero_length_and_reversed_spans_are_discarded_by_the_merger() -> None:
    """A degenerate span must not become a phantom gap of zero minutes."""
    rows = classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=2),
                           missing=lambda a, b: [(a, a), (b, b), (a, b)])
    for r in rows:
        assert r.minutes() > 0, f"degenerate span leaked through as {r}"


def test_reversed_missing_span_does_not_produce_a_negative_run() -> None:
    rows = classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=2),
                           missing=lambda a, b: [(MON + timedelta(hours=1), MON)])
    for r in rows:
        assert r.end > r.start, f"run is not half-open-positive: {r}"


# ==================================================== the human reset action
def test_reopen_clears_a_permanent_gap_and_is_explicit() -> None:
    """`reopen` is the ONLY way a terminal record goes away, and it is a human action - never
    a side effect of scanning. If it silently no-ops, a wrongly-learned closure is permanent."""
    ledger = GapLedger()
    run = GapRun(CRYPTO, DERIV, MON, MON + timedelta(minutes=30), GapState.PERMANENTLY_MISSING)
    ledger.record_permanent([run], reason="vendor has no data")

    # While recorded, the window is never re-enqueued.
    assert ledger.enqueueable(classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=1))) == []

    assert ledger.reopen(CRYPTO, DERIV) == 1
    assert ledger.permanent_runs(CRYPTO, DERIV) == []
    pending = ledger.enqueueable(classify_window(CRYPTO, DERIV, MON, MON + timedelta(hours=1)))
    assert any(r.state is GapState.FILLING for r in pending), (
        "after an explicit reopen the window must be repairable again"
    )


def test_reopen_on_an_unknown_instrument_is_a_no_op_not_a_crash() -> None:
    ledger = GapLedger()
    assert ledger.reopen("CRYPTO.NEVER-SEEN", DERIV) == 0


def test_reopen_is_scoped_to_one_broker() -> None:
    ledger = GapLedger()
    ledger.record_permanent(
        [GapRun(CRYPTO, DERIV, MON, MON + timedelta(minutes=5), GapState.PERMANENTLY_MISSING)],
        reason="x")
    ledger.reopen(CRYPTO, "mt5")
    assert ledger.permanent_runs(CRYPTO, DERIV), "reopen for mt5 must not clear deriv"


def test_enqueueable_keeps_permanent_runs_visible_for_reporting() -> None:
    """A PERMANENTLY_MISSING run must not be filtered out of its own listing - the operator
    has to be able to SEE the windows that will never be repaired."""
    ledger = GapLedger()
    run = GapRun(CRYPTO, DERIV, MON, MON + timedelta(minutes=5), GapState.PERMANENTLY_MISSING)
    ledger.record_permanent([run], reason="x")
    assert run in ledger.enqueueable([run]), "reporting must not hide terminal records"


# ==================================================== standards config edges
def test_a_non_string_class_name_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.yaml"
        p.write_text("1:\n  always_open: true\n  tick_size: \"0.01\"\n"
                     "  min_qty: \"0.01\"\n  qty_step: \"0.01\"\n", encoding="utf-8")
        with pytest.raises(AssetStandardsError, match="class name"):
            load_standards(p)


def test_an_empty_class_name_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.yaml"
        p.write_text('"":\n  always_open: true\n  tick_size: "0.01"\n'
                     '  min_qty: "0.01"\n  qty_step: "0.01"\n', encoding="utf-8")
        with pytest.raises(AssetStandardsError, match="class name"):
            load_standards(p)


def test_a_class_entry_that_is_not_a_mapping_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.yaml"
        p.write_text("crypto: 5\n", encoding="utf-8")
        with pytest.raises(AssetStandardsError, match="mapping"):
            load_standards(p)


def test_a_yaml_list_at_the_top_level_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.yaml"
        p.write_text("- crypto\n- forex\n", encoding="utf-8")
        with pytest.raises(AssetStandardsError, match="mapping|non-empty"):
            load_standards(p)


def test_standards_survive_a_calendar_built_from_them() -> None:
    """Integration: the shipped standard must produce a working calendar, not just parse.
    The resolver is per-instrument on purpose - a resolver that answered 'crypto' for
    everything would make every assertion below meaningless."""
    by_class = {CRYPTO: "crypto", FOREX: "forex"}
    cal = TradingCalendar(class_resolver=lambda i: {"asset_class": by_class[i]})
    assert cal.is_open(CRYPTO, SAT) is True, "crypto trades 24/7"
    assert cal.is_open(FOREX, SAT) is False, "forex is shut Saturday"
    assert set(standards()) == {"crypto", "forex", "commodity"}
