"""
M2b.5 (part 2) - the asset-class standard is CONFIG, not code. Failure-mode sweep.

Human directive 2026-09-27: *"everything that can be configured should be made configurable,
especially things that have the potential to be expanded."* A hardcoded table in Python makes
adding an asset class a code change and a deploy, which is the wrong shape for the one part
of the system whose whole purpose is to grow.

These tests are written BEFORE the implementation (AGENTS.md 2 Step 3) and every one of them
is a FAILURE MODE, because in config the dangerous case is not "it does not work" - it is
"it works, and is quietly wrong".
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from modules.reference.calendar import TradingCalendar
from modules.reference.standards import (
    AssetStandardsError,
    class_standard,
    load_standards,
    reload_standards,
    session_defaults,
    standard_path,
)
from shared.contracts.errors import UnknownInstrumentError
from shared.reference.asset_classes import AssetStandardError, money_decimal, parse_standard

SAT = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)   # Saturday
FRI = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)   # Friday

VALID = """
crypto:
  price_dp: 8
  always_open: true
  tick_size: "0.00000001"
  min_qty: "0.00001"
  qty_step: "0.00001"
forex:
  price_dp: 5
  open_weekday: 6
  open_minute: 1260
  close_weekday: 4
  close_minute: 1260
  tick_size: "0.00001"
  min_qty: "0.01"
  qty_step: "0.01"
"""


def _cfg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "asset_classes.yaml"
    p.write_text(body, encoding="utf-8")
    return p


# ==================================================== it is data, not code
def test_standard_is_loaded_from_a_yaml_file(tmp_path: Path) -> None:
    stds = load_standards(_cfg(tmp_path, VALID))
    assert set(stds) == {"crypto", "forex"}
    assert stds["forex"].session["open_minute"] == 1260


def test_shipped_standard_file_exists_and_loads() -> None:
    stds = load_standards(standard_path())
    assert set(stds) == {"crypto", "forex", "commodity"}
    for name, std in stds.items():
        assert std.name == name


def test_adding_an_asset_class_is_a_yaml_edit_not_a_code_change(tmp_path: Path) -> None:
    """The expansion case the directive is about: a brand new class, no Python touched."""
    body = VALID + """
index:
  price_dp: 2
  open_weekday: 1
  open_minute: 60
  close_weekday: 5
  close_minute: 1140
  tick_size: "0.01"
  min_qty: "0.1"
  qty_step: "0.1"
"""
    path = _cfg(tmp_path, body)
    stds = load_standards(path)
    assert "index" in stds, "a new class must need no code change"
    assert stds["index"].price_dp == 2

    # ...and the calendar honours it immediately, because the standard is read through a
    # function rather than bound at import. This is the config hot-reload path.
    try:
        reload_standards(path)
        cal = TradingCalendar(class_resolver=lambda i: {"asset_class": "index"})
        assert cal.is_open("IDX.SOMETHING", datetime(2026, 9, 16, 12, 0, tzinfo=UTC)) is True
    finally:
        reload_standards()  # restore the shipped standard for every other test


# ==================================================== fail-closed on absence
def test_missing_standard_file_is_an_error_not_a_default(tmp_path: Path) -> None:
    """No safe default exists: guessing forex hours would trade an asset at the weekend."""
    with pytest.raises(AssetStandardsError, match="not found|missing"):
        load_standards(tmp_path / "nope.yaml")


def test_empty_standard_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(AssetStandardsError):
        load_standards(_cfg(tmp_path, ""))


def test_standards_file_that_is_not_a_mapping_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(AssetStandardsError):
        load_standards(_cfg(tmp_path, "- just\n- a\n- list\n"))


# ==================================================== per-class validation
def test_unknown_key_in_a_class_is_rejected(tmp_path: Path) -> None:
    """A typo must not be silently ignored - that is how a session quietly stops applying."""
    body = VALID + 'commodity:\n  price_dp: 2\n  open_minute: 1320\n  typo_here: 1\n'
    with pytest.raises(AssetStandardsError, match="typo_here|unknown"):
        load_standards(_cfg(tmp_path, body))


def test_always_open_together_with_weekdays_is_rejected(tmp_path: Path) -> None:
    """Two contradictory answers. There is no safe resolution, so refuse to start."""
    body = (
        'weird:\n  always_open: true\n  open_weekday: 6\n  open_minute: 0\n'
        '  close_weekday: 4\n  close_minute: 60\n'
        '  tick_size: "0.01"\n  min_qty: "0.01"\n  qty_step: "0.01"\n'
    )
    with pytest.raises(AssetStandardsError, match="always_open|contradict"):
        load_standards(_cfg(tmp_path, body))


def test_windowed_class_without_close_is_rejected(tmp_path: Path) -> None:
    body = 'weird:\n  open_weekday: 6\n  open_minute: 1260\n  tick_size: "0.01"\n  min_qty: "0.01"\n  qty_step: "0.01"\n'
    with pytest.raises(AssetStandardsError, match="close"):
        load_standards(_cfg(tmp_path, body))


@pytest.mark.parametrize("minute", [-1, 1440, 5000])
def test_out_of_range_minute_is_rejected(tmp_path: Path, minute: int) -> None:
    body = (
        'weird:\n  open_weekday: 6\n  open_minute: 1260\n'
        f'  close_weekday: 4\n  close_minute: {minute}\n'
        '  tick_size: "0.01"\n  min_qty: "0.01"\n  qty_step: "0.01"\n'
    )
    with pytest.raises(AssetStandardsError, match="close_minute|minute"):
        load_standards(_cfg(tmp_path, body))


@pytest.mark.parametrize("weekday", [-1, 7, 99])
def test_out_of_range_weekday_is_rejected(tmp_path: Path, weekday: int) -> None:
    body = (
        f'weird:\n  open_weekday: {weekday}\n  open_minute: 1260\n'
        '  close_weekday: 4\n  close_minute: 1260\n'
        '  tick_size: "0.01"\n  min_qty: "0.01"\n  qty_step: "0.01"\n'
    )
    with pytest.raises(AssetStandardsError, match="open_weekday|weekday"):
        load_standards(_cfg(tmp_path, body))


def test_min_qty_above_qty_step_in_config_is_rejected(tmp_path: Path) -> None:
    body = 'weird:\n  always_open: true\n  tick_size: "0.01"\n  min_qty: "5"\n  qty_step: "0.01"\n'
    with pytest.raises(AssetStandardsError, match="min_qty"):
        load_standards(_cfg(tmp_path, body))


def test_unquoted_money_in_the_standard_config_is_rejected(tmp_path: Path) -> None:
    """Same money rule as the asset master, applied to the standard itself."""
    body = 'weird:\n  always_open: true\n  tick_size: 0.01\n  min_qty: "0.01"\n  qty_step: "0.01"\n'
    with pytest.raises(AssetStandardsError, match="tick_size"):
        load_standards(_cfg(tmp_path, body))


def test_absurd_tick_size_in_config_is_rejected(tmp_path: Path) -> None:
    body = 'weird:\n  always_open: true\n  tick_size: "1e400"\n  min_qty: "0.01"\n  qty_step: "0.01"\n'
    with pytest.raises(AssetStandardsError, match="tick_size"):
        load_standards(_cfg(tmp_path, body))


def test_missing_required_key_is_rejected_naming_the_key(tmp_path: Path) -> None:
    body = 'weird:\n  always_open: true\n  min_qty: "0.01"\n  qty_step: "0.01"\n'
    with pytest.raises(AssetStandardsError, match="tick_size"):
        load_standards(_cfg(tmp_path, body))


def test_duplicate_class_is_rejected(tmp_path: Path) -> None:
    """YAML duplicate keys silently keep the last one; that is a config trap."""
    body = VALID + '\ncrypto:\n  always_open: false\n  tick_size: "1"\n  min_qty: "1"\n  qty_step: "1"\n'
    with pytest.raises(AssetStandardsError, match="duplicate|crypto"):
        load_standards(_cfg(tmp_path, body))


def test_malformed_yaml_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(AssetStandardsError):
        load_standards(_cfg(tmp_path, "crypto:\n  - broken\n   indent: [\n"))


# ==================================================== the coupling fix
def test_calendar_accepts_an_injected_class_resolver() -> None:
    """Found by the add/remove proof: the calendar resolved the class through a module global,
    so a newly added instrument was invisible to it. A hidden global is not testable."""
    cal = TradingCalendar(class_resolver=lambda i: {"asset_class": "crypto"})
    assert cal.is_open("CRYPTO.BRANDNEW", SAT) is True
    assert cal.is_open("CRYPTO.BRANDNEW", FRI) is True


def test_calendar_honours_an_injected_forex_resolver() -> None:
    cal = TradingCalendar(class_resolver=lambda i: {"asset_class": "forex"})
    assert cal.is_open("FX.BRANDNEW", FRI) is True
    assert cal.is_open("FX.BRANDNEW", SAT) is False


def test_calendar_rejects_a_resolver_returning_an_unknown_class() -> None:
    cal = TradingCalendar(class_resolver=lambda i: {"asset_class": "equity"})
    with pytest.raises(UnknownInstrumentError, match="no calendar rule for asset_class"):
        cal.is_open("EQ.SOMETHING", FRI)


def test_calendar_rejects_a_resolver_returning_a_bad_shape() -> None:
    """A resolver returning a bare string must not be coerced into an asset class."""
    def bad(_i: str) -> Any:
        return "forex"

    cal = TradingCalendar(class_resolver=bad)
    with pytest.raises(UnknownInstrumentError, match="must return a mapping"):
        cal.is_open("FX.BAD", FRI)


def test_calendar_rejects_a_resolver_returning_an_empty_mapping() -> None:
    """No asset_class at all is a typed error, never a silent 'assume closed'."""
    cal = TradingCalendar(class_resolver=lambda i: {})
    with pytest.raises(UnknownInstrumentError, match="no calendar rule for asset_class"):
        cal.is_open("FX.NOCLASS", FRI)


def test_session_defaults_come_from_the_loaded_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QT_TEST_STANDARDS", str(_cfg(tmp_path, VALID)))
    stds = reload_standards()
    assert stds["forex"].session["open_weekday"] == 6
    assert set(session_defaults()) == {"crypto", "forex"}


def test_class_standard_unknown_is_a_typed_error() -> None:
    with pytest.raises(AssetStandardsError, match="unknown asset_class"):
        class_standard("nope")


# ==================================================== money edge cases
@pytest.mark.parametrize("bad", ["", " 0.01 ", "0.01USD", "-0", "0.0.1", "nan",
                                 "Infinity", "0x10", "1.2.3", "one"])
def test_money_decimal_rejects_every_malformed_literal(bad: str) -> None:
    # The specific type matters: `raises(Exception)` would also pass if the parser blew up
    # with an AttributeError, i.e. it would not distinguish "we rejected this" from "it broke".
    with pytest.raises(AssetStandardError):
        money_decimal(bad, field="tick_size")


@pytest.mark.parametrize("raw", [0.01, 1, 100, True])
def test_money_decimal_rejects_unquoted_numbers(raw: object) -> None:
    """A YAML number is already a float by the time we see it - the case AGENTS.md 3.1 bans."""
    with pytest.raises(AssetStandardError, match="QUOTED"):
        money_decimal(raw, field="tick_size")


@pytest.mark.parametrize("ok,expected", [("0.01", "0.01"), ("100", "100"),
                                         ("0.00000001", "0.00000001"),
                                         ("1E-5", "0.00001")])
def test_money_decimal_accepts_the_literals_we_actually_use(ok: str, expected: str) -> None:
    assert money_decimal(ok) == Decimal(expected)


def test_parse_standard_never_returns_a_mutable_default() -> None:
    """Reference data must not be mutable at runtime - a shared dict is a silent corruption
    that only shows up as a mystery three weeks later."""
    std = parse_standard("x", {"always_open": True, "tick_size": "0.01",
                               "min_qty": "0.01", "qty_step": "0.01"})
    with pytest.raises(FrozenInstanceError):
        std.price_dp = 99  # type: ignore[misc]


def test_session_defaults_are_defensive_copies() -> None:
    """A caller mutating the returned session dict must not corrupt the loaded standard for
    every other caller - that is a cross-tenant corruption path (AGENTS.md 4.3)."""
    first = session_defaults()
    first["forex"]["open_minute"] = 0
    first["forex"]["always_open"] = True
    second = session_defaults()
    assert second["forex"]["open_minute"] == 1260
    assert second["forex"]["always_open"] is False


def test_standards_cache_is_shared_but_not_mutable_across_callers() -> None:
    a, b = class_standard("forex"), class_standard("forex")
    assert a is b, "one process-wide load, not a re-read per call"
    assert a.session["open_minute"] == 1260


def test_concurrent_first_load_yields_a_complete_standard() -> None:
    """`standards()` is called from the ingest path as well as boot. A half-built dict read
    from two threads would hand one of them a missing class."""
    import threading

    reload_standards()  # clear the cache so the first call must build it
    results: list[object] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        try:
            barrier.wait(timeout=5)
            results.append(sorted(session_defaults()))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == [], f"concurrent load raised: {errors}"
    assert len(results) == 8
    assert all(r == ["commodity", "crypto", "forex"] for r in results), (
        f"a thread saw a partial standard: {results}"
    )
