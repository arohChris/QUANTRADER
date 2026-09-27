"""Step 7 real-data verification for M2b.6 (gap calendar) — against the REAL store, not fixtures.

WHY this exists (AGENTS.md 2 Step 7): a sub-module is not verified until it has been run
against real data and a human has looked at the output. Unit tests prove the logic; only this
proves the logic is wired to the data it will actually meet.

The subject is deliberately the WORST case in the database: a 341-minute hole in
`candles_1m` for CRYPTO.BTCUSD measured live on 2026-09-27. A 24/7 asset with a real,
contiguous, multi-hour gap is the exact case where "is this lost data or a shut market?"
matters, and where a naive detector would loop forever.

What this asserts, each with a printed value a human can check:
  1. The real hole is detected, as ONE contiguous run (not 341 separate gaps).
  2. Its state is FILLING, not CLOSED - the market was open, so this is real data loss.
  3. Every minute of the hole lies inside broker-tradable hours (24/7 asset).
  4. A REAL FX weekend window in the same store produces ZERO gap minutes - the CLOSED state
     working, and the reason this slice exists.
  5. Coverage verdict fails closed: `can_compute` is False across the hole.
  6. Re-running is idempotent: identical classification both passes.

Secrets: the Timescale password is read from REPO/.env and never printed (AGENTS.md 3.7).
Run:  python scripts/verify_m2b.6_coverage.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.m2_market_data.coverage import (
    GapState,
    classify_window,
    coverage_of,
)

REPO = Path(__file__).resolve().parents[2]
_MINUTE = timedelta(minutes=1)


def _password() -> str:
    env = REPO / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("TIMESCALE_DEV_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"TIMESCALE_DEV_PASSWORD missing from {env}")


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")
    return ok


def _weekend_is_closed(lo: datetime, hi: datetime) -> bool:
    """A real FX weekend closure must yield ZERO gap minutes.

    This is the property that justifies the whole slice: a naive detector reports market
    closure as lost data, and a 25-asset universe would then queue thousands of phantom
    repairs every weekend, forever.

    WHY align to midnight: the window must END on the exclusive Sunday-open boundary. A
    first version started at the store's first timestamp and added 45 hours, running 65
    minutes PAST the Sunday open - those 65 minutes are genuinely tradable, so a non-zero
    gap count was the CORRECT answer and the "expect zero" assertion was the bug.
    """
    sat = lo
    while sat.weekday() != 5:
        sat += timedelta(days=1)
    sat = sat.replace(hour=0, minute=0, second=0, microsecond=0)
    if sat + timedelta(hours=45) > hi:
        sat -= timedelta(days=7)
    rows = classify_window(
        "FX.EURUSD", "deriv", sat, sat + timedelta(hours=45),
        class_resolver=lambda i: {"asset_class": "forex"},
    )
    gap = sum(r.minutes() for r in rows if r.state is GapState.FILLING)
    closed = sum(r.minutes() for r in rows if r.state is GapState.CLOSED)
    return _check(
        "4. a real FX weekend CLOSURE yields ZERO gap minutes (CLOSED works)",
        gap == 0 and closed > 0,
        f"{sat.isoformat()} +45h -> gap={gap}min closed={closed}min "
        f"(sum={gap + closed}min, expect 2700)",
    )


async def _fetch(conn: asyncpg.Connection, sql: str) -> list[asyncpg.Record]:
    return list(await conn.fetch(sql))


async def main() -> int:
    pw = _password()
    # Role/database shape taken from scripts/verify_m2.10_warm.py (the working precedent) -
    # NOT guessed. A first attempt used postgres/quant and failed auth.
    dsn = f"postgresql://quantrader:{pw}@127.0.0.1:5432/quantrader"
    print("target: postgresql://quantrader:***@127.0.0.1:5432/quantrader "
          "(password never printed)")
    conn = await asyncpg.connect(dsn, timeout=10)
    results: list[bool] = []
    try:
        summary = (await _fetch(
            conn,
            "SELECT min(interval_start) lo, max(interval_start) hi, count(*) n, "
            "count(DISTINCT instrument_id) insts FROM candles_1m",
        ))[0]
        print("=" * 78)
        print("M2b.6 STEP 7 - gap classification against the REAL store")
        print("=" * 78)
        print(f"  candles_1m: n={summary['n']}  instruments={summary['insts']}")
        print(f"  range     : {summary['lo']} -> {summary['hi']}")
        lo, hi = summary["lo"], summary["hi"]
        if lo is None or hi is None or hi - lo < timedelta(minutes=2):
            print("  [SKIP] store has too little data to verify against")
            return 0

        # --- find the real gaps, using generate_series (never a per-minute Python walk) ---
        gap_rows = await _fetch(
            conn,
            """
            WITH span AS (SELECT min(interval_start) lo, max(interval_start) hi FROM candles_1m),
                 g AS (SELECT generate_series(lo, hi - interval '1 minute',
                                             interval '1 minute') AS t FROM span),
                 m AS (SELECT interval_start FROM candles_1m)
            SELECT d.t AS minute
            FROM g d
            WHERE NOT EXISTS (SELECT 1 FROM m WHERE interval_start = d.t)
            ORDER BY d.t
            """,
        )
        instrument = (await _fetch(
            conn, "SELECT DISTINCT instrument_id FROM candles_1m LIMIT 1"
        ))[0]["instrument_id"]

        minutes = [r["minute"] for r in gap_rows]
        # group into contiguous runs
        runs: list[list[datetime]] = []
        for m in minutes:
            if runs and (m - runs[-1][-1]) == _MINUTE:
                runs[-1].append(m)
            else:
                runs.append([m])

        if not runs:
            # A WHOLE store is the success state, and the verifier must handle it. An earlier
            # revision called max() on the empty list here and crashed - so the one condition
            # we most want to confirm could not be checked at all.
            print("\n  REAL DEFECT: none. The store has ZERO missing minutes.")
            results.append(_check(
                "1. the real store has no missing minutes at all",
                True, f"{summary['n']} rows, {lo.isoformat()} -> {hi.isoformat()}",
            ))
            verdict = coverage_of(
                instrument, "deriv", lo, hi,
                present=lambda a, b: [a],
                missing=lambda a, b: [],
                class_resolver=lambda i: {"asset_class": "crypto"},
            )
            results.append(_check(
                "5. coverage over the whole stored span is COMPLETE (can_compute True)",
                verdict.can_compute, f"state={verdict.state.value}",
            ))
            fx_ok = _weekend_is_closed(lo, hi)
            results.append(fx_ok)
            failed = results.count(False)
            print("\n" + "=" * 78)
            print(f"  {len(results) - failed}/{len(results)} checks passed"
                  + (f"  ({failed} FAILED)" if failed else ""))
            print("=" * 78)
            return 1 if failed else 0

        longest = max(runs, key=len)
        gap_start, gap_end = longest[0], longest[-1] + _MINUTE
        print(f"\n  REAL DEFECT: {len(minutes)} missing minutes in {len(runs)} run(s); "
              f"longest = {len(longest)} min")
        print(f"    window: {gap_start.isoformat()} -> {gap_end.isoformat()}")

        # --- 1 & 2: one contiguous run, state FILLING, not CLOSED -----------------------
        window = (gap_start, gap_end + timedelta(hours=2))
        missing = [(gap_start, gap_end)]
        rows = classify_window(
            instrument, "deriv", *window,
            missing=lambda a, b: missing,
            class_resolver=lambda i: {"asset_class": "crypto"},
        )
        filling = [r for r in rows if r.state is GapState.FILLING]
        covering = [r for r in filling if r.start <= gap_start and r.end >= gap_end]
        results.append(_check(
            "1. the real hole is ONE contiguous FILLING run",
            len(covering) == 1,
            f"{len(filling)} filling run(s); covering run = "
            f"{covering[0].start.isoformat()} -> {covering[0].end.isoformat()}"
            if covering else "no run covers the hole",
        ))
        results.append(_check(
            "2. state is FILLING (real data loss), NOT CLOSED",
            bool(covering) and covering[0].state is GapState.FILLING
            and not any(r.state is GapState.CLOSED for r in filling),
            f"covered minutes = {covering[0].minutes() if covering else 0}",
        ))
        results.append(_check(
            "3. no CLOSED run inside a 24/7 asset's hole",
            not any(r.state is GapState.CLOSED and r.minutes() > 0 for r in rows),
            f"closed minutes = {sum(r.minutes() for r in rows if r.state is GapState.CLOSED)}",
        ))

        # --- 5: coverage fails closed across the hole -------------------------------------
        verdict = coverage_of(
            instrument, "deriv", gap_start, gap_end,
            present=lambda a, b: [],
            missing=lambda a, b: missing,
            class_resolver=lambda i: {"asset_class": "crypto"},
        )
        results.append(_check(
            "5. coverage fails CLOSED across the hole (can_compute False)",
            verdict.state is GapState.FILLING and not verdict.can_compute,
            f"state={verdict.state.value}  can_compute={verdict.can_compute}",
        ))

        # --- 4: the CLOSED state, on a real FX weekend window ----------------------------
        # Find the most recent Saturday in the data span and classify the FX CLOSURE period
        # for EURUSD: Sat 00:00 -> Sun 21:00, exactly 45h, ending on the Sunday open boundary
        # (which is exclusive).
        #
        # WHY align to midnight: a first version of this check used the store's first
        # timestamp (01:05) as the start and added 45 hours, which pushed the end 65 minutes
        # PAST the Sunday open. Those 65 minutes are genuinely tradable and we hold no EURUSD
        # data, so 65 gap minutes was the CORRECT answer and the "expect zero" assertion was
        # the bug. Same class of error as an earlier property test that ran 2d21h into Monday.
        sat = lo
        while sat.weekday() != 5:
            sat += timedelta(days=1)
        sat = sat.replace(hour=0, minute=0, second=0, microsecond=0)
        if sat + timedelta(hours=45) > hi:
            sat -= timedelta(days=7)
        fx_rows = classify_window(
            "FX.EURUSD", "deriv", sat, sat + timedelta(hours=45),
            class_resolver=lambda i: {"asset_class": "forex"},
        )
        fx_gap = sum(r.minutes() for r in fx_rows if r.state is GapState.FILLING)
        fx_closed = sum(r.minutes() for r in fx_rows if r.state is GapState.CLOSED)
        results.append(_check(
            "4. the real FX weekend CLOSURE yields ZERO gap minutes (CLOSED works)",
            fx_gap == 0 and fx_closed > 0,
            f"{sat.isoformat()} +45h -> gap={fx_gap}min closed={fx_closed}min "
            f"(sum={fx_gap + fx_closed}min, expect 2700)",
        ))

        # --- 6: idempotent re-run ---------------------------------------------------------
        again = classify_window(
            instrument, "deriv", *window,
            missing=lambda a, b: missing,
            class_resolver=lambda i: {"asset_class": "crypto"},
        )
        results.append(_check(
            "6. re-classification is idempotent",
            [(r.start, r.end, r.state) for r in rows]
            == [(r.start, r.end, r.state) for r in again],
        ))

        # --- evidence table a human can eyeball -------------------------------------------
        print("\n  EVIDENCE (first 5 gap minutes and the covering run):")
        for m in minutes[:5]:
            print(f"    missing minute  {m.isoformat()}")
        if covering:
            r0 = covering[0]
            print(f"    covering run    {r0.start.isoformat()} -> {r0.end.isoformat()}"
                  f"  state={r0.state.value}  evidence={r0.evidence.value}"
                  f"  needs_verification={r0.needs_verification}")
        print("\n  NOTE: the covering run carries evidence=calendar, not broker - the broker")
        print("  was not consulted in this offline run. Per spec 3a that marks it")
        print("  needs_verification=True, which is the correct conservative outcome.")
    finally:
        await conn.close()

    failed = results.count(False)
    print("\n" + "=" * 78)
    print(f"  {len(results) - failed}/{len(results)} checks passed")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
