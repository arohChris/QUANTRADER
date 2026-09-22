"""
M1.2 — Clock Tests — SimulatedClock deterministic, SystemClock UTC
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from shared.core.clock import SimulatedClock, SystemClock


def test_system_clock_is_utc() -> None:
    clock = SystemClock()
    now = clock.now()
    assert now.tzinfo == timezone.utc


def test_simulated_clock_advance() -> None:
    start = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    clock = SimulatedClock(start)
    assert clock.now() == start
    clock.advance(timedelta(seconds=60))
    assert clock.now() == start + timedelta(seconds=60)
    clock.advance(30)  # float seconds
    assert clock.now() == start + timedelta(seconds=90)


def test_simulated_clock_requires_utc() -> None:
    naive = datetime(2024, 1, 1, 10, 0, 0)  # no tzinfo
    with pytest.raises(ValueError, match="timezone-aware"):
        SimulatedClock(naive)


@pytest.mark.asyncio
async def test_simulated_clock_sleep_is_instant() -> None:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    clock = SimulatedClock(start)
    # WHY test? Backtest 1 year must not take wall 1 year — sleep must be instant
    await clock.sleep(60)
    assert clock.now() == start + timedelta(seconds=60)


@pytest.mark.asyncio
async def test_system_clock_sleep_is_awaitable() -> None:
    clock = SystemClock()
    # Just verify it doesn't raise and sleeps a tiny amount
    await clock.sleep(0.01)


def test_clock_never_naive() -> None:
    # WHY? All modules must use clock.now() not datetime.now() — naive would break comparison
    # This test documents the rule — grep for datetime.now() in code should be 0 outside clock.py
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for py in (root / "shared").rglob("*.py"):
        if py.name == "clock.py":
            continue
        text = py.read_text(encoding="utf-8")
        # Allow datetime.now only if it's in comment or clock.py itself
        if "datetime.now(" in text and "timezone.utc" not in text:
            # Check it's not just in a comment/docstring — simple heuristic
            for line in text.splitlines():
                if "datetime.now(" in line and not line.strip().startswith("#") and "timezone.utc" not in line:
                    offenders.append(f"{py.relative_to(root)}: {line.strip()}")
    # We allow .now(timezone.utc) but not naive .now()
    # This is a soft check — just ensure no naive datetime.now() in shared
    naive = [o for o in offenders if "datetime.now()" in o and "timezone" not in o]
    assert naive == [], f"Naive datetime.now() found (use clock.now()): {naive}"
