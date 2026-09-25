"""Self-tests for scripts/check_retention.py — tests/ci folder."""
from __future__ import annotations

from pathlib import Path

from scripts.check_retention import main, run


def test_clean_tree_exits_zero(tmp_path: Path, capsys):
    mod = tmp_path / "modules" / "m2_market_data"
    mod.mkdir(parents=True)
    (mod / "storage.py").write_text(
        '"""Writes MarketData ticks to parquet lake RAW."""\n'
        "def write_batch(rows):\n"
        "    path = 'lake/market-data/ticks.parquet'\n"
        "    return path\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 0
    assert "no AnalyticsResult cold write" in capsys.readouterr().out


def test_analyticsresult_parquet_write_fails(tmp_path: Path, capsys):
    mod = tmp_path / "modules" / "m3_analytics"
    mod.mkdir(parents=True)
    (mod / "bad_cache.py").write_text(
        "from shared.contracts.analytics import AnalyticsResult\n"
        "def persist(ar: AnalyticsResult):\n"
        "    ar.to_parquet('lake/analytics/rsi.parquet')\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out
    assert "FIX" in out


def test_lineage_only_no_write_passes(tmp_path: Path):
    mod = tmp_path / "modules" / "m4_alpha"
    mod.mkdir(parents=True)
    (mod / "decision.py").write_text(
        "from shared.contracts.analytics import AnalyticsResult\n"
        "def lineage(ar: AnalyticsResult) -> int:\n"
        "    return ar.source_watermark or 0\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 0


def test_redis_cache_reference_passes(tmp_path: Path):
    mod = tmp_path / "modules" / "m3_analytics"
    mod.mkdir(parents=True)
    (mod / "engine.py").write_text(
        "from shared.contracts.analytics import AnalyticsResult\n"
        "def cache(redis, ar: AnalyticsResult):\n"
        "    redis.setex('features', 60, ar)\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 0


def test_root_missing_exits_one(tmp_path: Path):
    assert main(["--root", str(tmp_path / "missing")]) == 1


def test_adversarial_features_dict_to_parquet_fails(tmp_path: Path, capsys):
    mod = tmp_path / "modules" / "m3_analytics"
    mod.mkdir(parents=True)
    (mod / "hot.py").write_text(
        "features = {'rsi_14': 68.0}\n"
        "features.to_parquet('data/analytics_features.parquet')\n",
        encoding="utf-8",
    )
    # fixed 2026-09-24: a features-dict cold write IS a hit (docstring always banned it)
    assert run(tmp_path) == 1
    assert "[FAIL]" in capsys.readouterr().out


def test_real_tree_passes():
    app_root = Path(__file__).resolve().parents[2]
    assert main(["--root", str(app_root)]) == 0


# --- Regression: guard bypasses found 2026-09-24 (tests written first) ---


def test_check_file_outside_scripts_dir_not_skipped(tmp_path: Path):
    """BUG 2026-09-24: name.startswith('check_') skipped ANY file, not just guard scripts."""
    mod = tmp_path / "modules" / "m3_analytics"
    mod.mkdir(parents=True)
    (mod / "check_evil.py").write_text(
        "from shared.contracts.analytics import AnalyticsResult\n"
        "AnalyticsResult(...).to_parquet('data/out.parquet')\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 1


def test_features_dict_to_parquet_alone_detected(tmp_path: Path):
    """BUG 2026-09-24: RE_ANNOT gate meant a features dict write was invisible."""
    mod = tmp_path / "modules" / "m3_analytics"
    mod.mkdir(parents=True)
    (mod / "hot.py").write_text(
        "features = {'rsi_14': 68.0}\n"
        "features.to_parquet('data/analytics_features.parquet')\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 1


def test_features_dict_to_plain_parquet_detected(tmp_path: Path):
    """Features dict to a non-'analytics' path is still a banned cold write."""
    mod = tmp_path / "modules" / "m3_analytics"
    mod.mkdir(parents=True)
    (mod / "hot_plain.py").write_text(
        "features = {'rsi_14': 68.0}\n"
        "features.to_parquet('data/out.parquet')\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 1


def test_analytics_path_without_annotation_detected(tmp_path: Path):
    """BUG 2026-09-24: `df.to_parquet('.../analytics/...')` with no AnalyticsResult token."""
    mod = tmp_path / "modules" / "m3_analytics"
    mod.mkdir(parents=True)
    (mod / "df.py").write_text(
        "df.to_parquet('data/lake/analytics/rsi.parquet')\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 1


def test_market_data_parquet_still_passes(tmp_path: Path):
    """Positive control: RAW market-data parquet is explicitly allowed (§24)."""
    mod = tmp_path / "modules" / "m2_market_data"
    mod.mkdir(parents=True)
    (mod / "writer.py").write_text(
        "pq.write_table(t, 'data/lake/market-data/provider=deriv/out.parquet')\n",
        encoding="utf-8",
    )
    assert run(tmp_path) == 0
