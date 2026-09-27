"""
M4.1 — parameter_registry: strategy yaml params + precedence (E7/E8/E9).
WHY config owns identity: reparameterize entries without redeploying code;
  the frozen Signal.version is a STR, so yaml float 2.1 is normalized to
  "2.1" here (contract-note 5). Precedence: request > yaml > PARAM_DEFAULTS.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml


class ParameterConfigError(ValueError):
    """Strategy yaml malformed — refuse loud at startup, never guess (3.13)."""


class ParameterRegistry:
    def __init__(self, config_dir: Path) -> None:
        self._dir = Path(config_dir)
        self._params: dict[str, dict[str, Any]] = {}
        self._ids: dict[str, str] = {}
        self._versions: dict[str, str] = {}

    def load(self, names: Iterable[str]) -> None:
        for name in names:
            path = self._dir / f"{name}.yaml"
            if not path.exists():
                continue  # no yaml -> defaults path (tested); missing is legal
            self._load_one(name, path)

    def _load_one(self, name: str, path: Path) -> None:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ParameterConfigError(f"{path.name}: YAML parse error: {exc}") from exc
        if not isinstance(raw, dict):
            raise ParameterConfigError(f"{path.name}: root must be a mapping")
        version = raw.get("version", "1.0")
        if isinstance(version, bool) or not isinstance(version, (str, int, float)):
            raise ParameterConfigError(
                f"{path.name}: version must be a str/number, got {version!r}")
        params = raw.get("params", {})
        if params is None:  # explicit `params:` with no body == absent
            params = {}
        if not isinstance(params, dict):
            raise ParameterConfigError(f"{path.name}: params must be a mapping")
        for key, val in params.items():
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ParameterConfigError(
                    f"{path.name}: params.{key} must be a number, got {val!r}")
        sid = raw.get("strategy_id", name)
        if not isinstance(sid, str):
            raise ParameterConfigError(
                f"{path.name}: strategy_id must be a str, got {sid!r}")
        self._ids[name] = sid
        self._versions[name] = str(version)  # WHY: 2.1 (float) -> "2.1" (str)
        self._params[name] = dict(params)  # entry_model/stop_model read-ignored

    def version(self, name: str) -> str:
        """Strategy version string — default "1.0" when no yaml exists."""
        return self._versions.get(name, "1.0")

    def strategy_id(self, name: str) -> str:
        """yaml strategy_id else bare name — stable identity without config."""
        return self._ids.get(name, name)

    def effective(self, name: str, *, defaults: dict[str, float],
                  request: dict[str, float] | None = None) -> dict[str, float]:
        merged: dict[str, Any] = dict(defaults)
        merged.update(self._params.get(name, {}))  # yaml beats module defaults
        if request:
            merged.update(request)  # request beats yaml
        return merged
