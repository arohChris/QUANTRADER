"""WHY: D4 analytic registry — the ONLY discovery surface between the Feature Engine
and the one-file-per-analytic `analytics/` package (spec m3.1 REV3 §Contracts).

Enforces the M3.2 analytic contract AT REGISTER TIME (fail-loud, never at request
time): NAME / REQUIREMENTS / compute(candles, params) signature exactly, REGIME_LABEL
when provides_regime, no dependency cycles (amendments M3.1-a/b). The Isolation Law
is structural here: a compute that smuggles a provider/clock parameter is rejected
before it can ever run — an analytic never selects its own timeframe or pulls data.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DataRequirement(BaseModel):
    """Frozen depth/dependency declaration for one analytic — planner input.

    WHY frozen + extra=forbid: requirements feed the Requirement Planner window;
    a typo'd key silently shrinking history is a look-ahead bug waiting to happen.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    needs: tuple[str, ...] = ()
    minimum_history: int = Field(default=1, ge=1, description="bars of the resolved timeframe")
    warmup: int = Field(default=0, ge=0, description="extra bars allowed via WARMUP_INCOMPLETE")
    dependencies: tuple[str, ...] = ()
    defaults: dict[str, float] = Field(default_factory=dict)
    provides_regime: bool = False
    history_duration: str | None = Field(
        default=None, description="calendar depth e.g. '3d' — ceil(duration/timeframe) bars"
    )


class AnalyticsRegistry:
    """Register-once lookup — sync dict reads, zero I/O (analytics budget <20ms)."""

    def __init__(self) -> None:
        self._modules: dict[str, Any] = {}

    def register(self, analytic: Any) -> None:
        # WHY validate here, not at compute: a bad analytic must fail at startup,
        # never mid-request when money-time data is already flowing.
        name = getattr(analytic, "NAME", None)
        if not isinstance(name, str) or not name:
            raise ValueError(f"analytic must expose a non-empty NAME, got {name!r}")
        if name in self._modules:
            raise ValueError(f"duplicate analytic name {name!r}")

        compute = getattr(analytic, "compute", None)
        if not callable(compute):
            raise ValueError(  # noqa: TRY004 — register errors are ALL ValueError (tests match it)
                f"analytic {name!r} must expose a callable compute"
            )
        # Isolation Law (M3.1-b): exactly (candles, params) — no provider/clock/as_of
        # may appear, so an analytic can never fetch its own series.
        params = list(inspect.signature(compute).parameters)
        if params != ["candles", "params"]:
            raise ValueError(
                f"compute signature for {name!r} must be exactly (candles, params), got {params}"
            )

        requirements = getattr(analytic, "REQUIREMENTS", None)
        if not isinstance(requirements, DataRequirement):
            raise ValueError(  # noqa: TRY004 — same ValueError contract as duplicate/cycle/signature
                f"analytic {name!r} REQUIREMENTS must be a DataRequirement"
            )

        if requirements.provides_regime and not callable(getattr(analytic, "REGIME_LABEL", None)):
            raise ValueError(
                f"analytic {name!r} declares provides_regime=True but exposes no REGIME_LABEL"
            )

        # Cycle check BEFORE insert so request-time topo sort can trust the graph.
        # Forward refs (dep not yet registered) are legal — closure skips unregistered.
        for dep in requirements.dependencies:
            if dep == name or self._reaches(dep, name):
                raise ValueError(f"dependency cycle detected: {name!r} -> {dep!r}")

        self._modules[name] = analytic

    def _reaches(self, start: str, target: str) -> bool:
        """True if `target` is reachable from `start` by following declared deps."""
        stack = [start]
        seen: set[str] = set()
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node in seen:
                continue
            seen.add(node)
            module = self._modules.get(node)
            if module is None:
                continue  # unregistered link — no edge yet
            stack.extend(module.REQUIREMENTS.dependencies)
        return False

    def has(self, name: str) -> bool:
        return name in self._modules

    def get(self, name: str) -> Any:
        return self._modules[name]

    def names(self) -> tuple[str, ...]:
        return tuple(self._modules)

    def execution_order(self, names: Iterable[str]) -> tuple[str, ...]:
        """Topological deps-first order over the registered closure (spec flow step 7).

        WHY closure: a dependency needing more bars widens the planner window
        (Planner Dependency Law) and must compute before its dependent.
        """
        order: list[str] = []
        seen: set[str] = set()

        def visit(name: str) -> None:
            if name in seen:
                return
            module = self._modules.get(name)
            if module is None:
                return  # registered-only closure — unknown deps are the gate's call
            for dep in module.REQUIREMENTS.dependencies:
                visit(dep)
            seen.add(name)
            order.append(name)

        for name in names:
            visit(name)
        return tuple(order)

    @classmethod
    def from_package(cls) -> AnalyticsRegistry:
        """D4 discovery: scan `modules/m3_analytics/analytics/` for compute files.

        Empty package → empty registry, never a crash (test
        `all_unavailable_returns_empty_unavailable_no_raise` proves D4 compliance).
        A non-conforming file raising ValueError at import/register is intentional:
        fail-loud startup beats a feature silently missing at request time.
        """
        registry = cls()
        package = importlib.import_module("modules.m3_analytics.analytics")
        for info in pkgutil.iter_modules(package.__path__):
            if info.name.startswith("_"):
                continue
            module = importlib.import_module(f"modules.m3_analytics.analytics.{info.name}")
            registry.register(module)
        return registry
