"""
M2b.3 — Execution Profiles (descriptive cost facts) — Spec tests (WRITTEN BEFORE CODE).
WHY: AGENTS.md 2 Step 3 — tests exist first against docs/specs/m2b.3_execution_profiles.spec.md.
Implementation imported directly — collection RED (ModuleNotFoundError) until Step 5. NO
importorskip: missing impl must FAIL collection as honest RED, never skip (2 Step 6, 10).

Facts-only law (bible 02b M2b-I05): commission / spread / financing / slippage are DESCRIPTIVE
reference facts — A4 greps the impl source for "route" so routing decisions can never creep in.
Money law (AGENTS 3.1): every stored field is exact Decimal parsed from str — never a float;
H1-H3 are exact hand-calc asserts (no approx).

Covers spec Edges 1-17 (one fn each, named after the edge) + adversarial A1-A4 + A5 (extra,
NOT in spec, per 2 Step 3) + 3 hand-calcs + facade re-export/rotation seams.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

import modules.reference.execution_profiles as ep
from modules.reference import reference
from modules.reference.execution_profiles import (
    ExecutionProfile,
    SlippageParams,
    get_execution_profile,
    load_execution_profiles,
)
from shared.contracts.errors import (
    ReferenceValidationError,
    UnknownBrokerError,
    UnknownInstrumentError,
)

_UTC = UTC
_KEY = ("CRYPTO.BTCUSD", "deriv")


def _row(**over: object) -> dict:
    """One valid YAML row (master membership: CRYPTO.BTCUSD brokers [deriv, mt5])."""
    row: dict = {
        "instrument_id": "CRYPTO.BTCUSD",
        "broker": "deriv",
        "effective_from": "2026-01-01T00:00:00+00:00",
        "commission_rate": "0.0001",
        "typical_spread": "10.0",
        "financing_apy": "-0.0200",
        "slippage": {"model": "fixed_bps", "value": "0.50"},
    }
    row.update(over)
    return row


def _write_at(path: Path, rows: object) -> Path:
    path.write_text(yaml.safe_dump({"profiles": rows}), encoding="utf-8")
    return path


def _write(tmp_path: Path, rows: object) -> Path:
    return _write_at(tmp_path / "profiles.yaml", rows)


def _raw(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "profiles.yaml"
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def facade_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect the facade to a tmp config + clear LRU (spec Edge 16 rotation seam:
    module attr _PROFILES_PATH, cache_clear — mirrors reference._SNAPSHOT_PATH precedent)."""
    p = tmp_path / "profiles.yaml"
    monkeypatch.setattr(ep, "_PROFILES_PATH", p)
    ep.get_execution_profile.cache_clear()
    yield p
    ep.get_execution_profile.cache_clear()


# --- valid path (happy) -----------------------------------------------------

def test_load_valid_returns_typed_profiles(tmp_path: Path) -> None:
    loaded = load_execution_profiles(_write(tmp_path, [_row()]))
    assert list(loaded) == [_KEY]
    profiles = loaded[_KEY]
    assert len(profiles) == 1
    prof = profiles[0]
    assert isinstance(prof, ExecutionProfile)
    assert isinstance(prof.slippage, SlippageParams)
    assert prof.instrument_id == "CRYPTO.BTCUSD"
    assert prof.broker == "deriv"
    assert prof.effective_from == datetime(2026, 1, 1, tzinfo=_UTC)
    # Money law: every cost field is Decimal, never float.
    assert type(prof.commission_rate) is Decimal
    assert prof.commission_rate == Decimal("0.0001")
    assert prof.typical_spread == Decimal("10.0")
    assert prof.financing_apy == Decimal("-0.0200")
    assert prof.slippage.model == "fixed_bps"
    assert prof.slippage.value == Decimal("0.50")


# --- Edges 1-17 -------------------------------------------------------------

def test_edge_1_missing_file_fails_closed(tmp_path: Path) -> None:
    """No costs file = no backtest — never an implicit zero-cost profile."""
    with pytest.raises(ReferenceValidationError):
        load_execution_profiles(tmp_path / "missing.yaml")


def test_edge_1_top_level_not_a_mapping_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ReferenceValidationError):
        load_execution_profiles(_raw(tmp_path, "- just\n- a list\n"))


def test_edge_1_missing_profiles_key_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ReferenceValidationError):
        load_execution_profiles(_raw(tmp_path, "other: 1\n"))


def test_edge_2_empty_profiles_list_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ReferenceValidationError):
        load_execution_profiles(_write(tmp_path, []))


def test_edge_3_unknown_instrument_typed_error(facade_path: Path) -> None:
    """Caller-supplied instrument not in master → UnknownInstrumentError (master check
    BEFORE row lookup — pinned here; edge 11 covers the config-row variant at load)."""
    _write_at(facade_path, [_row()])
    with pytest.raises(UnknownInstrumentError):
        get_execution_profile("FAKE.ABC", "deriv")


def test_edge_4_pair_without_profile_typed_error(facade_path: Path) -> None:
    """Known instrument, broker has no row (headway not in BTCUSD brokers) → typed error."""
    _write_at(facade_path, [_row()])
    with pytest.raises(ReferenceValidationError, match="no execution profile"):
        get_execution_profile("CRYPTO.BTCUSD", "headway")


def test_edge_5_unknown_broker_typed_error(facade_path: Path) -> None:
    _write_at(facade_path, [_row()])
    with pytest.raises(UnknownBrokerError):
        get_execution_profile("CRYPTO.BTCUSD", "webull")


def test_edge_6_naive_effective_at_rejected(facade_path: Path) -> None:
    """UTC law (AGENTS 3.8): naive datetime must never be silently treated as UTC."""
    _write_at(facade_path, [_row()])
    with pytest.raises(ReferenceValidationError):
        get_execution_profile(
            "CRYPTO.BTCUSD",
            "deriv",
            effective_at=datetime(2026, 6, 1),  # noqa: DTZ001 — Edge 6: NAIVE IS the input
        )


def test_edge_7_before_earliest_profile_rejected(facade_path: Path) -> None:
    """Fail closed — never silently use a profile that was not yet in effect."""
    _write_at(facade_path, [_row()])
    with pytest.raises(ReferenceValidationError):
        get_execution_profile(
            "CRYPTO.BTCUSD", "deriv", effective_at=datetime(2025, 6, 1, tzinfo=_UTC)
        )


def test_edge_8_after_all_profiles_returns_latest(facade_path: Path) -> None:
    _write_at(facade_path, [_row()])
    prof = get_execution_profile(
        "CRYPTO.BTCUSD", "deriv", effective_at=datetime(3000, 1, 1, tzinfo=_UTC)
    )
    assert prof.commission_rate == Decimal("0.0001")


def test_edge_9_precedence_latest_effective_wins(tmp_path: Path) -> None:
    """Boundary: strictly before v2 → v1; exactly at v2.effective_from (<=) → v2."""
    p = _write(
        tmp_path,
        [
            _row(effective_from="2026-01-01T00:00:00+00:00", commission_rate="0.0001"),
            _row(effective_from="2026-06-01T00:00:00+00:00", commission_rate="0.0002"),
        ],
    )
    loaded = load_execution_profiles(p)[_KEY]
    before = [x for x in loaded if x.effective_from <= datetime(2026, 3, 1, tzinfo=_UTC)]
    at_v2 = [x for x in loaded if x.effective_from <= datetime(2026, 6, 1, tzinfo=_UTC)]
    assert max(before, key=lambda x: x.effective_from).commission_rate == Decimal("0.0001")
    assert max(at_v2, key=lambda x: x.effective_from).commission_rate == Decimal("0.0002")


def test_edge_9_facade_precedence_boundary(facade_path: Path) -> None:
    """Facade applies the same precedence: mid-history picks v1, boundary picks v2."""
    _write_at(
        facade_path,
        [
            _row(effective_from="2026-01-01T00:00:00+00:00", commission_rate="0.0001"),
            _row(effective_from="2026-06-01T00:00:00+00:00", commission_rate="0.0002"),
        ],
    )
    mar = get_execution_profile(
        "CRYPTO.BTCUSD", "deriv", effective_at=datetime(2026, 3, 1, tzinfo=_UTC)
    )
    jun = get_execution_profile(
        "CRYPTO.BTCUSD", "deriv", effective_at=datetime(2026, 6, 1, tzinfo=_UTC)
    )
    assert mar.commission_rate == Decimal("0.0001")
    assert jun.commission_rate == Decimal("0.0002")


def test_edge_10_duplicate_effective_from_rejected(tmp_path: Path) -> None:
    """Ambiguous overlap — never last-wins."""
    with pytest.raises(ReferenceValidationError):
        load_execution_profiles(
            _write(
                tmp_path,
                [
                    _row(effective_from="2026-01-01T00:00:00+00:00"),
                    _row(effective_from="2026-01-01T00:00:00+00:00", commission_rate="0.0002"),
                ],
            )
        )


def test_edge_11_config_row_unknown_instrument_rejected(tmp_path: Path) -> None:
    """Config drift vs identity SoT (config/assets master) — caught at load."""
    with pytest.raises(ReferenceValidationError):
        load_execution_profiles(_write(tmp_path, [_row(instrument_id="FAKE.BOGUS")]))


@pytest.mark.parametrize(
    "over",
    [
        pytest.param({"commission_rate": "0.11"}, id="commission_over_max"),
        pytest.param({"typical_spread": "1000001"}, id="spread_over_max"),
        pytest.param({"financing_apy": "1.5"}, id="financing_over_max"),
        pytest.param({"slippage": {"model": "fixed_bps", "value": "1001"}},
                     id="slippage_over_max"),
        pytest.param({"effective_from": "2026-01-01T00:00:00"}, id="naive_effective_from"),
    ],
)
def test_edge_12_bounds_refused_at_load(tmp_path: Path, over: dict) -> None:
    """Sane hard bounds (AGENTS 3.13): refuse, never clamp."""
    with pytest.raises(ReferenceValidationError):
        load_execution_profiles(_write(tmp_path, [_row(**over)]))


@pytest.mark.parametrize(
    "over",
    [
        pytest.param({"commission_rate": "-0.0001"}, id="negative_commission"),
        pytest.param({"typical_spread": "-1"}, id="negative_spread"),
    ],
)
def test_edge_13_negative_costs_refused(tmp_path: Path, over: dict) -> None:
    with pytest.raises(ReferenceValidationError):
        load_execution_profiles(_write(tmp_path, [_row(**over)]))


@pytest.mark.parametrize(
    "over",
    [
        pytest.param({"commission_rate": "abc"}, id="unparseable_string"),
        pytest.param({"commission_rate": None}, id="null"),
        pytest.param({"commission_rate": [0.1, 0.2]}, id="list"),
    ],
)
def test_edge_14_wrong_types_refused(tmp_path: Path, over: dict) -> None:
    with pytest.raises(ReferenceValidationError):
        load_execution_profiles(_write(tmp_path, [_row(**over)]))


def test_edge_15_profile_is_frozen(tmp_path: Path) -> None:
    prof = load_execution_profiles(_write(tmp_path, [_row()]))[_KEY][0]
    with pytest.raises(ValidationError):
        prof.commission_rate = Decimal("0.5")  # type: ignore[misc]


def test_edge_16_cache_stale_until_clear_then_rereads(facade_path: Path) -> None:
    """LRU per process (spec: rotation = edit file + cache_clear, snapshot precedent)."""
    _write_at(facade_path, [_row(commission_rate="0.0001")])
    first = get_execution_profile("CRYPTO.BTCUSD", "deriv")
    assert first.commission_rate == Decimal("0.0001")

    _write_at(facade_path, [_row(commission_rate="0.0002")])
    stale = get_execution_profile("CRYPTO.BTCUSD", "deriv")
    assert stale.commission_rate == Decimal("0.0001")  # cached until explicit clear

    ep.get_execution_profile.cache_clear()
    fresh = get_execution_profile("CRYPTO.BTCUSD", "deriv")
    assert fresh.commission_rate == Decimal("0.0002")


def test_edge_17_model_none_value_zero_accepted(tmp_path: Path) -> None:
    prof = load_execution_profiles(
        _write(tmp_path, [_row(slippage={"model": "none", "value": "0"})])
    )[_KEY][0]
    assert prof.slippage.model == "none"
    assert prof.slippage.value == Decimal(0)


@pytest.mark.parametrize(
    "slippage",
    [
        pytest.param({"model": "none", "value": "1.0"}, id="none_with_value"),
        pytest.param({"model": "fixed_bps", "value": "0"}, id="fixed_bps_zero"),
    ],
)
def test_edge_17_invalid_slippage_combo_refused(tmp_path: Path, slippage: dict) -> None:
    with pytest.raises(ReferenceValidationError):
        load_execution_profiles(_write(tmp_path, [_row(slippage=slippage)]))


# --- Adversarial ------------------------------------------------------------

def test_adv_a1_path_traversal_instrument_is_plain_unknown(facade_path: Path) -> None:
    """Pure dict lookup — zero filesystem involvement with caller input."""
    _write_at(facade_path, [_row()])
    with pytest.raises(UnknownInstrumentError):
        get_execution_profile("../../etc/passwd", "deriv")


def test_adv_a2_route_key_forbidden_by_extra_forbid(tmp_path: Path) -> None:
    """Structural proof routing decisions cannot enter M2b (bible 02b M2b-I05)."""
    with pytest.raises(ReferenceValidationError):
        load_execution_profiles(_write(tmp_path, [_row(route="mt5")]))


def test_adv_a3_commission_overflow_rejected(tmp_path: Path) -> None:
    """REV2 spec: 1.0e99 over bounds 0..0.10 → refused at load, no clamp, no OverflowError."""
    with pytest.raises(ReferenceValidationError):
        load_execution_profiles(_write(tmp_path, [_row(commission_rate=1e99)]))


def test_adv_a3_tiny_commission_unquoted_is_rejected(tmp_path: Path) -> None:
    """REV3 (2026-09-27, human-approved): unquoted money is rejected on REPRESENTATION
    grounds, not magnitude.

    REV2 asserted an unquoted `1e-99` was accepted because it happened to be inside the
    0..0.10 bounds. That made the rule turn on how small the number was, which is the wrong
    axis: the real question is whether the value is the author's literal or a binary float we
    recovered by luck. Quoted `"1e-99"` is accepted and stored exactly - asserted below."""
    with pytest.raises(ReferenceValidationError, match="QUOTED"):
        load_execution_profiles(_write(tmp_path, [_row(commission_rate=1e-99)]))

    prof = load_execution_profiles(
        _write(tmp_path, [_row(commission_rate="1e-99")])
    )[_KEY][0]
    assert prof.commission_rate == Decimal("1e-99")
    assert Decimal(0) <= prof.commission_rate <= Decimal("0.10")


def test_adv_a4_source_contains_no_route_substring() -> None:
    """Bible grep law as a test: facts-only module must not even contain the substring."""
    assert ep.__file__ is not None
    src = Path(ep.__file__).read_text(encoding="utf-8").lower()
    assert "route" not in src, "M2b-I05: routing decisions live in M6, not M2b"


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_adv_a5_nan_infinity_rejected_safely(tmp_path: Path, bad: str) -> None:
    """EXTRA adversarial (not in spec, 2 Step 3): Decimal parses NaN/Inf, and NaN breaks
    every bounds comparison (InvalidOperation) — must be rejected with the typed error."""
    with pytest.raises(ReferenceValidationError):
        load_execution_profiles(_write(tmp_path, [_row(commission_rate=bad)]))


# --- Hand-calcs (exact Decimal, never approx — AGENTS 3.1) ------------------

def test_hand_h1_commission_string_parses_exact(tmp_path: Path) -> None:
    # Hand calc: "0.0001" -> Decimal("0.0001") exact (1e-4 fraction of notional).
    # Illustrative cost (M8 applies, verify V3 prints): 84000 * 0.0001 = 8.40.
    prof = load_execution_profiles(_write(tmp_path, [_row(commission_rate="0.0001")]))[_KEY][0]
    assert prof.commission_rate == Decimal("0.0001")
    assert str(prof.commission_rate) == "0.0001"


def test_hand_h2_unquoted_float_rejected_quoted_parses_exact(tmp_path: Path) -> None:
    # Hand calc (REV3, 2026-09-27 human-approved): an UNQUOTED 0.0000001 is rejected, because
    # YAML has already turned it into a binary float by the time we read it, and
    # Decimal(str(f)) recovers the author's literal only via CPython's shortest-round-trip
    # repr - a runtime property, not a format guarantee. Quoted, it is exact.
    #
    # REV2 of this test asserted the float roundtrip SUCCEEDED. That was the whole point of
    # the test under the old law; under the new law the assertion is that the float never
    # gets in, and that the quoted form is exact.
    with pytest.raises(ReferenceValidationError, match="QUOTED"):
        load_execution_profiles(_write(tmp_path, [_row(commission_rate=0.0000001)]))

    prof = load_execution_profiles(
        _write(tmp_path, [_row(commission_rate="0.0000001")])
    )[_KEY][0]
    # Value equality is the assertion. `str()` is deliberately NOT asserted: Decimal renders
    # 1E-7 for this value, so a string check would pin the display form, not the number.
    assert prof.commission_rate == Decimal("0.0000001")


def test_hand_h3_trailing_zero_preserved(tmp_path: Path) -> None:
    # Hand calc: "0.00010" keeps exponent -5 (str "0.00010"), never silently rescaled —
    # proves string path, not float roundtrip (float would store 0.0001).
    prof = load_execution_profiles(_write(tmp_path, [_row(typical_spread="0.00010")]))[_KEY][0]
    assert prof.typical_spread == Decimal("0.00010")
    assert str(prof.typical_spread) == "0.00010"


# --- Facade -----------------------------------------------------------------

def test_facade_reexport_is_same_callable() -> None:
    """Single entrypoint (m2b.2 precedent): reference facade re-exports the impl callable."""
    assert reference.get_execution_profile is ep.get_execution_profile


def test_facade_get_end_to_end_default_now(facade_path: Path) -> None:
    """effective_at=None -> wall-clock now (UTC); row from 2026-01-01 is in effect."""
    _write_at(facade_path, [_row()])
    prof = get_execution_profile("CRYPTO.BTCUSD", "deriv")
    assert prof.commission_rate == Decimal("0.0001")
    assert prof.slippage.model == "fixed_bps"
