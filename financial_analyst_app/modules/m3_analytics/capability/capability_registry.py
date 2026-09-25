"""WHY: P15 capability gate — decide BEFORE any compute or Signal whether a feature /
strategy is supported for a broker, so M3.1 never computes UNAVAILABLE features and
M4.1 can DISABLE families (AGENTS §1 build-order law: P15 M3.0 → P16 M3.1 → P22 M4.1).

Pure + sync after load (no I/O, no DB, no network in the hot path); EVERYTHING
unknown or malformed fails CLOSED — a missing/typo'd config entry must never
silently enable trading. Bible: System_Development_Manual/modules/03_M3_Analytics.md
§3 · Spec: docs/specs/m3.0_capability.spec.md (LGTM ACTION 27 · tests LGTM A29).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from modules.m3_analytics.capability.capability_types import CapabilityEntry
from shared.contracts.market_data import CapabilityStatus

# WHY set: available = honest data paths only. PROXY_LIMITED is opt-in via
# allow_proxy (fail-closed — M3.1 must not silently treat a proxy as full data).
_HONEST = frozenset({CapabilityStatus.FULLY_SUPPORTED, CapabilityStatus.DERIVED})


def _require_mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        # WHY ValueError not TypeError: spec/tests freeze ValueError for ALL load
        # failures (A29) — TypeError would break the pytest.raises(ValueError) contract.
        raise ValueError(f"{where} must be a mapping, got {type(value).__name__}")  # noqa: TRY004
    return value


def _parse_status(raw: Any, where: str) -> CapabilityStatus:
    # Strict enum values only — prose aliases like "CURRENTLY_UNAVAILABLE" rejected.
    if not isinstance(raw, str):
        raise ValueError(f"{where}.status must be a string")  # noqa: TRY004 — spec-frozen ValueError
    try:
        return CapabilityStatus(raw)
    except ValueError as exc:
        raise ValueError(f"{where}.status {raw!r} is not a CapabilityStatus value") from exc


def _parse_requires(raw: Any, where: str) -> tuple[str, ...]:
    # WHY strict: null/non-list requires would make eligibility silently vacuous.
    if raw is None:
        raise ValueError(f"{where}.requires_analytics is null")
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"{where}.requires_analytics must be a list of strings")
    return tuple(raw)


def _parse_feature(raw: Any, where: str) -> CapabilityEntry:
    data = _require_mapping(raw, where)
    if "status" not in data:
        raise ValueError(f"{where}: status is required")
    fields: dict[str, Any] = {"status": _parse_status(data["status"], where)}
    if "requires" in data:
        fields["requires"] = _parse_requires(data["requires"], where)
    for optional in ("reason", "warning", "method"):
        if optional in data:
            value = data[optional]
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{where}.{optional} must be a string")
            fields[optional] = value
    # extra="forbid": unknown YAML keys → ValueError at load (fail-loud, not typo-hell).
    return CapabilityEntry(**fields)


def _merge_override(base: CapabilityEntry, raw: Any, where: str) -> CapabilityEntry:
    """Override fields REPLACE base fields; status always resolves (base if omitted)."""
    data = _require_mapping(raw, where)
    fields: dict[str, Any] = {
        "status": base.status,
        "reason": base.reason,
        "requires": base.requires,
        "warning": base.warning,
        "method": base.method,
    }
    if "status" in data:
        fields["status"] = _parse_status(data["status"], where)
    if "requires" in data:
        fields["requires"] = _parse_requires(data["requires"], where)
    for optional in ("reason", "warning", "method"):
        if optional in data:
            fields[optional] = data[optional]
    return CapabilityEntry(**fields)


class CapabilityRegistry:
    """Immutable-after-load capability lookup — sync dict reads, zero I/O per call."""

    def __init__(
        self,
        features: dict[str, CapabilityEntry],
        brokers: dict[str, dict[str, CapabilityEntry]],
        strategies: dict[str, tuple[str, ...]],
    ) -> None:
        self._features = features
        self._brokers = brokers
        self._strategies = strategies

    @classmethod
    def from_yaml(cls, path: str | Path) -> CapabilityRegistry:
        # FileNotFoundError propagates untouched: missing config = fail-loud startup.
        text = Path(path).read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError(f"malformed YAML in {path}: {exc}") from exc
        root = _require_mapping(data, "capabilities.yaml")
        if "version" not in root:
            raise ValueError("capabilities.yaml: missing version")

        features_raw = root.get("features")
        if not isinstance(features_raw, dict):
            raise ValueError(  # noqa: TRY004 — spec-frozen ValueError (A29 tests)
                "capabilities.yaml: features must be a mapping"
            )
        features = {
            name: _parse_feature(raw, f"features.{name}")
            for name, raw in features_raw.items()
        }

        brokers: dict[str, dict[str, CapabilityEntry]] = {}
        brokers_raw = root.get("brokers") or {}
        _require_mapping(brokers_raw, "brokers")
        for broker, broker_raw in brokers_raw.items():
            broker_data = _require_mapping(broker_raw, f"brokers.{broker}")
            overrides_raw = broker_data.get("overrides") or {}
            _require_mapping(overrides_raw, f"brokers.{broker}.overrides")
            merged: dict[str, CapabilityEntry] = {}
            for feature, override_raw in overrides_raw.items():
                if feature not in features:
                    # Override without base = config drift → refuse to load.
                    raise ValueError(f"brokers.{broker}: override without base feature {feature!r}")
                merged[feature] = _merge_override(
                    features[feature], override_raw, f"brokers.{broker}.overrides.{feature}"
                )
            brokers[broker] = merged

        strategies: dict[str, tuple[str, ...]] = {}
        strategies_raw = root.get("strategies") or {}
        _require_mapping(strategies_raw, "strategies")
        for strategy_id, strategy_raw in strategies_raw.items():
            strategy_data = _require_mapping(strategy_raw, f"strategies.{strategy_id}")
            if "requires_analytics" not in strategy_data:
                raise ValueError(f"strategies.{strategy_id}: requires_analytics missing")
            strategies[strategy_id] = _parse_requires(
                strategy_data["requires_analytics"], f"strategies.{strategy_id}"
            )

        return cls(features, brokers, strategies)

    def get(self, feature: str, broker: str, user_id: str | None = None) -> CapabilityEntry:
        # WHY user_id ignored: Day-1 broker-only gate per spec §Inputs; per-tenant
        # override reserved for Day-2 — parameter exists so callers never change.
        if not feature:
            return CapabilityEntry(
                status=CapabilityStatus.UNAVAILABLE, reason="unknown_feature: empty key"
            )
        if not broker:
            return CapabilityEntry(
                status=CapabilityStatus.UNAVAILABLE, reason="unknown_broker: empty key"
            )
        base = self._features.get(feature)
        if base is None:
            # Unknown feature → synthetic UNAVAILABLE; never raise in the hot path.
            return CapabilityEntry(
                status=CapabilityStatus.UNAVAILABLE, reason=f"unknown_feature {feature!r}"
            )
        override = self._brokers.get(broker, {}).get(feature)
        return override if override is not None else base

    def is_available(
        self, feature: str, broker: str, *, allow_proxy: bool = False, user_id: str | None = None
    ) -> bool:
        status = self.get(feature, broker, user_id=user_id).status
        if status is CapabilityStatus.PROXY_LIMITED:
            return allow_proxy
        return status in _HONEST

    def is_strategy_eligible(self, strategy_id: str, broker: str) -> tuple[bool, str]:
        required = self._strategies.get(strategy_id)
        if required is None:
            return False, f"unknown_strategy {strategy_id!r}"
        for feature in required:  # declared order; first failing feature wins
            status = self.get(feature, broker).status
            if status not in _HONEST:
                return False, f"needs {feature} {status.value}"
        return True, "eligible"
