"""
M1.3 — Environment Overlay — which env are we in?

Environments: development | testing | paper | canary | production
WHY overlay not branches? One codebase, 5 overlays — production stricter than paper.
  Code never does `if env == "prod": limit=0.05 else: 0.1` — it does `settings.risk.max_exposure`.

Usage:
    from shared.core.environment import get_env, is_production
    if is_production(): enable_stricter_checks()
"""
from __future__ import annotations

import os
from typing import Literal

Environment = Literal["development", "testing", "paper", "canary", "production"]

VALID_ENVS: set[str] = {"development", "testing", "paper", "canary", "production"}


def get_env() -> Environment:
    env = os.getenv("ENVIRONMENT", "development")
    if env not in VALID_ENVS:
        raise ValueError(f"Invalid ENVIRONMENT={env!r} — must be one of {VALID_ENVS}")
    return env  # type: ignore[return-value]


def is_production() -> bool:
    return get_env() == "production"


def is_paper() -> bool:
    return get_env() == "paper"


def is_testing() -> bool:
    return get_env() == "testing"
