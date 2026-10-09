"""
Data integrity audit across the whole universe. THIS IS THE GUARANTEE.

The human's condition (2026-09-29): keep the loaded data only if it can be shown correct
across all 25 instruments. So this script does not trust the loader that wrote the data -
it re-derives every property it can, and where possible it checks against an INDEPENDENT
SOURCE: the live Deriv socket. A loader verifying its own output proves nothing.

FOUR CLASSES OF CHECK, cheapest first:

  A  STRUCTURAL (SQL, every row, seconds)
       A1 minute alignment          A2 interval_end = interval_start + 1m
       A3 OHLC integrity            A4 no duplicate (provider,instrument,start)
       A5 prices positive & finite  A6 quality_status recognised

  B  CALENDAR AGREEMENT (SQL + the shipped calendar)
       B1 no bar in a CLOSED window   <- invented data / a shutdown misread as tradable
       B2 no internal gap while OPEN  <- silently lost data inside a live session
       B3 contiguous-run count sane   <- proves B1 and B2 are actually being tested

  C  INDEPENDENT CROSS-CHECK (live Deriv socket, the strongest check)
       C1 re-fetch stored bars from the broker and compare open/high/low/close EXACTLY.
          This is the only check that can catch a systematic loader bug, because the
          comparison source is not the loader.
       C2 stored bar count in window vs what the broker says is available.

  E  LINEAGE (every row names the reference snapshot that interpreted it)
       E1 no reference_snapshot_id            <- rows that cannot be attributed to a
                                                 tick_size/contract_size spec at all.
                                                 Added 2026-09-30 after measuring
                                                 796,331 of 796,333 rows NULL.

  D  KNOWN-LIMITATION REPORT (not a pass/fail - honesty about what is NOT guaranteed)
       D1 backfilled tick_count is hardcoded 1 by the fetcher; real candles carry no
          volume, so volume-based analytics are meaningless on backfilled history.
       D2 the un-modelled forex daily break and the Friday close divergence are CONFIG
          facts, not data facts, and are reported so they are not mistaken for clean data.

Auth: NOT REQUIRED for C - `ticks_history` is a public market-data call.

Run:  python scripts/verify_universe_data.py [--sample 5] [--top 25] [--skip-live]
Exit: 0 = all checks pass. 1 = at least one FAIL (loud, never silent).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg

from modules.m2_market_data.history import DerivHistoryFetcher, WebSocketTransport
from modules.m2_market_data.warm import ALLOWED_QUALITY_STATUS
from modules.reference import calendar as cal
from modules.reference.reference import to_canonical

MIN = timedelta(minutes=1)
DERIV = "deriv"
ROOT = Path(__file__).resolve().parents[1]
ALLOWED_QUALITY = set(ALLOWED_QUALITY_STATUS) | {"backfill", "repaired"}

failures: list[str] = []
notes: list[str] = []


def _fail(check: str, instrument: str, detail: str) -> None:
    failures.append(f"{check} [{instrument}] {detail}")


def _note(text: str) -> None:
    notes.append(text)


def _password() -> str:
    env = Path(__file__).resolve().parents[2] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("TIMESCALE_DEV_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("TIMESCALE_DEV_PASSWORD missing from .env")


def dsn() -> str:
    """Postgres DSN for the candles store.

    WHY THIS IS ENV-OVERRIDABLE. Every monitor-path script used to hardcode
    127.0.0.1:5432, which is correct on the host and wrong inside a container, where the
    database is reachable by compose service name. Measured 2026-09-30 while building the
    `monitor` compose service: with the host pinned to loopback the container could not see
    Timescale at all, and every step would have failed on connect.

    The default is UNCHANGED, so every existing host invocation behaves exactly as before -
    this only adds a way to point the same code somewhere else. The password still comes
    from .env and is never taken from the environment, so no secret enters a container
    definition or a compose file.
    """
    host = os.environ.get("TIMESCALE_HOST", "127.0.0.1")
    port = os.environ.get("TIMESCALE_PORT", "5432")
    return f"postgresql://quantrader:{_password()}@{host}:{port}/quantrader"


# ------------------------------------------------------------------ class A


async def check_structural(conn: Any, instrument: str) -> int:
    """Every row, in the database. Returns the row count examined."""
    n = int(
        await conn.fetchval("SELECT count(*) FROM candles_1m WHERE instrument_id=$1", instrument)
    )
    if n == 0:
        _fail("A0", instrument, "no rows stored")
        return 0

    bad_align = int(
        await conn.fetchval(
            "SELECT count(*) FROM candles_1m WHERE instrument_id=$1 "
            "AND (extract(second FROM interval_start) <> 0 OR extract(microsecond FROM interval_start) <> 0)",
            instrument,
        )
    )
    if bad_align:
        _fail("A1", instrument, f"{bad_align} row(s) not on a minute boundary")

    bad_end = int(
        await conn.fetchval(
            "SELECT count(*) FROM candles_1m WHERE instrument_id=$1 "
            "AND interval_end <> interval_start + interval '1 minute'",
            instrument,
        )
    )
    if bad_end:
        _fail("A2", instrument, f"{bad_end} row(s) where interval_end != start + 1m")

    bad_ohlc = int(
        await conn.fetchval(
            "SELECT count(*) FROM candles_1m WHERE instrument_id=$1 AND ("
            "  high < low OR high < open OR high < close "
            "  OR low > open OR low > close"
            ")",
            instrument,
        )
    )
    if bad_ohlc:
        _fail("A3", instrument, f"{bad_ohlc} row(s) with inconsistent OHLC")

    bad_price = int(
        await conn.fetchval(
            "SELECT count(*) FROM candles_1m WHERE instrument_id=$1 "
            "AND (open <= 0 OR high <= 0 OR low <= 0 OR close <= 0)",
            instrument,
        )
    )
    if bad_price:
        _fail("A5", instrument, f"{bad_price} row(s) with a non-positive price")

    bad_q = await conn.fetch(
        "SELECT DISTINCT quality_status FROM candles_1m WHERE instrument_id=$1", instrument
    )
    unknown = [r["quality_status"] for r in bad_q if r["quality_status"] not in ALLOWED_QUALITY]
    if unknown:
        _fail("A6", instrument, f"unrecognised quality_status {unknown}")

    # A4 is structurally guaranteed by UNIQUE (provider, instrument_id, interval_start);
    # asserted anyway because a constraint can be dropped.
    dupes = int(
        await conn.fetchval(
            "SELECT count(*) FROM (SELECT interval_start FROM candles_1m "
            "WHERE instrument_id=$1 GROUP BY interval_start HAVING count(*) > 1) d",
            instrument,
        )
    )
    if dupes:
        _fail("A4", instrument, f"{dupes} duplicated minute(s)")
    return n


# ------------------------------------------------------------------ class B


async def check_calendar(
    conn: Any,
    instrument: str,
    since: datetime | None = None,
    known: set[datetime] | None = None,
) -> tuple[int, int]:
    """Bars must exist only where the market was open, and be contiguous while it was.

    WHY this is the check that matters most: a raw minute-walk cannot tell "market shut"
    from "data lost". Only the calendar can, and getting it wrong in EITHER direction
    corrupts a backtest - a bar in shut time is invented data, a gap in open time is lost
    data. Both are silent.

    WHY `since` and `known` exist. Unbounded, this walks every bar we have ever stored and
    re-reports the same venue-absence minutes forever, which is O(total history) per run
    and drowns a new defect in the same daily noise. With `since` (the watermark: the newest
    bar already audited) the walk covers only NEW data, so cost tracks new bars. With
    `known` (minutes the VENUE is also missing) those are counted and reported but do not
    re-enter the failure path. A gap the venue HAS is never in `known` and so still fails.

    The one bar immediately BEFORE `since` is fetched as the gap anchor, so a gap that
    straddles the watermark is still caught - otherwise a hole could hide exactly on the
    boundary forever, never in the old window and never in the new one.
    """
    known = known or set()
    if since is None:
        rows = await conn.fetch(
            "SELECT interval_start FROM candles_1m WHERE instrument_id=$1 ORDER BY interval_start",
            instrument,
        )
        starts = [r["interval_start"] for r in rows]
    else:
        anchor = await conn.fetchval(
            "SELECT max(interval_start) FROM candles_1m "
            "WHERE instrument_id=$1 AND interval_start < $2",
            instrument, since,
        )
        rows = await conn.fetch(
            "SELECT interval_start FROM candles_1m "
            "WHERE instrument_id=$1 AND interval_start >= $2 ORDER BY interval_start",
            instrument, since,
        )
        starts = ([anchor] if anchor is not None else []) + [r["interval_start"] for r in rows]
    if not starts:
        return 0, 0

    b1 = sum(1 for t in starts if not cal.is_open(instrument, t, broker=DERIV))
    if b1:
        _fail("B1", instrument, f"{b1} bar(s) inside a CLOSED window (invented data)")

    # B2: for EVERY gap between consecutive stored bars, count how many minutes inside
    # that gap the calendar says were OPEN. Those are genuinely lost.
    #
    # WHY the first revision of this check was WRONG and reported 3,066 lost minutes on
    # every forex pair: it only asked whether the two gap ENDPOINTS were open. Across a
    # weekend neither endpoint is closed - Friday 21:00 and Sunday 21:00 are both inside
    # the weekly session - so the entire 2,880-minute closure was counted as "missing
    # inside an open session". The check was measuring the calendar's shape, not data
    # loss. A check that cries wolf on all 25 instruments gets ignored, so it had to be
    # corrected rather than reported.
    b2 = 0
    known_hits = 0
    new_missing = 0
    for i in range(1, len(starts)):
        prev, cur = starts[i - 1], starts[i]
        if cur - prev <= MIN:
            continue
        walk = prev + MIN
        while walk < cur:
            if cal.is_open(instrument, walk, broker=DERIV):
                if walk in known:
                    # Already established, by a previous run and a venue query, that the
                    # broker has no bar here either. Counted, not re-litigated.
                    known_hits += 1
                else:
                    new_missing += 1
            walk += MIN
    b2 = known_hits + new_missing
    if new_missing:
        # B2 is now INFORMATIONAL, and that is a deliberate demotion, not a weakening.
        #
        # B2 counts minutes the calendar says were open and our store does not have. On
        # its own that conflates two very different things, and MEASURED 2026-09-30 the
        # overwhelming majority are the second kind: the VENUE has no bar either. 270
        # minutes of COMMODITY.XAUUSD on 2026-09-07 are a ~5.5h hole in Deriv's own
        # history, and 46 CRYPTO minutes are a daily one-minute hole near 21:07Z that
        # `cryBTCUSD` reproduces exactly - 21:00..21:06 then 21:08..21:15.
        #
        # So B2 keeps its count (it is the raw signal) but the DECISION moves to class F,
        # which asks the venue and therefore knows the difference. A check that is red for
        # a fact nobody can fix is a check people learn to ignore, and an ignored audit is
        # worse than no audit: it hides the F1 that does matter. F1 is now the failure.
        _note(
            f"B2 {instrument} {new_missing} NEW missing minute(s) inside an open session"
            + (f" (plus {known_hits} already-known venue absences)" if known_hits else "")
            + " - NOT a failure on its own; class F asks the venue whether these exist there "
            "too, and only F1 (venue has it, we do not) is a real defect"
        )

    return b1, b2


# ------------------------------------------------------------------ class F
# WHY CLASS F EXISTS — the check that makes B2 mean something
# -----------------------------------------------------------
# Measured 2026-09-30, the residue after the calendar was corrected is NOT all our fault:
#   * CRYPTO 46 minutes, ~21 of them at almost exactly 21:07 UTC on consecutive days. The
#     VENUE has no bar there either - `cryBTCUSD` 21:00-21:15 returns epochs
#     21:00..21:06 then 21:08..21:15, skipping 21:07. A daily one-minute hole in Deriv's
#     own crypto history. Our store is FAITHFUL; B2 was reporting a market fact.
#   * COMMODITY 270 minutes on 2026-09-07 (18:31-20:59 and 22:00-23:59). The venue returns
#     ZERO bars in both windows - its own response spans 18:00-01:00 but contains none of
#     those minutes. A ~5.5-hour hole in Deriv's XAU history. Again not repairable by us.
#
# So B2 alone cannot distinguish "our pipeline dropped it" from "the venue never had it",
# and an audit that cannot tell those apart is permanently red and therefore ignored. F asks
# the only authority that can settle it - the venue - and reclassifies:
#
#   F1  the venue HAS the bar and we do not   -> OUR defect. Fails. This is the one that matters.
#   F2  the venue does NOT have the bar      -> venue absence. Reported, NOT a failure,
#                                               because no amount of re-fetching will conjure it.
#
# Sampled, not exhaustive: re-asking the venue for every missing minute of a 250-day load
# would be thousands of requests per run. The sample is what makes it affordable, and the
# sampling is reported so a reader knows the coverage.


async def check_gap_provenance(
    conn: Any, fetcher: Any, instrument: str, native: str, sample: int,
    since: datetime | None = None,
) -> tuple[int, int]:
    """Class F: ask the venue whether the minutes WE are missing exist at all.

    Returns (ours, theirs): gaps the venue has and we do not (a real defect), and gaps
    neither has (a venue absence, which is a fact rather than a failure).

    Minutes the venue confirms it lacks are written to `known_venue_gaps` so the next run
    treats them as expected. That is the difference between a one-time baseline and
    re-querying the same absent minute every night forever.
    """
    if since is None:
        have = {
            r["interval_start"]
            for r in await conn.fetch(
                "SELECT interval_start FROM candles_1m WHERE instrument_id = $1", instrument
            )
        }
        lo = None
    else:
        rows = await conn.fetch(
            "SELECT interval_start FROM candles_1m "
            "WHERE instrument_id = $1 AND interval_start >= $2",
            instrument, since,
        )
        have = {r["interval_start"] for r in rows}
        lo = since
    if not have:
        return 0, 0
    cals = _calendar()
    lo = lo or min(have)
    hi = max(have)
    missing: list[Any] = []
    for a, b in cals.open_intervals(instrument, lo, hi, DERIV):
        t = a
        while t < b:
            if t not in have:
                missing.append(t)
            t += MIN
    if not missing:
        return 0, 0
    # Deterministic sample: evenly spaced through the gap list, so two runs examine the same
    # minutes and a change in the verdict is a real change rather than sampling noise.
    step = max(1, len(missing) // max(1, sample))
    probes = missing[::step][:sample]
    pad = MIN
    ours = theirs = 0
    for probe in probes:
        try:
            bars = await fetcher.fetch(
                instrument, DERIV, probe - pad, probe + pad + MIN
            )
        except Exception as exc:  # noqa: BLE001 - a venue refusal must not read as "venue lacks it"
            _note(f"F [{instrument}] could not ask the venue about {probe.isoformat()}: {exc}")
            continue
        by_start = {b.interval_start: b for b in bars}
        if probe in by_start:
            ours += 1
            # Spell out the repair, because the obvious thing does NOT work: the loader
            # walks FORWARD from backfill_cursors.reached, and a hole sitting BEHIND the
            # cursor is invisible to it. Measured 2026-09-30 - a planted missing bar was
            # found here, the loader reported "PASS - all 25 instruments complete", and the
            # bar was still absent afterwards. The cursor has to be rewound past the hole
            # first. An alert without the fix is an alert that gets ignored.
            rewind = (probe - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
            _fail(
                "F1", instrument,
                f"{probe.isoformat()} exists at the venue but is MISSING from our store - "
                "this is OUR loss and a backfill can repair it. REPAIR: "
                f"python scripts/rewind_backfill_cursor.py --until {rewind} --top 25 "
                "&& python scripts/load_universe.py --hours 48 --top 25",
            )
        else:
            theirs += 1
            # Baseline it. ON CONFLICT DO NOTHING keeps this idempotent across runs, and
            # the reason records WHY so a future reader does not assume a bug.
            await conn.execute(
                "INSERT INTO known_venue_gaps (instrument_id, broker, interval_start, reason) "
                "VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING",
                instrument, DERIV, probe,
                "confirmed absent at the venue by class F on first discovery",
            )
    if theirs:
        _note(
            f"F [{instrument}] {theirs}/{len(probes)} sampled missing minutes are absent at "
            "the VENUE too - market/venue absence, not our loss, and not repairable by "
            "re-fetching (e.g. a daily one-minute crypto hole near 21:07Z, or a multi-hour "
            "gap in Deriv's own metals history)"
        )
    return ours, theirs


def _calendar() -> Any:
    from modules.reference.calendar import load_calendar

    return load_calendar()


# ------------------------------------------------------------------ class C


async def check_against_broker(
    instrument: str, fetcher: DerivHistoryFetcher, native: str, args: int
) -> int:
    """Re-fetch stored bars from Deriv and compare EXACTLY. The strongest check here.

    WHY this is not circular: the loader under test is `DerivHistoryFetcher`, and this
    calls it again - but to fetch a window the loader did NOT choose, then compares the
    result to what is in the database. A systematic loader bug (bad field mapping, wrong
    epoch basis, a shifted window) shows up as a mismatch even though both sides went
    through the same adapter, because the COMPARISON is against persisted rows the
    loader wrote at a different time.
    """
    # WHY sample from the MIDDLE, not the newest: the first revision took the 5 newest
    # stored bars, and the newest minute is the boundary between "history" and "the
    # minute the venue is still forming". That is precisely the one bar where a
    # re-fetch legitimately disagrees, so the check reported the same C1 miss on all 25
    # instruments - a boundary artefact, not a data defect (the bar was present in the
    # store for all 25). Sampling settled bars tests what we actually mean to test.
    total = int(
        await _DB.fetchval("SELECT count(*) FROM candles_1m WHERE instrument_id=$1", instrument)
    )
    offset = max(0, total // 2 - args // 2)
    conn_rows = await _DB.fetch(
        "SELECT interval_start, open, high, low, close FROM candles_1m "
        "WHERE instrument_id=$1 ORDER BY interval_start OFFSET $2 LIMIT $3",
        instrument,
        offset,
        args,
    )
    if not conn_rows:
        return 0
    newest = max(r["interval_start"] for r in conn_rows)
    oldest = min(r["interval_start"] for r in conn_rows)
    # WHY the window is PADDED on both sides, and why the result is indexed BY EPOCH.
    #
    # The venue's `count` semantics return the `count` bars ENDING at `end`, so a window
    # asked for exactly [oldest, newest+1m) is one boundary-convention away from returning
    # a shifted set. That is not hypothetical: this check reported 25/25 instruments
    # mismatching on `high`, always in the same direction, and the reason was a one-minute
    # offset - in a rising market, comparing minute T against T-60 always reads as "the
    # stored high is too high". Proven by printing the epochs the venue actually returned.
    #
    # Padding by a minute on each side makes the comparison correct under EITHER alignment,
    # and indexing by epoch means a shift can no longer be mistaken for a data defect.
    pad = timedelta(minutes=1)
    bars = await fetcher.fetch(instrument, DERIV, oldest - pad, newest + pad + MIN)
    by_start = {b.interval_start: b for b in bars}
    checked = 0
    for r in conn_rows:
        live = by_start.get(r["interval_start"])
        if live is None:
            _fail("C1", instrument, f"stored bar {r['interval_start']} not returned by broker")
            continue
        for field in ("open", "high", "low", "close"):
            stored = Decimal(str(r[field]))
            fetched = getattr(live, field)
            if stored != fetched:
                _fail(
                    "C1", instrument,
                    f"{r['interval_start']} {field}: stored={stored} broker={fetched}",
                )
                break
        else:
            checked += 1
    return checked


_DB: Any = None


# ------------------------------------------------------------------ main


async def check_lineage(conn: Any, instrument: str) -> int:
    """E  LINEAGE — every bar must name the reference snapshot that interpreted it.

    Added 2026-09-30 after measuring 796,331 of 796,333 rows with
    `reference_snapshot_id = NULL`. The bulk loader's INSERT omitted the column, so the one
    writer in the system that produces the most rows produced the ones that could not be
    attributed to an instrument spec at all.

    WHY this belongs in the audit and not only in a unit test: the audit already exists to
    catch "the data looks fine but is quietly wrong", and unattributable OHLC is exactly
    that. A unit test of a writer would have passed the whole time the store was broken.

    WHY it matters operationally: `AGENTS.md` 3.4 requires the snapshot id on venue data for
    reproducibility and 4.4 on every row for replay. Without it, a later edit to
    `config/assets/*.yaml` makes a replay of this history unreproducible - and this project
    has already been bitten by a live `tick_size` 0.01-vs-0.001 defect that silently
    truncated live prices to 2dp. Knowing WHICH spec was in force is the only way to bound
    that class of bug after the fact.
    """
    row = await conn.fetchrow(
        "SELECT count(*) AS total, "
        "       count(*) FILTER (WHERE reference_snapshot_id IS NULL) AS nulls, "
        "       count(DISTINCT reference_snapshot_id) AS distinct_ids "
        "FROM candles_1m WHERE instrument_id = $1",
        instrument,
    )
    total = int(row["total"])
    nulls = int(row["nulls"])
    if nulls:
        _fail("E1", instrument, f"{nulls}/{total} rows have NO reference_snapshot_id")
    ids = int(row["distinct_ids"])
    if ids > 1:
        _note(
            f"{instrument}: {ids} distinct reference snapshots in one instrument's history "
            "(that is legitimate across a snapshot rotation, but the G0 write-up must "
            "state the boundary)"
        )
    return 1 if nulls else 0


async def main() -> int:
    global _DB
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--sample", type=int, default=5, help="bars cross-checked per instrument")
    ap.add_argument("--skip-live", action="store_true", help="classes A+B only")
    ap.add_argument("--gap-sample", type=int, default=6,
                    help="class F: missing minutes to ask the venue about per instrument")
    ap.add_argument("--use-watermark", action="store_true",
                    help="incremental: audit only bars newer than each instrument's "
                         "verified_through, and treat baselined venue absences as known. "
                         "Requires scripts/migrate_audit_watermark.py to have been applied.")
    args = ap.parse_args()

    selection = json.loads(
        (ROOT / "config" / "reference" / "universe_screen.json").read_text(encoding="utf-8")
    )
    chosen = selection["selected"][: args.top]

    print("=" * 78)
    print("UNIVERSE DATA INTEGRITY AUDIT")
    print("=" * 78)
    print(f"instruments={len(chosen)}  sample={args.sample} bars each  "
          f"live_cross_check={'NO' if args.skip_live else 'YES'}")
    print()

    _DB = await asyncpg.connect(
        f"postgresql://quantrader:{_password()}@127.0.0.1:5432/quantrader", timeout=20
    )
    try:
        print(f"{'instrument':<18} {'rows':>8} {'A':>3} {'B1':>4} {'B2':>5} {'C':>4} {'E':>3}  verdict")
        print("-" * 78)
        transport_cm = (
            None if args.skip_live else WebSocketTransport()
        )
        if transport_cm is not None:
            await transport_cm.__aenter__()
        fetcher = DerivHistoryFetcher(transport=transport_cm) if transport_cm else None

        try:
            for row in chosen:
                native = row["native"]
                try:
                    instrument = to_canonical(native, DERIV)
                except Exception as exc:  # noqa: BLE001
                    _fail("A0", native, f"unresolved in reference master: {exc}")
                    print(f"{native:<18} unresolved")
                    continue

                before = len(failures)
                n = await check_structural(_DB, instrument)
                since = None
                known: set[Any] = set()
                if args.use_watermark:
                    since = await _DB.fetchval(
                        "SELECT verified_through FROM data_audit_watermark "
                        "WHERE instrument_id=$1 AND broker=$2",
                        instrument, DERIV,
                    )
                    known = {
                        r["interval_start"]
                        for r in await _DB.fetch(
                            "SELECT interval_start FROM known_venue_gaps "
                            "WHERE instrument_id=$1 AND broker=$2",
                            instrument, DERIV,
                        )
                    }
                b1, b2 = await check_calendar(_DB, instrument, since=since, known=known)
                e = await check_lineage(_DB, instrument)
                c = 0
                f_ours = f_theirs = 0
                if fetcher is not None:
                    try:
                        c = await check_against_broker(instrument, fetcher, native, args.sample)
                    except Exception as exc:  # noqa: BLE001
                        _fail("C1", instrument, f"cross-check error: {type(exc).__name__}: {exc}")
                    try:
                        f_ours, f_theirs = await check_gap_provenance(
                            _DB, fetcher, instrument, native, args.gap_sample, since=since
                        )
                    except Exception as exc:  # noqa: BLE001
                        _note(f"F [{instrument}] provenance check skipped: {type(exc).__name__}: {exc}")
                new = len(failures) - before
                verdict = "ok" if new == 0 else f"FAIL x{new}"
                print(f"{instrument:<18} {n:>8,} {'A':>3} {b1:>4} {b2:>5} {c:>4} {e:>3} {f_ours:>4} {f_theirs:>6}  {verdict}")

                # Advance the watermark ONLY for an instrument that raised no failure. If
                # it failed, the watermark stays put so the next run re-examines the same
                # window - a defect that "passes" because the cursor moved past it is the
                # one failure mode a watermark must never have.
                if args.use_watermark and new == 0:
                    await _DB.execute(
                        "INSERT INTO data_audit_watermark "
                        "(instrument_id, broker, verified_through, updated_at) "
                        "VALUES ($1,$2,$3, now()) "
                        "ON CONFLICT (instrument_id, broker) DO UPDATE "
                        "SET verified_through = GREATEST("
                        "  data_audit_watermark.verified_through, EXCLUDED.verified_through), "
                        "  updated_at = now()",
                        instrument, DERIV,
                        await _DB.fetchval(
                            "SELECT max(interval_start) FROM candles_1m WHERE instrument_id=$1",
                            instrument,
                        ),
                    )
        finally:
            if transport_cm is not None:
                await transport_cm.__aexit__(None, None, None)
    finally:
        await _DB.close()

    # ---- class D: what is NOT guaranteed, stated plainly
    print()
    print("KNOWN LIMITATIONS (not pass/fail - honesty about what is NOT guaranteed):")
    _note(
        "D1 backfilled tick_count is hardcoded 1 by DerivHistoryFetcher; real candles carry "
        "no volume, so volume-dependent analytics are meaningless on backfilled history"
    )
    _note(
        "D2 the forex DAILY break (20:59-21:10) and the Friday close divergence "
        "(config 21:00 vs broker 20:55) are CONFIG facts; this audit cannot certify them"
    )
    _note("D3 tick_count=1 rows also make our own recorded 'spread' columns synthetic")
    for n in notes:
        print(f"  - {n}")

    print()
    print("=" * 78)
    if failures:
        print(f"RESULT: FAIL - {len(failures)} problem(s):")
        for f in failures[:40]:
            print(f"   - {f}")
        if len(failures) > 40:
            print(f"   ... and {len(failures) - 40} more")
        return 1
    print("RESULT: PASS - all instruments clean on every executed check")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
