"""
M1.2 — Health Aggregation + State Tracker
WHY separate from engine_interface? Health aggregation is cross-engine concern (M12 reads it).
State tracker holds per-engine enum for UI badges: green/yellow/red.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel


class EngineHealth(str, Enum):
    healthy = "healthy"
    degraded = "degraded"
    unhealthy = "unhealthy"
    offline = "offline"


class HealthSnapshot(BaseModel):
    """Aggregated /health response — what GET /health returns."""

    model_config = {"frozen": True}
    status: Literal["healthy", "degraded", "unhealthy"]
    modules: dict[str, dict]


# Re-export HealthStatus for convenience
from .engine_interface import HealthStatus  # noqa: E402, F401
