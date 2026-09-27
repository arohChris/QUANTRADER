"""
M4.1 — lifecycle: enable/disable gate per strategy (+ per-user override).
WHY: an alpha kill switch must work without redeploy and independently of
  analytics health (3.3); default = enabled, every disable is a conscious act,
  explicit user scope beats platform scope (user_id None = platform default).
"""
from __future__ import annotations

from collections.abc import Iterable

from .strategy_registry import UnknownStrategyError


class Lifecycle:
    def __init__(self, names: Iterable[str]) -> None:
        self._known = frozenset(names)
        self._state: dict[tuple[str, str | None], bool] = {}  # None = platform

    def disable(self, name: str, user_id: str | None = None) -> None:
        self._check(name)
        self._state[(name, user_id)] = False

    def enable(self, name: str, user_id: str | None = None) -> None:
        self._check(name)
        self._state[(name, user_id)] = True

    def is_enabled(self, name: str, user_id: str | None = None) -> bool:
        self._check(name)
        for key in ((name, user_id), (name, None)):  # user override > platform
            if key in self._state:
                return self._state[key]
        return True  # default enabled

    def _check(self, name: str) -> None:
        if name not in self._known:
            raise UnknownStrategyError(f"unknown strategy {name!r}")
