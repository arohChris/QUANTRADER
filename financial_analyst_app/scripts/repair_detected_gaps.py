"""Find gaps the VENUE has that we do not, and fill them from the venue. Automatically.

WHY THIS IS NOT THE DRIVER. `BackfillDriver` walks FORWARD from
`backfill_cursors.reached`, so a hole that sits BEHIND the cursor is invisible to it.
Measured 2026-09-30: audit class F1 correctly found a missing bar at 2026-09-29T08:21Z,
then `load_universe.py` reported "RESULT: PASS - all 25 instruments complete" and the bar
was STILL absent. Catching the loss and repairing it were two manual steps, and the second
one silently did nothing.

The driver cannot fix a hole behind it, but a TARGETED fetch can: ask the venue for a small
padded window around the hole, and write what comes back. That path never touches a cursor,
so it cannot disturb incremental catch-up, and it costs one broker request instead of
re-walking a month of history.

ACCURACY IS THE WHOLE POINT, so every write is gated. A repair tool that can invent a bar
is worse than no repair tool, because the invented bar is indistinguishable from real data
in a backtest. Therefore a bar is written only when ALL of these hold:

  1. The VENUE returned it. Nothing is ever synthesised, interpolated or carried over from
     a neighbour. A minute the broker does not have cannot be filled, and is left open.
  2. It falls inside a CALENDAR-OPEN window. This is what stops a repair from writing a
     phantom bar into a closure if a response is ever re-anchored. The fetcher already
     clips responses to the caller's window (this is what fixed audit class C1), and this
     check is the second, independent gate on the same failure.
  3. Its epoch is inside the requested window.
  4. We do not already have it (the writer is ON CONFLICT DO NOTHING anyway, so this is
     belt and braces rather than the only defence).
  5. It carries real lineage - the writer is fail-closed on `reference_snapshot_id` and
     refuses to write without it.

After writing, every repaired minute is re-read from the database and confirmed present. A
repair that did not land is reported as a failure, never as a success.

Bounded on purpose: `--max-repairs` caps writes per run, so a systemic bug cannot turn one
scheduled run into thousands of venue requests.

Idempotent. Touches no cursor. Deletes nothing.

Usage:
    python scripts/repair_detected_gaps.py --top 25 --max-repairs 50 [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.m2_market_data.history import DerivHistoryFetcher, WebSocketTransport
from modules.reference.reference import current_snapshot_id, to_canonical
from scripts.load_universe import DERIV, MIN, PgWriter
from scripts.verify_universe_data import _calendar, _fail, _note, dsn

PAD = MIN  # one minute of slack either side of the hole


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _known_venue_absences(conn: Any, instrument: str) -> set[datetime]:
    """Minutes already baselined as 'the venue does not have this either'.

    WHY THIS IS THE MEMORY THAT MAKES A FULL SCAN AFFORDABLE. M2.14 changed this tool
    from a watermark-bounded scan to a full-range one, because the audit's watermark is a
    DIFFERENT component's trust boundary (it advances on a 5-bar sample, so it does not
    mean "no gaps before here"). A full range scan re-proposes every confirmed venue
    absence on every run, which would spend broker calls forever re-learning the same
    lesson. This set is what makes the next run converge to found=0.
    """
    try:
        rows = await conn.fetch(
            "SELECT interval_start FROM known_venue_gaps WHERE instrument_id=$1",
            instrument,
        )
    except Exception:  # noqa: BLE001 - migration may not have been applied yet.
        # Degrade to "no baseline exists" rather than disabling the safety net. A missing
        # table must cost us a few redundant probes, never the coverage itself.
        return set()
    return {r["interval_start"] for r in rows}


async def record_venue_absence(
    conn: Any, instrument: str, minute: datetime, *, reason: str, broker: str = DERIV
) -> None:
    """Baseline one minute as venue-absent so later runs stop re-probing it.

    This records ABSENCE and never a value. It grants no licence to synthesise, fill, or
    rewrite a bar - the only thing that may write a candle is the venue returning it.
    """
    await conn.execute(
        "INSERT INTO known_venue_gaps (instrument_id, broker, interval_start, reason) "
        "VALUES ($1,$2,$3,$4) ON CONFLICT (instrument_id, broker, interval_start) "
        "DO NOTHING",
        instrument, broker, minute, reason,
    )


async def find_repairable(
    conn: Any, instrument: str, *, cap: int
) -> list[datetime]:
    """Every calendar-open minute in the whole stored range that we are missing.

    WHY THERE IS NO `since` PARAMETER (spec M2.14 D1, measured defect). This used to be
    called as `find_repairable(conn, instrument, <audit watermark>, cap)`, and because
    `data_audit_watermark.verified_through` equals the newest stored bar, the tool
    proposed NOTHING: measured 0 candidates with the watermark against 46 without it. The
    watermark advances on a 5-bar sample, so it cannot mean "there are no gaps before
    here" - and this tool, not `BackfillDriver`, is the only thing that can see behind the
    frontier. Removing the parameter makes the mistake unrepresentable rather than merely
    documented; `test_find_repairable_takes_no_lower_bound_parameter` pins that.

    Baselined venue absences are excluded (D2) so the scan converges instead of looping.
    """
    rows = await conn.fetch(
        "SELECT interval_start FROM candles_1m WHERE instrument_id=$1", instrument
    )
    have = {r["interval_start"] for r in rows}
    if not have:
        return []
    lo, hi = min(have), max(have)

    skip = await _known_venue_absences(conn, instrument)
    cal = _calendar()
    candidates: list[datetime] = []
    for a, b in cal.open_intervals(instrument, lo, hi, DERIV):
        t = a
        while t < b:
            if t not in have and t not in skip:
                candidates.append(t)
            t += MIN
    # Newest first: if we hit the cap, repair the most recent data, which is what a
    # strategy or a live read is most likely to be touching.
    candidates.sort(reverse=True)
    return candidates[:cap]


async def repair_instrument(
    conn: Any,
    pool: Any,
    fetcher: Any,
    writer: Any,
    instrument: str,
    probes: list[datetime],
    dry: bool,
) -> tuple[int, int, int, int]:
    """Returns (found, filled, confirmed, fillable).

    `filled`/`confirmed` are only ever incremented on a real write. `fillable` counts what
    a real run WOULD write, so a detect-only run reports what it found without pretending
    to have written it - and without reporting a phantom PARTIAL failure for the sin of not
    having written anything.
    """
    found = filled = confirmed = fillable = 0
    cal = _calendar()
    for probe in probes:
        found += 1
        start, end = probe - PAD, probe + PAD + MIN
        try:
            bars = await fetcher.fetch(instrument, DERIV, start, end)
        except Exception as exc:  # noqa: BLE001 - a venue refusal is not a repairable gap
            _note(f"repair [{instrument}] {probe.isoformat()}: venue query failed "
                  f"({type(exc).__name__}: {exc}) - left open, NOT counted as filled")
            continue

        wanted = {probe}
        usable = [
            b for b in bars
            # Gate 1: the venue returned it (it is in `bars`).
            # Gate 2: it is inside a calendar-open window - the anti-re-anchoring gate.
            if cal.is_open(instrument, b.interval_start, broker=DERIV)
            # Gate 3: it is inside the requested window.
            and start <= b.interval_start < end
            # Gate 4: it is a minute we are actually missing.
            and b.interval_start in wanted
        ]
        if not usable:
            # The venue does not have it either. This is venue absence, not our loss, and
            # it is NOT repairable. Say so rather than writing anything.
            _note(f"repair [{instrument}] {probe.isoformat()}: venue has no bar here either - "
                  "venue absence, not repairable, nothing written")
            # Baseline it (spec M2.14 D3) so the next run stops re-probing the same minute.
            # Without this the full-range scan re-proposes every confirmed absence forever,
            # spending broker calls to relearn a fact we already established.
            if not dry:
                await record_venue_absence(
                    conn, instrument, probe, reason="venue_has_no_bar")
            continue

        if not dry:
            await writer(usable)
            filled += len(usable)

            # Verify from the database, not from what we think we sent. A repair that did
            # not land must be reported as a failure.
            for b in usable:
                n = await conn.fetchval(
                    "SELECT count(*) FROM candles_1m WHERE instrument_id=$1 AND interval_start=$2",
                    instrument, b.interval_start,
                )
                if n:
                    confirmed += 1
                else:
                    _fail("R1", instrument,
                          f"repair of {b.interval_start.isoformat()} did not persist - the writer "
                          "reported success but the row is not readable")
        else:
            # Dry run: count what COULD be written and say so, rather than incrementing
            # `filled` and then reporting confirmed=0 as a PARTIAL failure. A detect-only
            # run that reports a failure for not having written anything is a monitor
            # people learn to ignore.
            fillable += len(usable)
    return found, filled, confirmed, fillable


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--max-repairs", type=int, default=50,
                    help="hard cap on venue repair queries per instrument")
    ap.add_argument("--apply", action="store_true", help="write; without it, detect only")
    args = ap.parse_args()

    import json

    selection = json.loads(
        (ROOT / "config" / "reference" / "universe_screen.json").read_text(encoding="utf-8")
    )
    chosen = selection["selected"][: args.top]

    conn = await asyncpg.connect(
        dsn(), timeout=20
    )
    pool = await asyncpg.create_pool(
        dsn(), timeout=20
    )
    total_found = total_filled = total_confirmed = total_fillable = 0
    try:
        async with WebSocketTransport() as transport:
            fetcher = DerivHistoryFetcher(transport=transport)
            writer = PgWriter(pool, dry=not args.apply,
                              reference_snapshot_id=current_snapshot_id())

            print("=" * 78)
            print(f"GAP REPAIR ({'APPLY' if args.apply else 'DETECT ONLY - nothing will be written'})")
            print("=" * 78)
            for row in chosen:
                try:
                    instrument = to_canonical(row["native"], DERIV)
                except Exception as exc:  # noqa: BLE001 - per-instrument isolation, so one
                    # unresolvable screen entry cannot abort the other 24. Logged rather
                    # than swallowed, matching rewind_backfill_cursor.py.
                    _note(f"repair {row.get('native')!r} SKIP unresolved: "
                          f"{type(exc).__name__}: {exc}")
                    continue
                # Scan the WHOLE stored range (spec M2.14 D1). This used to start from
                # `data_audit_watermark.verified_through`, which equals the newest bar and
                # therefore hid every historical hole - measured 0 candidates against 46.
                # A scheduled run wants "what is broken now AND what broke behind me", and
                # the audit watermark is not evidence for either.
                probes = await find_repairable(conn, instrument, cap=args.max_repairs)
                if not probes:
                    print(f"{instrument:<18} nothing to repair")
                    continue
                found, filled, confirmed, fillable = await repair_instrument(
                    conn, pool, fetcher, writer, instrument, probes, dry=not args.apply)
                total_found += found
                total_fillable += fillable
                total_filled += filled
                total_confirmed += confirmed
                if not args.apply:
                    print(f"{instrument:<18} found={found:<4} fillable={fillable:<4} "
                          f"(detect only - nothing written)")
                else:
                    state = "OK" if filled == confirmed else "PARTIAL"
                    print(f"{instrument:<18} found={found:<4} filled={filled:<4} "
                          f"confirmed={confirmed:<4} {state}")
                if found and not fillable and not filled:
                    _note(f"repair [{instrument}] {found} gap(s) probed, none fillable - the "
                          "venue does not have these bars either")
    finally:
        await pool.close()
        await conn.close()

    print()
    print(f"TOTAL found={total_found} fillable={total_fillable} "
          f"filled={total_filled} confirmed={total_confirmed}")
    if not args.apply:
        print(f"RESULT: DETECT ONLY - {total_fillable} gap(s) would be filled; nothing written")
        return 0
    if total_found == 0:
        print("RESULT: PASS - no repairable gaps")
        return 0
    if total_filled == total_confirmed:
        print("RESULT: PASS - every repairable gap filled and verified against the database")
        return 0
    print(f"RESULT: FAIL - {total_found - total_confirmed} repair(s) did not verify")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
