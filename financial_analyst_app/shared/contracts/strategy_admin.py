"""
M1.5 - StrategyAdmin - GLOBAL platform approval for one strategy

WHY: one admin flip must govern ALL users at once and survive restarts (a DB row,
never memory-only state - AGENTS.md 3.9). Default TRUE = the strategy is available
to all users by default (human law 2026-09-30); a user row can NEVER resurrect an
unapproved strategy (m1.5 spec Run rule - precedence change vs lifecycle.py:29).
"""
from __future__ import annotations

from .base import AwareUtcDatetime, BaseContract


class StrategyAdmin(BaseContract):
    strategy_id: str  # registered strategy name
    admin_approved: bool = True  # global gate; absent DB row = default TRUE (opt-out)
    updated_at: AwareUtcDatetime  # UTC, system-managed on every write


# Consumers: M4 (runner gate), M10.2 (admin API)
StrategyAdmin.model_rebuild()
