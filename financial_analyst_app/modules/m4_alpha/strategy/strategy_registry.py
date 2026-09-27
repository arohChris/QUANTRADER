"""
M4.1 — strategy_registry: discovery + contract validation = the P22 gate.
WHY: registry.load() is the FIRST consumer of the m4.2 in-module contract, so
  it is where PROVISIONAL becomes enforced (frozen at M4.1 Step 2): a broken or
  isolation-leaking module never reaches Signal production. Load rebuilds from
  disk then atomically swaps — idempotent, never a partial registry.
"""
from __future__ import annotations

import importlib
import inspect
import math
import pkgutil
import re
import sys
from collections.abc import Callable
from types import ModuleType
from typing import cast

from .base_strategy import StrategyEntry

_SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
# WHY forbidden attrs: strategies must stay stateless pure functions — a
#   module-level provider/clock/as_of/candles smuggles I/O or wall-clock into
#   reproducible math (PIT + determinism law).
_ISOLATION = ("provider", "clock", "as_of", "candles")


class StrategyContractError(ValueError):
    """M4.2 module contract broken at load — fail loud, never partial."""


class UnknownStrategyError(ValueError):
    """Name not in the registry (typo/stale config) — typed, never KeyError."""


class StrategyRegistry:
    def __init__(self, package: str = "modules.m3_analytics.strategies") -> None:
        self._package = package
        self._entries: dict[str, StrategyEntry] = {}

    def load(self) -> None:
        _purge(self._package)  # WHY: a stale sys.modules parent pins an old
        #   __path__ (tests swap package dirs between loads) — fresh import each
        #   load keeps discovery honest AND idempotent for the real package.
        importlib.invalidate_caches()
        pkg = importlib.import_module(self._package)
        found: dict[str, StrategyEntry] = {}
        for info in pkgutil.iter_modules(pkg.__path__):  # itermodules alias gone in 3.14
            if info.name.startswith("_"):  # skips __init__ + _hidden (tested)
                continue
            mod = importlib.import_module(f"{self._package}.{info.name}")
            entry = self._validate(info.name, mod)
            if entry.name in found:  # defense-in-depth (note 2: same-dir dupes
                raise StrategyContractError(  #   are structurally impossible)
                    f"duplicate strategy NAME {entry.name!r}")
            found[entry.name] = entry
        self._entries = dict(sorted(found.items()))  # sorted order (note 3)

    def _validate(self, stem: str, mod: ModuleType) -> StrategyEntry:
        name = getattr(mod, "NAME", None)
        if not isinstance(name, str) or not _SNAKE.fullmatch(name):
            raise StrategyContractError(f"NAME must be snake_case str, got {name!r}")
        if name != stem:
            raise StrategyContractError(f"NAME {name!r} != module stem {stem!r}")
        slots = getattr(mod, "FEATURE_SLOTS", None)
        if not isinstance(slots, dict) or not slots:
            raise StrategyContractError(f"{name}: FEATURE_SLOTS must be a non-empty dict")
        seen: set[str] = set()
        for slot, value in slots.items():
            if not (isinstance(value, tuple) and len(value) == 2
                    and isinstance(value[0], str) and isinstance(value[1], dict)):
                raise StrategyContractError(
                    f"{name}: slot {slot!r} must be (analytic, overrides dict)")
            if value[0] in seen:
                raise StrategyContractError(
                    f"{name}: analytic {value[0]!r} shared by 2 slots")
            seen.add(value[0])
        defaults = getattr(mod, "PARAM_DEFAULTS", None)
        if not isinstance(defaults, dict):
            raise StrategyContractError(f"{name}: PARAM_DEFAULTS must be a dict")
        for key, val in defaults.items():
            if (isinstance(val, bool) or not isinstance(val, (int, float))
                    or not math.isfinite(val)):
                raise StrategyContractError(
                    f"{name}: PARAM_DEFAULTS[{key!r}] not finite: {val!r}")
        for attr in _ISOLATION:
            if hasattr(mod, attr):
                raise StrategyContractError(
                    f"{name}: module-level {attr!r} forbidden (isolation)")
        fn = getattr(mod, "evaluate", None)
        if not callable(fn):
            raise StrategyContractError(f"{name}: missing evaluate(features, params)")
        try:
            sig = list(inspect.signature(fn).parameters)
        except (TypeError, ValueError) as exc:
            raise StrategyContractError(
                f"{name}: evaluate signature unreadable: {exc}") from exc
        if sig != ["features", "params"]:
            raise StrategyContractError(
                f"{name}: evaluate signature {sig} != ['features', 'params']")
        return StrategyEntry(
            name=name, module=mod, slots=dict(slots), defaults=dict(defaults),
            evaluate=cast("Callable[[dict[str, float], dict[str, float]], float]", fn))

    def names(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def entries(self) -> tuple[StrategyEntry, ...]:
        return tuple(self._entries.values())

    def get(self, name: str) -> StrategyEntry:
        try:
            return self._entries[name]
        except KeyError:
            raise UnknownStrategyError(f"unknown strategy {name!r}") from None


def _purge(package: str) -> None:
    for key in [k for k in sys.modules
                if k == package or k.startswith(package + ".")]:
        del sys.modules[key]
