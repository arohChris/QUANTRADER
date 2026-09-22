"""
M1.4 — Permissions — RBAC
WHY here not in API? Because M10 Experience API AND M10 Command both check permissions.
     Single source: ROLE_PERMISSIONS dict.

Roles: viewer (read-only), trader (can preview), admin (can kill_switch)
WHY hardcoded dict Day 1? DB-backed RBAC later. Day 1: simple, auditable, no DB query per check.
"""
from __future__ import annotations

from typing import Literal

Role = Literal["viewer", "trader", "admin"]

ROLE_PERMISSIONS: dict[str, set[str]] = {
    # WHY viewer can read portfolio+market but not risk kill_switch?
    # Principle of least privilege — viewer is dashboard-only.
    "viewer": {
        "portfolio.read",
        "market_data.read",
        "analytics.read",
        "health.read",
    },
    "trader": {
        "portfolio.read",
        "market_data.read",
        "analytics.read",
        "risk.preview",
        "risk.read",
        "order.preview",
        "order.read",
        "trade.create",
        "trade.confirm",
        "command:marketday",
        "command:forex",
        "command:risk",
    },
    "admin": {
        "*",  # all
    },
}


def can(role: Role | str, action: str) -> bool:
    """
    WHY role string not user object? So M10 API does `can(request.user.role, "risk.kill_switch")`
    without importing DB user model into core.
    """
    perms = ROLE_PERMISSIONS.get(role, set())
    return "*" in perms or action in perms


def can_user(user: object, action: str) -> bool:
    """
    Convenience — extracts role from user object (dict or object with .role).
    WHY duck-typing? Works with both Pydantic User and dict from JWT.
    """
    role = None
    if isinstance(user, dict):
        role = user.get("role")
    else:
        role = getattr(user, "role", None)
    if role is None:
        return False
    return can(role, action)


def assert_can(role: str, action: str) -> None:
    """Raises PermissionError if not allowed — for API handlers."""
    if not can(role, action):  # type: ignore[arg-type]
        from .exceptions import PermissionDeniedError

        raise PermissionDeniedError(f"Role {role!r} cannot do {action!r}")
