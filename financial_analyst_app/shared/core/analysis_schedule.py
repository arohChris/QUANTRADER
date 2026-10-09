"""
M1.5 — analysis_schedule — activation-anchored repeat cadence (pure time math)

WHY: the strategy's analysis must repeat every timeframe interval starting at the
user's activation time, and a timeframe change must re-derive the cadence WITHOUT
moving the anchor (m1.5 spec Schedule rule — human law 2026-09-30). Pure `def`,
no I/O, no clock reads: the scheduler (Step C wiring) supplies `now`, so this stays
deterministic and testable.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from shared.timeframes import INTERVAL_MINUTES

_ZERO: Final = timedelta(0)


def _require_aware(name: str, value: datetime) -> None:
    # WHY: naive datetimes silently pick a local offset — refuse, don't guess (§3.8).
    if value.tzinfo is None or value.utcoffset() != _ZERO:
        raise ValueError(f"{name} must be tz-aware UTC, got {value!r}")


def next_run_at(activation_at: datetime, timeframe: str, now: datetime) -> datetime:
    """Smallest instant `activation_at + n * timeframe` that is >= `now` (n >= 0).

    - now <= activation_at -> activation_at itself (scheduled wait, n = 0).
    - O(1) via floor division: a months-old anchor never loops interval-by-interval.
    - unknown timeframe -> ValueError (typed, never a silent default interval).
    """
    _require_aware("activation_at", activation_at)
    _require_aware("now", now)
    minutes = INTERVAL_MINUTES.get(timeframe)
    if minutes is None:
        raise ValueError(
            f"unknown timeframe {timeframe!r} — registry keys: {sorted(INTERVAL_MINUTES)}"
        )
    interval = timedelta(minutes=minutes)
    if now <= activation_at:
        return activation_at
    elapsed = now - activation_at
    steps = elapsed // interval  # floor: anchor + steps*interval <= now
    base = activation_at + steps * interval
    return base if base >= now else base + interval
