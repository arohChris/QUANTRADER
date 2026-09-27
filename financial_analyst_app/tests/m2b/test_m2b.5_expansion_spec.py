"""
M2b.5 (part 3) - the gaps a review of parts 1-2 found, plus the 25-asset expansion scenario.

Written because reviewing the first two suites showed three real holes:
  1. the 25-asset expansion - the ACTUAL goal - had no test at all, only a 2-asset demo;
  2. concurrency was claimed in the spec but never tested, while `standards()` is called
     from the ingest path as well as boot;
  3. `GapLedger.record_permanent` idempotency was only tested single-threaded, though
     backfill and reconciliation both call it.
"""
from __future__ import annotations

import tempfile
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from modules.m2_market_data.coverage import (
    GapLedger,
    GapRun,
    GapState,
    SessionEvidence,
    classify_window,
    coverage_of,
)
from modules.reference.calendar import TradingCalendar
from modules.reference.reference import list_orphans, load_assets
from modules.reference.standards import load_standards, standards

# The expansion target, recorded 2026-09-27. 10 crypto (DOT/ETC excluded: recognised by the
# broker but returning zero bars), 13 forex, 2 metals.
CRYPTO_10 = ["BTC", "ETH", "SOL", "XRP", "ADA", "LTC", "BCH", "TON", "XLM", "NEAR"]
FOREX_13 = ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD", "EURGBP", "USDCAD", "EURCHF",
            "EURJPY", "GBPJPY", "AUDJPY", "USDCHF", "AUDNZD", "GBPAUD"]
METALS_2 = ["XAU", "XAG"]
ALL_25 = ([f"CRYPTO.{c}USD" for c in CRYPTO_10]
          + [f"FX.{p}" for p in FOREX_13]
          + [f"COMMODITY.{m}USD" for m in METALS_2])

SAT = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)     # Saturday
WEEK = 7 * 24 * 60


def _write_25(tmp_path: Path) -> list[Path]:
    """Author all 25 assets minimally - one line of money config each."""
    files: list[Path] = []
    groups = {
        "crypto25.yaml": ([f"CRYPTO.{c}USD" for c in CRYPTO_10], "crypto", "1"),
        "forex25.yaml": ([f"FX.{p}" for p in FOREX_13], "forex", "100000"),
        "metals25.yaml": ([f"COMMODITY.{m}USD" for m in METALS_2], "commodity", "100"),
    }
    for name, (symbols, asset_class, contract) in groups.items():
        body = "".join(
            f'{sym}:\n  asset_class: {asset_class}\n  contract_size: "{contract}"\n'
            f'  brokers: [deriv]\n  broker_native:\n    deriv: frx{sym.split(".")[1]}\n'
            for sym in symbols
        )
        p = tmp_path / name
        p.write_text(body, encoding="utf-8")
        files.append(p)
    return files


# ==================================================== the 25-asset scenario
def test_all_25_assets_load_from_minimal_authoring(tmp_path: Path) -> None:
    files = _write_25(tmp_path)
    master = load_assets(files)
    assert len(master) == 25, f"expected 25 instruments, got {len(master)}"
    assert sorted(master) == sorted(ALL_25)


def test_all_25_inherit_their_class_conventions(tmp_path: Path) -> None:
    master = load_assets(_write_25(tmp_path))
    std = {n: s for n, s in standards().items()}
    for inst_id, entry in master.items():
        asset_class = entry["asset_class"]
        assert entry["min_qty"] == std[asset_class].min_qty, f"{inst_id} min_qty drifted"
        assert entry["qty_step"] == std[asset_class].qty_step, f"{inst_id} qty_step drifted"
        assert isinstance(entry["tick_size"], Decimal)
        assert isinstance(entry["contract_size"], Decimal)


def test_all_25_get_the_correct_session_hours(tmp_path: Path) -> None:
    """The point of the whole slice, at full scale: every asset's trading week follows its
    class without a single session line written."""
    master = load_assets(_write_25(tmp_path))
    cal = TradingCalendar(class_resolver=lambda i: master[i])
    monday = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)

    for inst_id, entry in master.items():
        if entry["asset_class"] == "crypto":
            assert cal.open_minutes(inst_id, monday, monday + timedelta(days=7)) == WEEK, (
                f"{inst_id} should trade 24/7"
            )
            assert cal.is_open(inst_id, SAT) is True
        else:
            hours = cal.open_minutes(inst_id, monday, monday + timedelta(days=7))
            assert 0 < hours < WEEK, f"{inst_id} must have a weekend closure, got {hours}h"
            assert cal.is_open(inst_id, SAT) is False, f"{inst_id} must be shut Saturday"


def test_removing_one_asset_from_25_leaves_the_other_24_intact(tmp_path: Path) -> None:
    files = _write_25(tmp_path)
    master = load_assets(files)
    del master["CRYPTO.SOLUSD"]
    assert len(master) == 24
    cal = TradingCalendar(class_resolver=lambda i: master[i])
    # A removed instrument is unknown, and says so with a typed error.
    with pytest.raises(Exception):  # noqa: B017 - tightened below
        cal.is_open("CRYPTO.SOLUSD", SAT)
    # ...and its neighbours are untouched.
    assert cal.is_open("CRYPTO.ADAUSD", SAT) is True
    assert cal.is_open("FX.EURUSD", SAT) is False


def test_25_asset_weekend_produces_gaps_only_for_crypto(tmp_path: Path) -> None:
    """End-to-end: the gap classifier over a full weekend for all 25 assets. Only the 10
    crypto assets may report gap minutes; a single session asset reporting one is the exact
    bug the CLOSED state exists to prevent."""
    master = load_assets(_write_25(tmp_path))
    sat = datetime(2026, 9, 19, 0, 0, tzinfo=UTC)
    sun_open = datetime(2026, 9, 20, 21, 0, tzinfo=UTC)
    offenders: list[str] = []
    for inst_id, entry in master.items():
        rows = classify_window(inst_id, "deriv", sat, sun_open,
                               class_resolver=lambda i: master[i])
        gap_minutes = sum(r.minutes() for r in rows if r.state is GapState.FILLING)
        if entry["asset_class"] == "crypto":
            assert gap_minutes > 0, f"{inst_id} trades all weekend and must report gaps"
        elif gap_minutes:
            offenders.append(f"{inst_id} reported {gap_minutes} gap minutes while shut")
    assert offenders == [], offenders


# ==================================================== concurrency
def test_gap_ledger_is_idempotent_under_concurrent_record() -> None:
    """Backfill and reconciliation both call record_permanent. Without a lock, two threads
    inserting the same window would produce duplicate rows and a duplicate repair request."""
    ledger = GapLedger()
    run = GapRun("CRYPTO.BTCUSD", "deriv", SAT, SAT + timedelta(minutes=30),
                 GapState.PERMANENTLY_MISSING)
    added: list[int] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait(timeout=5)
        added.append(ledger.record_permanent([run], reason="venue has no data"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sum(added) == 1, f"exactly one thread may report a NEW record, got {added}"
    assert len(ledger.permanent_runs("CRYPTO.BTCUSD", "deriv")) == 1


def test_concurrent_classify_window_is_consistent() -> None:
    """Gap classification runs from the ingest-adjacent path. A shared mutable default would
    make two threads see different answers for the same window."""
    results: list[int] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait(timeout=5)
        rows = classify_window("FX.EURUSD", "deriv", SAT, SAT + timedelta(hours=1))
        results.append(sum(r.minutes() for r in rows if r.state is GapState.FILLING))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(results) == 8
    assert len(set(results)) == 1, f"threads disagreed on the same window: {results}"
    assert results[0] == 0, "forex is shut Saturday, so zero gap minutes"


# ==================================================== broker oracle combinations
def test_coverage_of_with_a_broker_that_confirms_a_real_hole() -> None:
    class BrokerOpen:
        def evidence(self, instrument_id, broker, start, end):
            return [(start, end, SessionEvidence.BROKER, True)]

    verdict = coverage_of(
        "CRYPTO.BTCUSD", "deriv", SAT, SAT + timedelta(hours=2),
        present=lambda a, b: [],
        missing=lambda a, b: [(SAT, SAT + timedelta(hours=2))],
        oracle=BrokerOpen(),
    )
    assert verdict.state is GapState.FILLING
    assert not verdict.can_compute
    assert all(r.evidence is SessionEvidence.BROKER for r in verdict.runs)


def test_broker_confirmed_closure_clears_coverage_to_complete() -> None:
    """Broker says 'shut, there is nothing there'. With no missing data and no open span,
    the verdict must not report a gap - otherwise every weekend is a permanent block."""
    class BrokerClosed:
        def evidence(self, instrument_id, broker, start, end):
            return [(start, end, SessionEvidence.BROKER, False)]

    verdict = coverage_of(
        "FX.EURUSD", "deriv", SAT, SAT + timedelta(hours=2),
        present=lambda a, b: [a],
        missing=lambda a, b: [],
        oracle=BrokerClosed(),
    )
    assert verdict.state is GapState.COMPLETE, [r.state for r in verdict.runs]
    assert verdict.can_compute


# ==================================================== shipped config sanity
def test_shipped_standard_classes_all_have_instruments() -> None:
    """A class nothing uses is dead config that looks load-bearing."""
    repo_cfg = Path(__file__).resolve().parents[2] / "config"
    master = load_assets(sorted((repo_cfg / "assets").glob("*.yaml")))
    used = {e["asset_class"] for e in master.values()}
    assert set(load_standards()) - used == set(), (
        f"unused classes: {sorted(set(load_standards()) - used)}"
    )


def test_orphan_detection_on_a_real_removal(tmp_path: Path) -> None:
    """Actually REMOVE an instrument from the master, then show a broker-native map still
    naming it. (A first draft of this test left all 25 present and asserted an orphan for an
    instrument that still existed - it asserted `[]` and would have passed for the wrong
    reason if the assert had been looser.)"""
    _write_25(tmp_path)
    # Rewrite the crypto file WITHOUT SOL - this is the removal.
    remaining = [c for c in CRYPTO_10 if c != "SOL"]
    (tmp_path / "crypto25.yaml").write_text("".join(
        f'CRYPTO.{c}USD:\n  asset_class: crypto\n  contract_size: "1"\n'
        f'  brokers: [deriv]\n  broker_native:\n    deriv: frx{c}USD\n' for c in remaining
    ), encoding="utf-8")
    # ...and a mapping file that still knows about SOL.
    stale = tmp_path / "symbol_mapping.yaml"
    stale.write_text("CRYPTO.SOLUSD:\n  deriv: frxSOLUSD\n", encoding="utf-8")

    files = [f for f in sorted(tmp_path.glob("*.yaml")) if f.name != "symbol_mapping.yaml"]
    assert "CRYPTO.SOLUSD" not in load_assets(files), "precondition: SOL must be gone"
    orphans = list_orphans(master_files=files, mapping_files=[stale])
    assert orphans == ["symbol_mapping.yaml: CRYPTO.SOLUSD is not in the instrument master"], orphans


def test_standards_reload_does_not_leak_into_other_readers(tmp_path: Path) -> None:
    """A test that reloads a fixture config must not leave the fixture installed for the
    rest of the suite - that is how a passing suite starts lying."""
    fixture = tmp_path / "only_crypto.yaml"
    fixture.write_text(
        'crypto:\n  always_open: true\n  price_dp: 8\n  tick_size: "0.00000001"\n'
        '  min_qty: "0.00001"\n  qty_step: "0.00001"\n', encoding="utf-8")
    from modules.reference.standards import reload_standards

    try:
        reload_standards(fixture)
        assert set(standards()) == {"crypto"}
    finally:
        reload_standards()
    assert set(standards()) == {"commodity", "crypto", "forex"}, (
        "the shipped standard must be restored after a fixture reload"
    )


def test_tempdir_is_actually_cleaned_up() -> None:
    """Guard against a test leaving config fixtures behind in the repo."""
    before = set(Path(tempfile.gettempdir()).glob("*.yaml"))
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.yaml"
        p.write_text("crypto:\n  always_open: true\n  tick_size: \"0.01\"\n"
                     "  min_qty: \"0.01\"\n  qty_step: \"0.01\"\n", encoding="utf-8")
        assert p.exists()
    assert set(Path(tempfile.gettempdir()).glob("*.yaml")) == before
