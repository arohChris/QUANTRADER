"""
M1.2 — State Tracker — per-engine lifecycle state enum
WHY not just health? State tracks lifecycle phase (initializing → healthy → shutting_down)
     while health tracks runtime condition (healthy vs degraded vs unhealthy).
"""
from __future__ import annotations

from enum import Enum


class State(str, Enum):
    initializing = "initializing"
    healthy = "healthy"
    degraded = "degraded"
    unhealthy = "unhealthy"
    shutting_down = "shutting_down"
    offline = "offline"
