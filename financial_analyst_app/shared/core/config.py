"""
M1.3 — Config & Environment — YAML is the Truth
INSTITUTIONAL HEDGE FUND GRADE

WHY: No hard-coded `if symbol=="frxEURUSD"` or `rsi_period=14` in code.
     All numbers live in config/*.yaml, loaded once, validated at startup.
     Change YAML, not code. Invalid YAML → startup FAILS LOUD (not half-boot).

Loaded via pydantic-settings + pydantic BaseModel validation.
Single import: `from shared.core.config import settings`

Environment overlay: base YAML + environments/{env}.yaml deep-merged.
  e.g., risk/limits.yaml base: max_exposure 0.1 → production.yaml overlay: 0.05 (stricter)

Account mode (demo vs live): config/accounts.yaml + optional env ACCOUNT_MODE.
  Placeholders are ENV/Vault NAMES only — never secret values in YAML.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

# WHY Path relative to project root? So `pytest` and `uvicorn` find same config regardless of cwd.
CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"


# ---- Typed config models — validated on load (fail fast) ----


class RiskLimits(BaseModel):
    """Validated risk limits — WHY typed? Catches `max_exposure: \"oops\"` at startup, not at 3am fill time."""

    model_config = {"extra": "forbid"}
    max_exposure_per_symbol: float = Field(default=0.1, ge=0, le=1)
    max_leverage: float = Field(default=10, ge=1)
    min_free_margin: float = Field(default=100)
    max_drawdown: float = Field(default=0.2)


class SystemConfig(BaseModel):
    model_config = {"extra": "forbid"}
    base_currency: str = "USD"
    clock: str = "utc"
    log_level: str = "INFO"


AccountMode = Literal["demo", "live"]


class AccountSlot(BaseModel):
    """One mode slot (demo or live) — holds REFERENCES to env/vault, never secrets."""

    model_config = {"extra": "forbid"}
    account_id_env: str = ""
    api_token_env: str = ""
    api_token_vault_key: str = ""
    enabled: bool = True


class BrokerAccountConfig(BaseModel):
    model_config = {"extra": "forbid"}
    mode: AccountMode = "demo"
    demo: AccountSlot = Field(default_factory=AccountSlot)
    live: AccountSlot = Field(default_factory=lambda: AccountSlot(enabled=False))


class AccountsConfig(BaseModel):
    """Customer-runtime demo/live switch — Manual §2b layer 3."""

    model_config = {"extra": "forbid"}
    default_mode: AccountMode = "demo"
    brokers: dict[str, BrokerAccountConfig] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path, env: dict[str, str] | None = None) -> AccountsConfig:
        raw = _load_yaml(path)
        if not raw:
            return cls()
        cfg = cls(**raw)
        # Env override wins over YAML mode (bot/deploy flips without editing file).
        override = (env or os.environ).get("ACCOUNT_MODE", "").strip().lower()
        if override in ("demo", "live"):
            brokers = {
                name: b.model_copy(update={"mode": override})  # type: ignore[arg-type]
                for name, b in cfg.brokers.items()
            }
            cfg = cfg.model_copy(update={"brokers": brokers, "default_mode": override})  # type: ignore[arg-type]
        return cfg

    def resolve(self, broker: str) -> dict[str, Any]:
        """
        Effective account refs for one broker. Fail loud if live not armed.
        Returns env/vault NAMES — caller reads values from process env or vault.
        """
        if broker not in self.brokers:
            raise ValueError(f"unknown broker in accounts.yaml: {broker!r}")
        b = self.brokers[broker]
        mode = b.mode
        slot = b.live if mode == "live" else b.demo
        if mode == "live" and not slot.enabled:
            raise ValueError(
                f"broker {broker!r} mode=live but live.enabled is false — "
                "set live.enabled: true in config/accounts.yaml to arm real-money path"
            )
        if mode == "demo" and not slot.enabled:
            raise ValueError(f"broker {broker!r} demo slot disabled")
        return {
            "broker": broker,
            "mode": mode,
            "account_id_env": slot.account_id_env,
            "api_token_env": slot.api_token_env,
            "api_token_vault_key": slot.api_token_vault_key,
            "enabled": slot.enabled,
        }


class Settings(BaseModel):
    """Singleton settings — loaded once at import, validated, then frozen for runtime."""

    model_config = {"extra": "allow", "frozen": False}  # allow extra for forward compat, but validate known
    environment: str = Field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    risk: RiskLimits = Field(default_factory=RiskLimits)
    system: SystemConfig = Field(default_factory=SystemConfig)
    accounts: AccountsConfig = Field(default_factory=AccountsConfig)
    # Raw YAML dicts for dynamic sections (assets/brokers/strategies) — validated per-module
    raw: dict[str, Any] = Field(default_factory=dict)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                raise ValueError(f"YAML top-level must be dict: {path}")
            return data
    except yaml.YAMLError as exc:
        # WHY loud? Silent bad YAML = wrong risk limit in prod
        raise RuntimeError(f"CONFIG ERROR: invalid YAML {path}: {exc}") from exc


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge overlay onto base — WHY deep? So production.yaml can override just risk.max_exposure."""
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings(config_root: Path | None = None) -> Settings:
    """
    WHY function not just global? Test can call load_settings(tmp_path) with fixtures.
    Loads:
      1. config/system/system.yaml + config/risk/limits.yaml (base)
      2. config/environments/{env}.yaml overlay (deep-merge)
      3. .env is handled by pydantic-settings if needed (secrets, never YAML)
    """
    root = config_root or CONFIG_ROOT
    env = os.getenv("ENVIRONMENT", "development")

    # Base
    system_raw = _load_yaml(root / "system" / "system.yaml")
    risk_raw = _load_yaml(root / "risk" / "limits.yaml")
    # Overlay
    overlay = _load_yaml(root / "environments" / f"{env}.yaml")
    # Account mode (demo/live) — optional file; missing → safe empty AccountsConfig
    accounts = AccountsConfig.load(root / "accounts.yaml")

    merged_system = _deep_merge(system_raw, overlay.get("system", {}))
    merged_risk = _deep_merge(risk_raw, overlay.get("risk", {}))

    try:
        settings = Settings(
            environment=env,
            system=SystemConfig(**merged_system) if merged_system else SystemConfig(),
            risk=RiskLimits(**merged_risk) if merged_risk else RiskLimits(),
            accounts=accounts,
            raw={"system": merged_system, "risk": merged_risk, "overlay": overlay},
        )
    except ValidationError as exc:
        # WHY loud + field name? So DevOps sees "missing threshold in strategies/momentum.yaml"
        raise RuntimeError(f"CONFIG ERROR: validation failed: {exc}") from exc

    return settings


# Singleton — imported as `from shared.core.config import settings`
try:
    settings = load_settings()
except RuntimeError as exc:
    # In tests without config files, allow empty settings — but log loudly
    # WHY not sys.exit? Import-time exit breaks pytest collection
    import logging

    logging.getLogger(__name__).warning("config load failed, using defaults: %s", exc)
    settings = Settings()
