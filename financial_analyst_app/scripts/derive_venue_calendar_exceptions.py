"""Derive calendar EXCEPTIONS (holidays + dated early closes) from the venue.

WHY THIS IS A SCRIPT AND NOT A HAND-WRITTEN LIST
-----------------------------------------------
`trading_times` is auth-free and publishes, per symbol per date: `times`, `trading_days`,
and an `events` array. A hand-maintained holiday list is a liability - it goes stale the
moment the venue publishes next year's dates, and nothing notices. Deriving it means the
list is as current as the last run, and re-running it is how you find out it changed.

Measured event shapes (2026-09-30, live socket, 25 instruments):
    {"dates": "Fridays",                       "descrip": "Closes early (at 20:55)"}
    {"dates": "2026-12-25",                    "descrip": "Christmas Day"}
    {"dates": "2026-11-26, 2027-01-18, 2027-02-15", "descrip": "Closes early (at 16:30)"}
    {"dates": "2027-03-26",                    "descrip": "Good Friday"}
So `dates` is EITHER a recurring weekday word OR a comma-separated list of ISO dates, and
`descrip` distinguishes a full closure from an early close with a stated time.

WHY THE PER-CLASS SPLIT IS THE WHOLE POINT
-------------------------------------------
Good Friday 2027-03-26 closes METALS but not FOREX. A single global holiday list would be
wrong for one of them. The same applies to the 16:30 early closes - they are metals-only.
This script therefore reports per class, never merged.

READ-ONLY. Talks to the public socket only; writes nothing to the database.

Usage:
  python scripts/derive_venue_calendar_exceptions.py --days 250
  python scripts/derive_venue_calendar_exceptions.py --days 250 --emit-yaml
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WS = "wss://api.derivws.com/trading/v1/options/ws/public"
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EARLY_AT = re.compile(r"\(?at\s+(\d{1,2}):(\d{2})\)?")
#: The `events[].dates` vocabulary is PLURAL and abbreviated ("Fridays", "Thurs"), while the
#: `trading_days` enum in the schema is singular and full ("Sun".."Sat"). Two revisions of this
#: script got it wrong in opposite directions - first comparing events against the enum (missing
#: every rule), then enumerating plurals and still missing "fridays". Enumerating a language is
#: the wrong shape of solution, so a trailing "s" is stripped and a small stem set is used.
#: The false "unparsed" warning this produced mattered more than the missing rows: a warning
#: that cries wolf is how a reader learns to ignore the one signal meant to catch a genuinely
#: unrecognised token.
_WEEKDAY_STEMS = {
    "sun", "sunday", "mon", "monday", "tue", "tues", "tuesday",
    "wed", "weds", "wednesday", "thu", "thur", "thurs", "thursday",
    "fri", "friday", "sat", "saturday",
}


def is_weekday_word(token: str) -> bool:
    t = token.strip().lower()
    return t in _WEEKDAY_STEMS or (t.endswith("s") and t[:-1] in _WEEKDAY_STEMS)

#: our canonical class per native prefix, so the output lands in the same vocabulary the
#: asset_classes.yaml already uses.
CLASS_OF_PREFIX = {"frx": None, "cry": "crypto"}  # resolved per symbol below


def minute_of_day(hh: str, mm: str) -> int:
    return int(hh) * 60 + int(mm)


async def fetch_day(ws: object, iso: str, rid: int) -> dict:
    await ws.send(json.dumps({"trading_times": iso, "req_id": rid}))
    while True:
        frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=40))
        if frame.get("req_id") == rid:
            if "error" in frame:
                raise RuntimeError(f"{iso}: {frame['error']}")
            return frame.get("trading_times") or {}


def walk_symbols(tree: object) -> list[dict]:
    """The documented hierarchy is Market -> SubMarket -> symbols[]; walk it defensively
    rather than trusting depth, because a level added server-side would otherwise silently
    yield ZERO symbols and a clean-looking empty report."""
    out: list[dict] = []

    def node(n: object) -> None:
        if isinstance(n, dict):
            if "underlying_symbol" in n:
                out.append(n)
            for v in n.values():
                node(v)
        elif isinstance(n, list):
            for v in n:
                node(v)

    node(tree)
    return out


def classify(native: str) -> str:
    if native.startswith("cry"):
        return "crypto"
    if native.startswith("frxXAU") or native.startswith("frxXAG"):
        return "commodity"
    if native.startswith("frx"):
        return "forex"
    return "unknown"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=250, help="window from today")
    ap.add_argument("--emit-yaml", action="store_true", help="print config-ready YAML")
    args = ap.parse_args()

    from modules.reference.reference import to_canonical

    selection = json.loads(
        (ROOT / "config" / "reference" / "universe_screen.json").read_text(encoding="utf-8")
    )
    wanted: set[str] = set()
    for row in selection["selected"]:
        native = row.get("native")
        if isinstance(native, str) and native:
            try:
                to_canonical(native, "deriv")
            except Exception:  # noqa: BLE001 - unresolved symbols are simply not watched
                continue
            wanted.add(native)

    start = date.today()
    end = start + timedelta(days=args.days)
    print("=" * 78)
    print(f"DERIVING VENUE CALENDAR EXCEPTIONS  {start} -> {end}  ({args.days} days)")
    print(f"{len(wanted)} instruments watched; 1 request per date; read-only")
    print("=" * 78)

    # class -> {iso date -> {closure|early minute}}
    closures: dict[str, set[str]] = defaultdict(set)
    early: dict[str, dict[str, int]] = defaultdict(dict)
    # class -> recurring weekday words, reported but NOT emitted (a recurring early close is
    # already expressed by close_weekday/close_minute in the weekly window)
    recurring: dict[str, set[str]] = defaultdict(set)
    #: anything whose `dates` token matched neither an ISO date nor a weekday word. Surfaced
    #: rather than dropped, because a silently-ignored token is a silently-missing holiday.
    unparsed: dict[str, set[str]] = defaultdict(set)
    scanned = 0
    failed: list[str] = []

    async with websockets.connect(WS, open_timeout=30) as ws:
        rid = 1000
        d = start
        while d <= end:
            rid += 1
            try:
                tree = await fetch_day(ws, d.isoformat(), rid)
            except Exception as exc:  # noqa: BLE001 - one bad date must not lose the sweep
                failed.append(f"{d.isoformat()}: {type(exc).__name__}: {exc}")
                d += timedelta(days=1)
                continue
            scanned += 1
            for sym in walk_symbols(tree):
                native = sym.get("underlying_symbol")
                if native not in wanted:
                    continue
                cls = classify(native)
                for ev in sym.get("events") or []:
                    if not isinstance(ev, dict):
                        continue
                    raw_dates = str(ev.get("dates") or "").strip()
                    descrip = str(ev.get("descrip") or "").strip()
                    if not raw_dates:
                        continue
                    # WHY THE SPLIT IS LOAD-BEARING: the venue packs several dates into ONE
                    # `dates` string - e.g. "2026-11-26, 2027-01-18, 2027-02-15" all carrying
                    # "Closes early (at 16:30)". An earlier revision tested the WHOLE string
                    # against the ISO-date pattern, so the multi-date form failed to match and
                    # was silently misfiled as a recurring rule - which is how the first run
                    # reported 1 metals early close when the venue lists 3. The venue narrows
                    # `events` to the date being queried, so a single-date string is also seen;
                    # both shapes must be handled.
                    for token in (t.strip() for t in raw_dates.split(",")):
                        if not token:
                            continue
                        if not ISO_DATE.match(token):
                            if is_weekday_word(token):
                                recurring[cls].add(f"{token}: {descrip}")
                            else:
                                unparsed[cls].add(f"{token!r} in {raw_dates!r} ({descrip})")
                            continue
                        m = EARLY_AT.search(descrip)
                        if m:
                            early[cls][token] = minute_of_day(m.group(1), m.group(2))
                        else:
                            closures[cls].add(token)
            d += timedelta(days=1)
            if scanned % 25 == 0:
                print(f"   ...{scanned} dates scanned")

    print()
    print(f"dates scanned: {scanned}   failed: {len(failed)}")
    for f in failed[:5]:
        print(f"   {f}")
    print()
    # every class is printed, INCLUDING ones with nothing, so an empty class is a reported
    # absence rather than a missing section.
    for cls in ("forex", "commodity", "crypto"):
        print("=" * 78)
        print(f"CLASS {cls.upper()}")
        print("=" * 78)
        hol = sorted(closures.get(cls, ()))
        ec = sorted((early.get(cls) or {}).items())
        print(f"  full closures      {len(hol)}")
        for h in hol:
            d = date.fromisoformat(h)
            print(f"     {h}  {d.strftime('%a')}  {d}")
        print(f"  dated early closes {len(ec)}")
        for k, v in ec:
            d = date.fromisoformat(k)
            print(f"     {k}  {d.strftime('%a')}  closes {v // 60:02d}:{v % 60:02d} UTC")
        rec = sorted(recurring.get(cls, ()))
        if rec:
            print("  recurring rules (already expressed by the weekly window; not emitted):")
            for r in rec:
                print(f"     {r}")
        bad = sorted(unparsed.get(cls, ()))
        if bad:
            print("  !! UNPARSED tokens - these are NOT in the config and must be read:")
            for b in bad:
                print(f"     {b}")
        if not hol and not ec and not rec and not bad:
            print("  (no events at all - this class trades every published day, "
                  "including the closures the others observe)")

    if args.emit_yaml:
        print()
        print("=" * 78)
        print("CONFIG FRAGMENT  (merge into config/reference/asset_classes.yaml)")
        print("=" * 78)
        for cls in ("forex", "commodity", "crypto"):
            hol = sorted(closures.get(cls, ()))
            ec = dict(sorted((early.get(cls) or {}).items()))
            print(f"{cls}:")
            if hol:
                print("  holidays:")
                for h in hol:
                    print(f'    - "{h}"')
            else:
                print("  holidays: []")
            if ec:
                print("  early_closes:")
                for k, v in ec.items():
                    print(f'    "{k}": {v}   # {v // 60:02d}:{v % 60:02d} UTC')
            else:
                print("  early_closes: {}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
