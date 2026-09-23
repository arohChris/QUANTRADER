"""
M1.3 account-mode tests — demo vs live switch + placeholders (WRITTEN FOR loader).
WHY: Customer runtime (Manual §2b) must flip demo↔live without code edits; secrets
only via env/vault refs — never values in YAML.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from shared.core.config import AccountsConfig, BrokerAccountConfig, load_settings


def _write_accounts(tmp_path: Path, body: str) -> None:
    (tmp_path / "accounts.yaml").write_text(body, encoding="utf-8")


DEFAULT_ACCOUNTS = """
default_mode: demo
brokers:
  deriv:
    mode: demo
    demo:
      account_id_env: DERIV_DEMO_ACCOUNT_ID
      api_token_env: DERIV_DEMO_API_TOKEN
      api_token_vault_key: ""
      enabled: true
    live:
      enabled: false
      account_id_env: DERIV_LIVE_ACCOUNT_ID
      api_token_env: DERIV_LIVE_API_TOKEN
      api_token_vault_key: ""
"""


def test_default_mode_is_demo_and_live_disarmed(tmp_path: Path) -> None:
    _write_accounts(tmp_path, DEFAULT_ACCOUNTS)
    cfg = AccountsConfig.load(tmp_path / "accounts.yaml")
    assert cfg.default_mode == "demo"
    assert cfg.brokers["deriv"].mode == "demo"
    assert cfg.brokers["deriv"].live.enabled is False
    # Placeholders present as env NAMES only — no secret values
    assert cfg.brokers["deriv"].live.account_id_env == "DERIV_LIVE_ACCOUNT_ID"
    assert cfg.brokers["deriv"].live.api_token_env == "DERIV_LIVE_API_TOKEN"


def test_switch_to_live_requires_enabled_flag(tmp_path: Path) -> None:
    # Flip ONLY brokers.deriv.mode (not default_mode — "default_mode: demo" also contains "mode: demo")
    body = DEFAULT_ACCOUNTS.replace(
        "  deriv:\n    mode: demo",
        "  deriv:\n    mode: live",
        1,
    )
    # live.enabled still false → resolve must refuse
    _write_accounts(tmp_path, body)
    cfg = AccountsConfig.load(tmp_path / "accounts.yaml")
    assert cfg.brokers["deriv"].mode == "live"
    with pytest.raises(ValueError, match="live"):
        cfg.resolve("deriv")


def test_switch_live_when_enabled(tmp_path: Path) -> None:
    body = DEFAULT_ACCOUNTS.replace(
        "  deriv:\n    mode: demo",
        "  deriv:\n    mode: live",
        1,
    ).replace(
        "live:\n      enabled: false", "live:\n      enabled: true", 1
    )
    _write_accounts(tmp_path, body)
    cfg = AccountsConfig.load(tmp_path / "accounts.yaml")
    resolved = cfg.resolve("deriv")
    assert resolved["mode"] == "live"
    assert resolved["account_id_env"] == "DERIV_LIVE_ACCOUNT_ID"
    assert resolved["api_token_env"] == "DERIV_LIVE_API_TOKEN"


def test_env_override_beats_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_accounts(tmp_path, DEFAULT_ACCOUNTS)
    monkeypatch.setenv("ACCOUNT_MODE", "live")
    cfg = AccountsConfig.load(tmp_path / "accounts.yaml", env=os.environ)
    # override sets intent to live — still must have live.enabled to resolve
    assert cfg.brokers["deriv"].mode == "live"
    with pytest.raises(ValueError, match="live"):
        cfg.resolve("deriv")


def test_unknown_broker_raises(tmp_path: Path) -> None:
    _write_accounts(tmp_path, DEFAULT_ACCOUNTS)
    cfg = AccountsConfig.load(tmp_path / "accounts.yaml")
    with pytest.raises(ValueError, match="broker"):
        cfg.resolve("not-a-broker")


def test_load_settings_includes_accounts(tmp_path: Path) -> None:
    (tmp_path / "system").mkdir()
    (tmp_path / "risk").mkdir()
    (tmp_path / "environments").mkdir()
    (tmp_path / "system" / "system.yaml").write_text("base_currency: USD\n", encoding="utf-8")
    (tmp_path / "risk" / "limits.yaml").write_text("max_exposure_per_symbol: 0.1\n", encoding="utf-8")
    (tmp_path / "environments" / "development.yaml").write_text("", encoding="utf-8")
    _write_accounts(tmp_path, DEFAULT_ACCOUNTS)

    settings = load_settings(config_root=tmp_path)
    assert isinstance(settings.accounts, AccountsConfig)
    assert settings.accounts.default_mode == "demo"
    assert "deriv" in settings.accounts.brokers


def test_no_secret_values_in_shipped_accounts_yaml() -> None:
    """Adversarial: shipped YAML must not contain token-like values (env NAMES only)."""
    from shared.core.config import CONFIG_ROOT

    path = CONFIG_ROOT / "accounts.yaml"
    assert path.exists(), "config/accounts.yaml must ship with placeholders"
    text = path.read_text(encoding="utf-8")
    # crude but effective: no long opaque secrets after api_token: value form
    assert "api_token:" not in text.replace("api_token_env:", "").replace(
        "api_token_vault_key:", ""
    )
    assert "mode: demo" in text  # safe default while G0
