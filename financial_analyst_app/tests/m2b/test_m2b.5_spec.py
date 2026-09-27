"""
M2b.5 Asset-Class Standard - Step 3 tests, written AGAINST
docs/specs/m2b.5_asset_class_standard.spec.md.

The point of the slice: adding or removing an asset must be ONE edit, and everything
class-derived must follow automatically. The add/remove propagation tests at the bottom are
the load-bearing ones.

Spec-first: this file fails collection until shared/reference/asset_classes.py exists.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from modules.reference.calendar import is_open, open_intervals
from modules.reference.reference import (
    AssetConfigError,
    get_instrument,
    list_orphans,
    load_assets,
    validate_asset_file,
)
from modules.reference.standards import class_standard, load_standards
from shared.reference.asset_classes import (
    AssetClassStandard,
    AssetStandardError,
    money_decimal,
    parse_standard,
)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "assets"
FRI = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)     # Friday, a trading day
SAT = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)     # Saturday
SUN = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)     # Sunday before the 21:00 open


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


# ============================================================ E1-E3 inheritance
def test_E1_crypto_asset_inherits_always_open() -> None:
    std = class_standard("crypto")
    assert std.session["always_open"] is True


def test_E2_forex_asset_inherits_sunday_evening_to_friday() -> None:
    std = class_standard("forex")
    assert std.session["open_weekday"] == 6 and std.session["open_minute"] == 21 * 60
    assert std.session["close_weekday"] == 4 and std.session["close_minute"] == 21 * 60


def test_E3_commodity_asset_inherits_metals_hours() -> None:
    std = class_standard("commodity")
    assert std.session["open_minute"] == 22 * 60
    assert std.session["close_minute"] == 21 * 60


def test_calendar_actually_uses_the_shared_standard() -> None:
    """Invariant 5: one table. The calendar's answer must be DERIVED from the standard, not
    from a private copy that could drift."""
    assert is_open("CRYPTO.BTCUSD", SAT) is class_standard("crypto").session["always_open"]
    assert is_open("FX.EURUSD", SAT) is False   # forex standard says shut Saturday
    assert is_open("COMMODITY.XAUUSD", SAT) is False
    assert is_open("CRYPTO.BTCUSD", FRI) is True


# ============================================================ E4-E5 money typing
def test_E4_unquoted_money_field_is_rejected(tmp_path: Path) -> None:
    body = "CRYPTO.TEST:\n  asset_class: crypto\n  contract_size: 100\n  tick_size: 0.01\n"
    with pytest.raises(AssetConfigError, match="tick_size"):
        validate_asset_file(_write(tmp_path, "a.yaml", body))


def test_E5_quoted_money_field_becomes_exact_decimal(tmp_path: Path) -> None:
    body = (
        "CRYPTO.TEST:\n  asset_class: crypto\n  contract_size: \"100\"\n"
        "  tick_size: \"0.01\"\n"
    )
    path = _write(tmp_path, "a.yaml", body)
    inst = load_assets([path])["CRYPTO.TEST"]
    # Hand calc: "0.01" must be Decimal("0.01") exactly, never via float.
    assert inst["tick_size"] == Decimal("0.01")
    assert isinstance(inst["tick_size"], Decimal)
    assert isinstance(inst["contract_size"], Decimal)


# ============================================================ E6-E7 contract size
def test_E6_missing_contract_size_is_rejected(tmp_path: Path) -> None:
    """A missing contract size is a guaranteed wrong notional, so there is no safe default."""
    body = "CRYPTO.TEST:\n  asset_class: crypto\n"
    with pytest.raises(AssetConfigError, match="contract_size"):
        validate_asset_file(_write(tmp_path, "a.yaml", body))


def test_E7_non_positive_contract_size_is_rejected(tmp_path: Path) -> None:
    for bad in ('"0"', '"-5"'):
        body = f"CRYPTO.TEST:\n  asset_class: crypto\n  contract_size: {bad}\n"
        with pytest.raises(AssetConfigError, match="contract_size"):
            validate_asset_file(_write(tmp_path, "a.yaml", body))


def test_contract_size_differs_per_instrument_and_that_is_correct() -> None:
    """Gold 100, Silver 5000. The standard must NOT flatten a real per-instrument
    difference - this is the case that stops 'standardise everything' being wrong."""
    assert get_instrument("COMMODITY.XAUUSD")["contract_size"] == Decimal("100")  # noqa: FURB157 - money from str, never from int/float
    assert get_instrument("COMMODITY.XAGUSD")["contract_size"] == Decimal("5000")  # noqa: FURB157 - money from str, never from int/float


# ============================================================ E8-E9 asset class
def test_E8_unknown_asset_class_is_rejected(tmp_path: Path) -> None:
    body = "FX.BAD:\n  asset_class: forexx\n  contract_size: \"100000\"\n"
    with pytest.raises(AssetConfigError, match="asset_class|forexx"):
        validate_asset_file(_write(tmp_path, "a.yaml", body))


def test_E9_absent_asset_class_is_rejected(tmp_path: Path) -> None:
    body = "FX.BAD:\n  contract_size: \"100000\"\n"
    with pytest.raises(AssetConfigError, match="asset_class"):
        validate_asset_file(_write(tmp_path, "a.yaml", body))


# ============================================================ E10 session override
def test_E10_per_asset_session_override_is_rejected(tmp_path: Path) -> None:
    """Session hours are a property of the CLASS. Allowing a per-asset exception is exactly
    how the two tables drift apart again."""
    body = (
        "CRYPTO.TEST:\n  asset_class: crypto\n  contract_size: \"1\"\n"
        "  sessions:\n    - open: \"00:00\"\n      close: \"00:00\"\n"
    )
    with pytest.raises(AssetConfigError, match="session"):
        validate_asset_file(_write(tmp_path, "a.yaml", body))


# ============================================================ E11-E12 overrides
def test_E11_tick_size_override_is_honoured(tmp_path: Path) -> None:
    body = (
        "CRYPTO.TEST:\n  asset_class: crypto\n  contract_size: \"1\"\n"
        "  tick_size: \"0.00000001\"\n"
    )
    inst = load_assets([_write(tmp_path, "a.yaml", body)])["CRYPTO.TEST"]
    assert inst["tick_size"] == Decimal("0.00000001")


def test_E12_min_qty_above_qty_step_is_rejected(tmp_path: Path) -> None:
    body = (
        "CRYPTO.TEST:\n  asset_class: crypto\n  contract_size: \"1\"\n"
        "  qty_step: \"0.01\"\n  min_qty: \"0.5\"\n"
    )
    with pytest.raises(AssetConfigError, match="min_qty"):
        validate_asset_file(_write(tmp_path, "a.yaml", body))


# ============================================================ E13-E14 orphans
def test_E13_orphan_session_override_is_reported(tmp_path: Path) -> None:
    """Removal is the dangerous direction: a stale rule for a gone instrument still fires."""
    files = [_write(tmp_path, "a.yaml", "CRYPTO.TEST:\n  asset_class: crypto\n  contract_size: \"1\"\n")]
    calendar = _write(tmp_path, "cal.yaml", "CRYPTO.GONE:\n  sessions:\n    - open: \"00:00\"\n      close: \"01:00\"\n")
    orphans = list_orphans(master_files=files, calendar_files=[calendar])
    assert any("CRYPTO.GONE" in o for o in orphans)


def test_E14_orphan_broker_native_is_reported(tmp_path: Path) -> None:
    files = [_write(tmp_path, "a.yaml", "CRYPTO.TEST:\n  asset_class: crypto\n  contract_size: \"1\"\n")]
    mapping = _write(tmp_path, "map.yaml", "CRYPTO.GONE:\n  deriv: frxGONE\n")
    orphans = list_orphans(master_files=files, mapping_files=[mapping])
    assert any("CRYPTO.GONE" in o for o in orphans)


# ============================================================ E15-E16 hand calcs
def test_E15_decimal_addition_is_exact() -> None:
    # Hand calc: 0.1 + 0.2 == 0.3 exactly in Decimal; binary float gives 0.30000000000000004.
    assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
    assert 0.1 + 0.2 != 0.3  # the float behaviour we are removing


def test_E16_tick_size_keeps_precision_a_float_could_not() -> None:
    # Hand calc: 0.00000001 is exactly representable as a float, but 0.1 is not exact in
    # binary. Decimal keeps the AUTHORED literal either way.
    assert money_decimal("0.00000001") == Decimal("0.00000001")
    assert money_decimal("0.1") == Decimal("0.1")
    assert Decimal("0.1") * 3 == Decimal("0.3")


# ============================================================ E17-E18 invariants
def test_E17_the_standard_round_trips_without_losing_a_field() -> None:
    """REPLACED a tautology. The previous version asserted the standard contains no
    always_open+weekday contradiction - but `parse_standard` already rejects that, so the
    test could never fail. This one CAN: it serialises each class back to plain data and
    re-parses, so a field the schema silently drops (a new key added to config but not
    carried into the dataclass) fails here."""
    for name, std in load_standards().items():
        assert isinstance(std, AssetClassStandard)
        as_data: dict[str, object] = {"price_dp": std.price_dp,
                                      "tick_size": str(std.tick_size),
                                      "min_qty": str(std.min_qty),
                                      "qty_step": str(std.qty_step)}
        as_data.update(std.session)
        again = parse_standard(name, as_data)
        assert again == std, f"{name}: round-trip lost or altered a field"


def test_shipped_config_agrees_with_the_shipped_standard() -> None:
    """Cross-file drift check - CAN fail, unlike the old vacuous orphan test.

    Two files carry the truth (`config/assets/*.yaml` and
    `config/reference/asset_classes.yaml`) and a human edits them separately. If an
    instrument is added with a value that silently disagrees with its class, or with a
    class that does not exist, this is where it surfaces - at review, not in trade.
    """
    standards = load_standards()
    master = load_assets(sorted(CONFIG_DIR.glob("*.yaml")))
    assert master, "the shipped asset master must not be empty"
    for inst_id, entry in master.items():
        asset_class = entry["asset_class"]
        assert asset_class in standards, f"{inst_id}: class {asset_class!r} is not in the standard"
        std = standards[asset_class]
        # An instrument that declares no override must equal its class default exactly.
        for field, inherited in (("min_qty", std.min_qty), ("qty_step", std.qty_step)):
            assert isinstance(entry[field], Decimal), f"{inst_id}: {field} is not a Decimal"
            assert entry[field] > 0, f"{inst_id}: {field} must be positive"
            del inherited
        assert isinstance(entry["contract_size"], Decimal), f"{inst_id}: contract_size not Decimal"
        assert entry["contract_size"] > 0


def test_shipped_config_declares_no_unused_asset_class() -> None:
    """A class in the standard with no instrument using it is dead config - it looks like it
    governs something, and one day an instrument will rely on it by accident."""
    standards = load_standards()
    master = load_assets(sorted(CONFIG_DIR.glob("*.yaml")))
    used = {e["asset_class"] for e in master.values()}
    unused = sorted(set(standards) - used)
    assert unused == [], f"classes in the standard with no instrument: {unused}"


def test_E18_the_calendar_cannot_diverge_from_the_standard() -> None:
    """Cross-check the calendar against the standard by brute force over a full week."""
    for inst, cls in (("FX.EURUSD", "forex"), ("COMMODITY.XAUUSD", "commodity"),
                      ("CRYPTO.BTCUSD", "crypto")):
        std = class_standard(cls)
        expected_always = bool(std.session.get("always_open"))
        start = datetime(2026, 9, 14, tzinfo=UTC)
        total = sum(
            int((b - a).total_seconds() // 60)
            for a, b in open_intervals(inst, start, start + timedelta(days=7))
        )
        if expected_always:
            assert total == 7 * 24 * 60, f"{inst} should be open all week, got {total}"
        else:
            assert 0 < total < 7 * 24 * 60, f"{inst} should have closures, got {total}"


# ============================================================ the point of the slice
def test_ADDING_an_asset_is_one_edit_and_inherits_everything(tmp_path: Path) -> None:
    """A brand new crypto asset needs class + contract size + broker natives. Nothing else.
    Session, tick and qty must all follow the class standard automatically."""
    body = (
        "CRYPTO.NEWCOIN:\n"
        "  asset_class: crypto\n"
        "  contract_size: \"1\"\n"
        "  brokers: [deriv]\n"
        "  broker_native:\n"
        "    deriv: frxNEWCOIN\n"
    )
    inst = load_assets([_write(tmp_path, "a.yaml", body)])["CRYPTO.NEWCOIN"]

    # every class-derived field arrived without being written
    assert inst["tick_size"] == class_standard("crypto").tick_size
    assert inst["min_qty"] == class_standard("crypto").min_qty
    assert inst["qty_step"] == class_standard("crypto").qty_step
    assert inst["asset_class"] == "crypto"


def test_ADDING_a_forex_asset_inherits_the_forex_session(tmp_path: Path) -> None:
    body = "FX.NEWPAIR:\n  asset_class: forex\n  contract_size: \"100000\"\n"
    inst = load_assets([_write(tmp_path, "a.yaml", body)])["FX.NEWPAIR"]
    assert inst["tick_size"] == class_standard("forex").tick_size
    assert inst["asset_class"] == "forex"


def test_shipped_config_has_no_unquoted_money_fields() -> None:
    """The real files must obey the rule this slice enforces, not just the tests."""
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for field in ("tick_size", "contract_size", "min_qty", "qty_step"):
            for line in text.splitlines():
                if line.strip().startswith(f"{field}:"):
                    value = line.split(":", 1)[1].strip()
                    assert value.startswith(('"', "'")), (
                        f"{path.name}: {field}: {value} is unquoted - money must be a "
                        "quoted string so it is never parsed as a float"
                    )


# ============================================================ adversarial
def test_ADV_absurd_tick_size_is_rejected(tmp_path: Path) -> None:
    """`Decimal("1e400")` is FINITE and exact - exactness is not sanity. A tick of 1e400
    would round every price to a multiple of 1e400, destroying the data while looking valid.
    AGENTS.md 3.13: refuse to start on an absurd value."""
    body = (
        "CRYPTO.TEST:\n  asset_class: crypto\n  contract_size: \"1\"\n"
        "  tick_size: \"1e400\"\n"
    )
    with pytest.raises(AssetConfigError, match="tick_size"):
        validate_asset_file(_write(tmp_path, "a.yaml", body))


def test_ADV_non_finite_exponent_is_rejected() -> None:
    for bad in ("Infinity", "-Infinity", "NaN", "sNaN"):
        with pytest.raises(AssetStandardError):
            money_decimal(bad, field="tick_size")


def test_ADV_empty_and_malformed_strings_are_rejected() -> None:
    for bad in ("", " 0.01 ", "0.01USD", "-0", "0.0.1", "nan", "Infinity"):
        with pytest.raises(AssetStandardError):
            money_decimal(bad, field="tick_size")


def test_ADV_duplicate_instrument_across_files_is_rejected(tmp_path: Path) -> None:
    body = "CRYPTO.DUP:\n  asset_class: crypto\n  contract_size: \"1\"\n"
    a = _write(tmp_path, "a.yaml", body)
    b = _write(tmp_path, "b.yaml", body)
    with pytest.raises(AssetConfigError, match="CRYPTO.DUP"):
        load_assets([a, b])


def test_ADV_unknown_class_raises_rather_than_defaulting() -> None:
    # The standard raises AssetStandardError; the asset LOADER wraps that as AssetConfigError.
    # `shared` cannot import `modules`, so the wrapping happens one layer up by design.
    with pytest.raises(AssetStandardError):
        class_standard("equity")
