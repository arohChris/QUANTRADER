"""
M1.3 — Config Tests — bad YAML fails loud, overlay merges, feature flags
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shared.core.config import RiskLimits, SystemConfig, load_settings
from shared.core.feature_flags import is_enabled, reload as reload_flags


def test_risk_limits_validation() -> None:
    # Valid
    rl = RiskLimits(max_exposure_per_symbol=0.1, max_leverage=10)
    assert rl.max_exposure_per_symbol == 0.1
    # Invalid — should raise ValidationError → RuntimeError in load_settings
    with pytest.raises(Exception):
        RiskLimits(max_exposure_per_symbol=1.5)  # >1 not allowed


def test_load_settings_from_tmp(tmp_path: Path) -> None:
    # WHY tmp? So test doesn't need real config/ files — proves loader works isolated
    (tmp_path / "system").mkdir()
    (tmp_path / "risk").mkdir()
    (tmp_path / "environments").mkdir()
    (tmp_path / "system" / "system.yaml").write_text("base_currency: USD\nlog_level: INFO\n", encoding="utf-8")
    (tmp_path / "risk" / "limits.yaml").write_text("max_exposure_per_symbol: 0.1\nmax_leverage: 10\n", encoding="utf-8")
    (tmp_path / "environments" / "development.yaml").write_text("", encoding="utf-8")

    settings = load_settings(config_root=tmp_path)
    assert settings.system.base_currency == "USD"
    assert settings.risk.max_leverage == 10


def test_overlay_merges(tmp_path: Path) -> None:
    (tmp_path / "system").mkdir()
    (tmp_path / "risk").mkdir()
    (tmp_path / "environments").mkdir()
    (tmp_path / "system" / "system.yaml").write_text("base_currency: USD\nlog_level: INFO\n", encoding="utf-8")
    (tmp_path / "risk" / "limits.yaml").write_text("max_exposure_per_symbol: 0.1\nmax_leverage: 10\n", encoding="utf-8")
    (tmp_path / "environments" / "production.yaml").write_text("risk:\n  max_exposure_per_symbol: 0.05\n", encoding="utf-8")

    import os

    old = os.getenv("ENVIRONMENT")
    os.environ["ENVIRONMENT"] = "production"
    try:
        settings = load_settings(config_root=tmp_path)
        assert settings.risk.max_exposure_per_symbol == 0.05  # overlay wins
        assert settings.risk.max_leverage == 10  # base preserved
    finally:
        if old is None:
            os.environ.pop("ENVIRONMENT", None)
        else:
            os.environ["ENVIRONMENT"] = old


def test_invalid_yaml_raises_loud(tmp_path: Path) -> None:
    (tmp_path / "system").mkdir()
    (tmp_path / "risk").mkdir()
    (tmp_path / "environments").mkdir()
    (tmp_path / "system" / "system.yaml").write_text("not: [valid: yaml: :\n", encoding="utf-8")
    (tmp_path / "risk" / "limits.yaml").write_text("max_exposure_per_symbol: 0.1\n", encoding="utf-8")
    (tmp_path / "environments" / "development.yaml").write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="CONFIG ERROR"):
        load_settings(config_root=tmp_path)


def test_feature_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Create temp flags
    (tmp_path / "system").mkdir(parents=True)
    flags_path = tmp_path / "system" / "feature_flags.yaml"
    flags_path.write_text("enable_stat_arb: true\nenable_headway: false\n", encoding="utf-8")

    # Patch path
    import shared.core.feature_flags as ff

    old_path = ff._FLAGS_PATH
    old_flags = ff._FLAGS
    try:
        ff._FLAGS_PATH = flags_path  # type: ignore[assignment]
        ff._FLAGS = None
        assert is_enabled("enable_stat_arb") is True
        assert is_enabled("enable_headway") is False
        assert is_enabled("nonexistent") is False  # default false
        assert is_enabled("nonexistent", default=True) is True
    finally:
        ff._FLAGS_PATH = old_path
        ff._FLAGS = old_flags
