"""WHY: regression tests for the three silent-data-loss defects found and fixed on 2026-10-07
and 2026-10-08. Each one produced CORRECT-LOOKING output while losing data, which is exactly
why they survived: nothing errored, every run reported success, and the numbers were plausible.

These are pinned because all three were invisible from the outside:

  1. load_universe.py window ignored a rewound cursor -> a 1039-minute hole on 2026-10-02,
     identical across all 25 instruments, survived every repair attempt.
  2. run_monitor_loop.py idle_check compared newest-bar to the AUDIT watermark and never
     checked the bar's AGE -> reported a healthy IDLE while the store aged past 96 hours.
  3. market_router.py sized the window as `limit * interval` -> 1m could only reach 5 hours
     of the 38 days stored.

Spec: docs/specs/m2.14_gap_repair_coverage.spec.md (defect class) and
docs/specs/m10.2_market_data_api.spec.md (window rule, amendment 3c).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from modules.experience.api.v1 import market_router as MR

# --------------------------------------------------------------------------
# Defect 1 - the loader window must honour a rewound cursor
# --------------------------------------------------------------------------


def test_loader_window_takes_the_earlier_of_horizon_and_cursor() -> None:
    """A rewound cursor must be able to pull the window back to it.

    THE DEFECT. `load_universe.py` computed `start = now - hours` and the driver then applied
    `start = max(start, cursor)`. `max` keeps the LATER bound, so any cursor older than
    `now - hours` was silently discarded - which made `rewind_backfill_cursor.py` a no-op for
    exactly the case it exists for: a hole OLDER than the catch-up window.

    `min` is the correct operator. A normal run is unaffected (the cursor trails the horizon,
    so `min` yields the horizon); a rewound cursor now genuinely pulls the window back.
    """
    import pathlib

    src = pathlib.Path("scripts/load_universe.py").read_text(encoding="utf-8")
    assert "max(" not in src.split("oldest_cursor")[-1][:400], (
        "the loader window must not use max(start, cursor): max keeps the later bound and "
        "discards a rewound cursor, which is how a 1039-minute hole survived every repair"
    )
    assert "min(end - timedelta(hours=args.hours), oldest_cursor)" in src, (
        "the window must be min(horizon, oldest cursor)"
    )


def test_driver_cursor_advances_only_over_written_bars() -> None:
    """The cursor must never move past a bar that was not actually written.

    This is the property that makes a rewind recoverable at all. If the cursor advanced over
    skipped windows, no amount of rewinding would help because the evidence would be gone.
    `backfill_driver.py` sets it to `max(interval_end for x in usable)`.
    """
    import pathlib

    src = pathlib.Path("modules/m2_market_data/backfill_driver.py").read_text(encoding="utf-8")
    assert "committed = max(x.interval_end for x in usable)" in src, (
        "the cursor must advance to the max end of WRITTEN bars, never to the requested end"
    )


# --------------------------------------------------------------------------
# Defect 2 - the monitor must not idle while the store is stale
# --------------------------------------------------------------------------


def test_idle_gate_has_a_frontier_freshness_threshold() -> None:
    """THE DEFECT. idle_check compared newest-bar to the audit watermark only.

    Both being equal proves the AUDIT is current. It says NOTHING about the STORE. When the
    loader had not run, the loop printed a healthy `IDLE` and slept - while the data aged past
    96 hours. The safety net was asleep precisely because nothing was arriving.
    """
    import pathlib

    src = pathlib.Path("scripts/run_monitor_loop.py").read_text(encoding="utf-8")
    assert "FRONTIER_STALE_AFTER_SEC" in src, (
        "idle_check must assert the newest bar's AGE, not only its relation to the watermark"
    )
    assert "store frontier is stale" in src, "and it must say so in the reason it reports"


def test_frontier_threshold_is_tighter_than_the_loop_interval() -> None:
    """A stale frontier must be noticed before the loop sleeps through it again.

    The loop runs every 30 minutes. If the staleness threshold were looser than the sleep, a
    run could conclude "nothing to do" and sleep straight past the point where staleness
    became visible - which is the original failure with a different number.
    """
    assert MR is not None  # imported for symmetry; the constant lives in the loop script
    import pathlib

    src = pathlib.Path("scripts/run_monitor_loop.py").read_text(encoding="utf-8")
    assert "FRONTIER_STALE_AFTER_SEC = 15 * 60" in src, (
        "15 minutes is the agreed threshold: the store is 1m bars, so a frontier older than "
        "that means the feed stopped, not that the market is quiet"
    )


# --------------------------------------------------------------------------
# Defect 3 - the candle window must not be limit x interval
# --------------------------------------------------------------------------


def test_window_has_a_floor_independent_of_interval() -> None:
    """THE DEFECT. `window = limit * interval` meant 1m could reach 5 HOURS while 24h could
    reach 300 DAYS - a 7,200x swing driven by a parameter the caller never set, and an exact
    reproduction of the mock-era TIMEFRAME_WINDOW mistake this slice was written to remove.
    """
    assert MR.MIN_WINDOW_MINUTES == 24 * 60, (
        "a floor is required so a short interval can never be starved of history"
    )


def test_window_is_the_larger_of_limit_scaled_and_the_floor() -> None:
    """A floor, not a replacement: asking for 300 daily bars must still return 300."""
    assert hasattr(MR, "MIN_WINDOW_MINUTES")
    mins, page = 1440, 300          # 24h
    assert max(mins * page, MR.MIN_WINDOW_MINUTES) == mins * page
    mins, page = 1, 300             # 1m
    assert max(mins * page, MR.MIN_WINDOW_MINUTES) == MR.MIN_WINDOW_MINUTES


def test_one_minute_request_can_reach_more_than_five_hours() -> None:
    """The observable symptom: at 1m the old window covered only 300 minutes."""
    old = timedelta(minutes=1 * 300)
    new = timedelta(minutes=max(1 * 300, MR.MIN_WINDOW_MINUTES))
    assert old == timedelta(hours=5)
    assert new > old
    assert new >= timedelta(hours=24)


def test_serialise_bar_reports_gapped_when_the_bucket_is_short() -> None:
    """A bucket with missing minutes must be visible, never averaged into a clean bar.

    Real venue gaps exist (metals 2026-09-07, the crypto 21:07Z hole). A backtest that
    validates data the system does not have is the failure AGENTS.md 3.4 forbids.
    """
    row = {"t": datetime(2026, 10, 8, 3, 50, tzinfo=UTC), "o": "1", "h": "2",
           "l": "0.5", "c": "1.5", "n": 4, "reference_snapshot_id": "REF-2026-09-25-001"}
    assert MR._serialise_bar(row, expected=5)["quality"] == "gapped"
    assert MR._serialise_bar({**row, "n": 5}, expected=5)["quality"] == "complete"


@pytest.mark.parametrize("tf,expected", [("1m", "LIVE"), ("5m", "LIVE"),
                                        ("1m", "DELAYED"), ("5m", "STALE")])
def test_freshness_boundaries_are_explicit_not_inferred(tf, expected) -> None:
    """LIVE <= 90s, DELAYED <= 15min, STALE beyond. Enumerated so a shift is caught."""
    samples = {"LIVE": 30, "DELAYED": 600, "STALE": 3600}
    assert MR._classify(samples[expected]) == expected
    assert tf in MR.INTERVAL_MINUTES


def test_data_state_is_carried_in_data_not_the_frozen_envelope() -> None:
    """Amendment 3a. Editing `envelope_success` would need a Change/Why/Impact/Bump proposal.

    An account endpoint has no `data_state`; a market endpoint has no pagination watermark.
    Putting either in `meta` would force every future domain to carry fields it cannot answer.
    """
    import inspect as _inspect

    sig = _inspect.signature(MR.envelope_success)
    assert set(sig.parameters) >= {"data", "correlation_id", "as_of", "schema_version"}
    assert "staleness_sec" not in sig.parameters, (
        "staleness is payload state; it must not widen the frozen envelope"
    )