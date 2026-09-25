"""Self-tests for scripts/check_pit_guard.py — separate tests/ci folder (not module slices)."""
from __future__ import annotations

from pathlib import Path

from scripts.check_pit_guard import main, run, run_docs

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_pit_guard.py"


def test_no_alpha_code_exits_zero(tmp_path: Path, capsys):
    assert run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "pre-P22 skip" in out


def test_root_missing_exits_one(tmp_path: Path):
    assert main(["--root", str(tmp_path / "nope")]) == 1


def test_alpha_with_temporal_resolver_and_as_of_passes(tmp_path: Path, capsys):
    alpha = tmp_path / "modules" / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "gen.py").write_text(
        "from shared.temporal.temporal_resolver import resolve\n"
        "window = resolve(req, as_of=T)\n"
        "candles = lake.get_candles(as_of=T)\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 0
    assert "no direct Lake bypass" in capsys.readouterr().out


def test_alpha_direct_lake_list_limit_fails(tmp_path: Path, capsys):
    alpha = tmp_path / "modules" / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "bad.py").write_text(
        "rows = lake.list(limit=50)\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert "FIX" in out


def test_alpha_from_lake_import_fails(tmp_path: Path, capsys):
    alpha = tmp_path / "modules" / "strategy" / "momentum"
    alpha.mkdir(parents=True)
    (alpha / "s.py").write_text("from lake import get_candles\n", encoding="utf-8")
    assert run(tmp_path) == 1
    assert "direct lake import" in capsys.readouterr().out


def test_adversarial_hostile_path_still_detected(tmp_path: Path):
    alpha = tmp_path / "modules" / "alpha"
    alpha.mkdir(parents=True)
    # missing as_of + temporal — must fail even with weird content
    (alpha / "x.py").write_text("lake.get_candles(limit=1)\n'; DROP TABLE--\n", encoding="utf-8")
    assert run(tmp_path) == 1


def test_real_tree_passes():
    app_root = Path(__file__).resolve().parents[2]
    assert main(["--root", str(app_root)]) == 0


# --- Regression: guard bypasses found 2026-09-24 (tests written first) ---


def test_comment_spoof_does_not_whitelist(tmp_path: Path):
    """BUG 2026-09-24: guard keywords inside a comment whitelisted real lake reads."""
    alpha = tmp_path / "modules" / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "spoof.py").write_text(
        "# rows resolved via temporal_resolver with as_of T\n"
        "rows = lake.list(limit=50)\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 1


def test_trailing_comment_does_not_whitelist(tmp_path: Path):
    alpha = tmp_path / "modules" / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "spoof2.py").write_text(
        "rows = lake.list(limit=50)  # temporal_resolver as_of\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 1


def test_aliased_lake_call_detected(tmp_path: Path):
    """BUG 2026-09-24: `l = lake; l.list(limit=50)` evaded the `lake.` prefix regex."""
    alpha = tmp_path / "modules" / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "alias.py").write_text(
        "l = lake\n"
        "rows = l.list(limit=50)\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 1


def test_m4_alpha_directory_is_scanned(tmp_path: Path):
    """BUG 2026-09-24: ALPHA_GLOBS missed modules/m4_*/ (m4_alpha, m4_strategy_registry)."""
    m4 = tmp_path / "modules" / "m4_alpha"
    m4.mkdir(parents=True)
    (m4 / "registry.py").write_text("rows = lake.list(limit=50)\n", encoding="utf-8")
    assert run(tmp_path) == 1


def test_m8_backtest_directory_is_scanned(tmp_path: Path):
    m8 = tmp_path / "modules" / "m8_backtest"
    m8.mkdir(parents=True)
    (m8 / "walk.py").write_text("rows = lake.get_candles()\n", encoding="utf-8")
    assert run(tmp_path) == 1


def test_guarded_alias_still_passes(tmp_path: Path):
    """Positive control: alias + guard keywords in real code is fine."""
    alpha = tmp_path / "modules" / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "good.py").write_text(
        "from shared.temporal.temporal_resolver import resolve\n"
        "l = pit\n"
        "rows = l.get_candles(as_of=T)\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 0


# --- S0 2026-09-24: the BIBLE must not teach the banned bypass (docs scan) ---
# WHY: 08_M8_Validation.md literally contained `lake.list(..., user_id=...)` —
# the exact AGENTS S10 hard-ban — and check_pit_guard.py only scanned *.py.
# The manual was a free pass to teach look-ahead. Tests first, then impl.


def _mk_manual(tmp_path: Path) -> Path:
    """Layout: <tmp>/System_Development_Manual/modules (sibling of app root)."""
    docs = tmp_path / "System_Development_Manual" / "modules"
    docs.mkdir(parents=True)
    return docs


def test_docs_lake_list_with_user_id_fails(tmp_path: Path, capsys):
    docs = _mk_manual(tmp_path)
    (docs / "08_M8_Validation.md").write_text(
        "```python\nparquets = lake.list(broker=broker, user_id=user_id, symbol=s)\n```\n",
        encoding="utf-8",
    )
    assert run_docs(docs) == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out and "08_M8_Validation.md" in out


def test_docs_lake_get_positional_user_id_fails(tmp_path: Path, capsys):
    """03_M3_Analytics.md:87 was positional: lake.get(symbol, broker, user_id, timeframe)."""
    docs = _mk_manual(tmp_path)
    (docs / "03_M3_Analytics.md").write_text(
        "```python\ncandles = lake.get(symbol, broker, user_id, timeframe)\n```\n",
        encoding="utf-8",
    )
    assert run_docs(docs) == 1
    assert "lake read" in capsys.readouterr().out


def test_docs_legacy_per_tenant_lake_path_fails(tmp_path: Path, capsys):
    """08_M8:32 used the dead layout data/lake/brokers/deriv/user_id=42/... (market-data is global)."""
    docs = _mk_manual(tmp_path)
    (docs / "08_M8_Validation.md").write_text(
        "loads `data/lake/brokers/deriv/user_id=42/date=2026-01-01/EURUSD.parquet`\n",
        encoding="utf-8",
    )
    assert run_docs(docs) == 1
    assert "legacy" in capsys.readouterr().out


def test_docs_prose_as_of_does_not_whitelist(tmp_path: Path):
    """Adversarial: naming the guard in prose must not excuse a banned call."""
    docs = _mk_manual(tmp_path)
    (docs / "03_M3_Analytics.md").write_text(
        "We resolve via temporal_resolver with as_of T. Then:\n"
        "candles = lake.get(symbol, broker, user_id, timeframe)\n",
        encoding="utf-8",
    )
    assert run_docs(docs) == 1


def test_docs_clean_manual_passes(tmp_path: Path, capsys):
    docs = _mk_manual(tmp_path)
    (docs / "03_M3_Analytics.md").write_text(
        "Window = `market-data/provider=deriv/instrument=FX.EURUSD` (no user_id).\n"
        "candles = pit.get_market_state(instrument, as_of=T)\n",
        encoding="utf-8",
    )
    assert run_docs(docs) == 0
    assert "[OK]" in capsys.readouterr().out


def test_docs_missing_manual_is_skipped(tmp_path: Path, capsys):
    """App-only checkout (no bible sibling) must not fail the guard."""
    assert run_docs(tmp_path / "System_Development_Manual") == 0
    assert "skipped" in capsys.readouterr().out


def test_docs_glob_scans_master_and_modules(tmp_path: Path):
    docs = _mk_manual(tmp_path)
    (docs.parent / "System_Development_Manual.md").write_text(
        "data/lake/brokers/deriv/user_id=1/x.parquet\n", encoding="utf-8"
    )
    (docs / "02_M2_Data.md").write_text("clean\n", encoding="utf-8")
    assert run_docs(docs.parent) == 1


def test_main_runs_docs_scan_for_sibling_manual(tmp_path: Path, capsys):
    """main(--root app) must also scan <repo>/System_Development_Manual."""
    app = tmp_path / "financial_analyst_app"
    app.mkdir(parents=True)
    docs = _mk_manual(tmp_path)
    (docs / "08_M8_Validation.md").write_text(
        "lake.list(user_id=user_id)\n", encoding="utf-8"
    )
    assert main(["--root", str(app)]) == 1


def test_real_manual_passes_docs_scan():
    """The actual bible must be free of the banned patterns (RED until S0 docs fixes)."""
    repo = Path(__file__).resolve().parents[3]
    docs = repo / "System_Development_Manual"
    assert docs.is_dir(), f"bible not found at {docs}"
    assert run_docs(docs) == 0
