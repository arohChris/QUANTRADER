"""
P15 / M3.0 Capability Registry — pure lookup gate — Spec tests (WRITTEN BEFORE CODE).
WHY: AGENTS.md §2 Step 3 — tests first against docs/specs/m3.0_capability.spec.md
(LGTM ACTION 27, 2026-09-24, conditional: AGENTS.md followed critically).

Direct impl import ONLY — no importorskip. Collection RED until Step 5
(honest "not implemented"); after impl, 100% pass, 0 skips (§2 Step 6).

Contracts only: CapabilityStatus frozen (no bump). No money path → no hand-calc.
Gate name frozen (spec §Test plan): test_order_book_unavailable_on_deriv (P15).
Day-1: broker lookup only; user_id accepted but ignored; no DB/I/O in get().
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Direct impl import — fails collection until Step 5 (honest RED).
from modules.m3_analytics.capability.capability_registry import (  # noqa: F401
    CapabilityRegistry,
)
from modules.m3_analytics.capability.capability_types import CapabilityEntry  # noqa: F401

from shared.contracts.market_data import CapabilityStatus

# --- YAML fixture: frozen Day-1 shape from spec §YAML shape ---

_YAML_OK = """
version: "1.0"
features:
  rsi_14:
    status: FULLY_SUPPORTED
    requires: [price]
  ema_20:
    status: FULLY_SUPPORTED
    requires: [price]
  tick_rate:
    status: PROXY_LIMITED
    method: tick_count_proxy
    warning: "tick_count ≠ volume"
    requires: [tick_count]
  order_book_imbalance:
    status: UNAVAILABLE
    reason: "needs bid_levels"
    requires: [bid_levels, ask_qty]
brokers:
  deriv:
    overrides:
      order_book_imbalance:
        status: UNAVAILABLE
        reason: "Deriv has_depth:false"
  mt5:
    overrides:
      order_book_imbalance:
        status: FULLY_SUPPORTED
strategies:
  rsi_overbought_oversold:
    requires_analytics: [rsi_14]
  donchian_breakout:
    requires_analytics: [ema_20]
  microstructure_flow:
    requires_analytics: [order_book_imbalance]
"""


def _write_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "capabilities.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _registry(tmp_path: Path, text: str = _YAML_OK) -> CapabilityRegistry:
    return CapabilityRegistry.from_yaml(_write_yaml(tmp_path, text))


# --- Correct (spec §Correct 1–10) ---


def test_rsi_14_fully_supported_on_deriv(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    entry = reg.get("rsi_14", "deriv")
    assert entry.status is CapabilityStatus.FULLY_SUPPORTED
    assert isinstance(entry, CapabilityEntry)


def test_is_available_rsi_true(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    assert reg.is_available("rsi_14", "deriv") is True


def test_order_book_unavailable_on_deriv(tmp_path: Path) -> None:
    # P15 gate — exact name frozen in spec §Test plan / §Correct 3.
    reg = _registry(tmp_path)
    entry = reg.get("order_book_imbalance", "deriv")
    assert entry.status is CapabilityStatus.UNAVAILABLE
    assert entry.reason == "Deriv has_depth:false"
    assert reg.is_available("order_book_imbalance", "deriv") is False


def test_order_book_imbalance_available_on_mt5_override(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    entry = reg.get("order_book_imbalance", "mt5")
    assert entry.status is CapabilityStatus.FULLY_SUPPORTED
    assert reg.is_available("order_book_imbalance", "mt5") is True


def test_strategy_rsi_eligible_on_deriv(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    assert reg.is_strategy_eligible("rsi_overbought_oversold", "deriv") == (
        True,
        "eligible",
    )


def test_strategy_microstructure_ineligible_on_deriv(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    eligible, reason = reg.is_strategy_eligible("microstructure_flow", "deriv")
    assert eligible is False
    assert "order_book_imbalance" in reason


def test_proxy_limited_not_available_by_default(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    entry = reg.get("tick_rate", "deriv")
    assert entry.status is CapabilityStatus.PROXY_LIMITED
    assert reg.is_available("tick_rate", "deriv") is False


def test_proxy_limited_available_with_allow_proxy_flag(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    entry = reg.get("tick_rate", "deriv")
    assert reg.is_available("tick_rate", "deriv", allow_proxy=True) is True
    assert entry.warning == "tick_count ≠ volume"


def test_unknown_feature_fails_closed_not_raise(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    entry = reg.get("unknown_feature_xyz", "deriv")
    assert entry.status is CapabilityStatus.UNAVAILABLE
    assert entry.reason is not None and "unknown_feature" in entry.reason
    assert reg.is_available("unknown_feature_xyz", "deriv") is False


def test_unknown_broker_uses_global_defaults(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    # No ctrader overrides → global features: defaults still resolve.
    assert reg.get("rsi_14", "ctrader").status is CapabilityStatus.FULLY_SUPPORTED
    assert (
        reg.get("order_book_imbalance", "ctrader").status
        is CapabilityStatus.UNAVAILABLE
    )


def test_unknown_strategy_id_ineligible(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    eligible, reason = reg.is_strategy_eligible("no_such_strategy", "deriv")
    assert eligible is False
    assert "unknown_strategy" in reason


def test_deterministic_same_args_same_status(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    a = reg.get("ema_20", "deriv")
    b = reg.get("ema_20", "deriv")
    assert a.status is b.status
    assert a.reason == b.reason
    assert a.requires == b.requires


def test_user_id_ignored_day1_same_result(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    none_entry = reg.get("rsi_14", "deriv", user_id=None)
    tenant_entry = reg.get("rsi_14", "deriv", user_id="42")
    assert none_entry.status is tenant_entry.status
    assert none_entry.reason == tenant_entry.reason


# --- Edges (spec §Edges) ---


def test_empty_feature_string_unavailable(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    entry = reg.get("", "deriv")
    assert entry.status is CapabilityStatus.UNAVAILABLE
    assert reg.is_available("", "deriv") is False


def test_strategy_empty_requires_vacuously_eligible(tmp_path: Path) -> None:
    text = _YAML_OK.replace(
        "  microstructure_flow:\n    requires_analytics: [order_book_imbalance]",
        "  microstructure_flow:\n    requires_analytics: [order_book_imbalance]\n"
        "  empty_stub:\n    requires_analytics: []",
    )
    reg = _registry(tmp_path, text)
    assert reg.is_strategy_eligible("empty_stub", "deriv") == (True, "eligible")


def test_override_requires_without_status_keeps_base_status(tmp_path: Path) -> None:
    text = _YAML_OK.replace(
        "  deriv:\n    overrides:\n      order_book_imbalance:\n"
        "        status: UNAVAILABLE\n        reason: \"Deriv has_depth:false\"",
        "  deriv:\n    overrides:\n      order_book_imbalance:\n"
        "        requires: [bid_levels]\n"
        "      rsi_14:\n        requires: [price]",
    )
    reg = _registry(tmp_path, text)
    # rsi_14 override replaces requires only → status stays base FULLY_SUPPORTED.
    entry = reg.get("rsi_14", "deriv")
    assert entry.status is CapabilityStatus.FULLY_SUPPORTED
    assert entry.requires == ("price",)


# --- Load-time failures (spec §YAML shape / §Edges) ---


def test_missing_yaml_file_filenotfounderror(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(FileNotFoundError):
        CapabilityRegistry.from_yaml(missing)


def test_invalid_status_value_valueerror(tmp_path: Path) -> None:
    # Includes truncated "FULLY" and prose alias "CURRENTLY_UNAVAILABLE" (§Adversarial/§Edges).
    for i, bad in enumerate(("FULLY", "CURRENTLY_UNAVAILABLE", "nope")):
        sub = tmp_path / f"case_{i}"
        sub.mkdir()
        text = _YAML_OK.replace("status: FULLY_SUPPORTED", f"status: {bad}", 1)
        with pytest.raises(ValueError):
            _registry(sub, text)


def test_features_not_dict_valueerror(tmp_path: Path) -> None:
    text = 'version: "1.0"\nfeatures: []\n'
    with pytest.raises(ValueError):
        _registry(tmp_path, text)


def test_missing_version_or_features_valueerror(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _registry(tmp_path, "features:\n  rsi_14:\n    status: FULLY_SUPPORTED\n")
    (tmp_path / "capabilities.yaml").unlink(missing_ok=True)
    with pytest.raises(ValueError):
        _registry(tmp_path, 'version: "1.0"\n')


def test_override_without_base_feature_valueerror(tmp_path: Path) -> None:
    text = _YAML_OK.replace(
        "      order_book_imbalance:\n        status: UNAVAILABLE\n"
        "        reason: \"Deriv has_depth:false\"",
        "      phantom_feature:\n        status: UNAVAILABLE",
    )
    with pytest.raises(ValueError):
        _registry(tmp_path, text)


def test_malformed_yaml_valueerror(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _registry(tmp_path, "version: [unclosed\n  features: {{{")


def test_adversarial_strategy_requires_null_valueerror(tmp_path: Path) -> None:
    text = _YAML_OK.replace(
        "  rsi_overbought_oversold:\n    requires_analytics: [rsi_14]",
        "  rsi_overbought_oversold:\n    requires_analytics: null",
    )
    with pytest.raises(ValueError):
        _registry(tmp_path, text)


# --- Adversarial (spec §Adversarial — ≥1 beyond normal edges) ---


def test_adversarial_unknown_broker_and_empty_strings(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    # Empty broker fail-closed; hostile feature key never reaches SQL (there is none).
    assert reg.get("rsi_14", "").status is CapabilityStatus.UNAVAILABLE
    assert reg.is_available("rsi_14", "") is False
    hostile = reg.get("'; DROP TABLE features;--", "deriv")
    assert hostile.status is CapabilityStatus.UNAVAILABLE
    assert reg.is_available("'; DROP TABLE features;--", "ctrader") is False
