"""WHY: bible §6a.3 Temporal Resolver — the ONE place window math lives.

A65 law: depth = analytic requirement → planner → required bars → provider;
no hardcoded time-depth tables anywhere. Pure sync `def`: tuple of declared
requirements + `as_of` + timeframe in, exact `[window_start, window_end]` out.
FeatureEngine flow 5 delegates here (behavior-identical formula — m3.1
Historical Depth + Planner Dependency Laws). NEVER does I/O; NEVER imports
`modules` (import law).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from shared.timeframes import INTERVAL_MINUTES

_DURATION_RE = re.compile(r"(\d+)([mhd])")
_DURATION_UNITS = {"m": 1, "h": 60, "d": 1440}  # calendar 1w/1mo/1y deliberately absent


class TemporalRequirement(BaseModel):
    """One analytic's declared depth — planner input (frozen, extra=forbid)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    minimum_history: int = Field(ge=1, description="bars of the resolved timeframe")
    warmup: int = Field(ge=0, description="extra pristine bars (§8b quality gate)")
    history_duration: str | None = Field(
        default=None, description="calendar depth e.g. '3d' — ceil(duration/tf) bars"
    )


class TemporalResolution(BaseModel):
    """The window every consumer must fetch — callers never derive dates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    window_start: datetime  # tz-aware UTC, exclusive-of-nothing: first bar starts >= here
    window_end: datetime  # == as_of — the PIT boundary
    needed_bars: int = Field(ge=1)


def _duration_minutes(duration: str) -> int:
    """'3d' → 4320 minutes (Historical Depth Law: calendar → bars via ceil)."""
    match = _DURATION_RE.fullmatch(duration)
    if match is None or int(match.group(1)) == 0:
        raise ValueError(f"invalid history_duration {duration!r} — expected e.g. '3d'")
    return int(match.group(1)) * _DURATION_UNITS[match.group(2)]


def resolve(
    *,
    requirements: tuple[TemporalRequirement, ...],
    as_of: datetime,
    timeframe: str,
) -> TemporalResolution:
    """Union over the requirement tuple (Planner Dependency Law) → one window.

    WHY tuple-not-singular: the dependency closure is resolved as a whole —
    bible §6a.3 shows a single-requirement example; union is the m3.1 law
    (Discrepancy 2 in m3.1_provider_port spec, flagged not silent).
    """
    if not requirements:
        raise ValueError("resolve: at least one TemporalRequirement is required")
    if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
        raise ValueError("resolve: as_of must be tz-aware UTC (naive rejected)")
    if timeframe not in INTERVAL_MINUTES:
        raise ValueError(f"resolve: unknown timeframe {timeframe!r} (calendar buckets banned)")

    tf_minutes = INTERVAL_MINUTES[timeframe]
    needed_bars = 0
    for req in requirements:
        bars = req.minimum_history + req.warmup
        if req.history_duration is not None:
            bars = max(bars, -(-_duration_minutes(req.history_duration) // tf_minutes))
        needed_bars = max(needed_bars, bars)
    window_end = as_of
    try:
        window_start = window_end - timedelta(minutes=needed_bars * tf_minutes)
    except OverflowError as exc:
        # X2: depth outside datetime 1..9999 (year ~ -2.7M) — fail closed with the
        # exact int bar count (no float, never silently clamped) — spec "no overflow".
        raise ValueError(
            f"resolve: window_start underflows datetime range for "
            f"needed_bars={needed_bars} — absurd history depth rejected (fail closed)"
        ) from exc
    return TemporalResolution(
        window_start=window_start, window_end=window_end, needed_bars=needed_bars
    )
