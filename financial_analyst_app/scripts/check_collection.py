"""D13 guard: the test suite must be COLLECTABLE, and any blocker must be owned and dated.

WHY this exists: a missing module made `pytest` unable to collect the repository, so the
full suite could not be run at all. A collection error is worse than a test failure: it hides
every other result, and it is easy to leave in place for weeks while everyone quotes a
qualified test count.

The failure mode this is built to prevent is the dangerous one: an UNREGISTERED blocker being
quietly ignored so the suite looks green while describing less than it appears to. So:

  * An unlisted collection error            -> FAIL. New breakage is never masked.
  * A listed blocker, within its review date -> WARN loudly, naming owner and how to clear.
  * A listed blocker PAST its review date   -> FAIL. A blocker may not quietly become permanent.
  * A listed blocker whose file now imports -> FAIL. The exemption outlived its cause, and
    leaving it would let a real regression hide behind a stale entry.

Run:  python scripts/check_collection.py
Exit: 0 = collectable (possibly with a warned, owned blocker); 1 = not acceptable.
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTER = ROOT / "config" / "known_collection_blockers.yaml"

# Collection error text -> the file pytest blamed, when it can be attributed to one file.
_REQUIRED_KEYS = ("file", "owner", "why", "how_to_clear", "raised_on", "review_by")


def _collect() -> tuple[int, list[str]]:
    """Collect WITHOUT the registered ignores, so the TRUE state is observed.

    WHY `-o addopts=...` and not a bare run: `pyproject.toml` carries an --ignore for the
    registered blocker so day-to-day work can continue. If this guard inherited it, the guard
    would see a clean collect, conclude the blocker was fixed, and tell the human to delete
    the register entry - while the module still does not exist. That is precisely the
    'works, and is quietly wrong' failure this script exists to prevent, so the guard must
    look past the exemption rather than through it.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-o", "addopts=--import-mode=importlib"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=300,
    )
    errors = [
        line.strip() for line in (proc.stdout + proc.stderr).splitlines()
        if line.startswith(("ERROR ", "E   ModuleNotFoundError"))
    ]
    return proc.returncode, errors


def _blamed_files(errors: list[str]) -> set[str]:
    out: set[str] = set()
    for line in errors:
        if line.startswith("ERROR "):
            # "ERROR tests/m2b/test_m2b.3_spec.py" or "ERROR tests/... - ModuleNotFoundError"
            token = line.removeprefix("ERROR ").split(" ")[0].strip()
            if token.endswith(".py"):
                out.add(token.replace("\\", "/"))
    return out


def _load_register() -> list[dict[str, Any]]:
    if not REGISTER.exists():
        return []
    data = yaml.safe_load(REGISTER.read_text(encoding="utf-8")) or {}
    entries = data.get("blockers") or []
    return [e for e in entries if isinstance(e, dict)]


def main() -> int:
    code, errors = _collect()
    blamed = _blamed_files(errors)
    entries = {str(e.get("file", "")).replace("\\", "/"): e for e in _load_register()}
    # WHY UTC and not date.today(): AGENTS.md 3.8 requires UTC for internal timestamps, and a
    # review deadline that shifts by a day depending on the runner's timezone is not a deadline.
    today = dt.datetime.now(dt.UTC).date()

    print("=" * 78)
    print("D13 GUARD - suite collectability")
    print("=" * 78)

    if code == 0:
        if entries:
            print("[WARN] The suite collects, but these exemptions are still registered.")
            for path, e in entries.items():
                print(f"  - {path}")
                print("      reason to remove: the blocker it described is gone.")
            print("\n  ACTION: delete the entry/entries from")
            print(f"          {REGISTER.relative_to(ROOT)}")
            print("          A stale exemption lets a real regression hide behind it.")
        else:
            print("[OK] Suite collects cleanly. No registered blockers.")
        return 0

    unlisted = sorted(blamed - set(entries))
    listed = sorted(blamed & set(entries))

    if unlisted:
        print("[FAIL] Collection error in a file with NO registered blocker.")
        for f in unlisted:
            print(f"  - {f}")
        print("\n  This is new breakage. Fix it; do NOT add it to the register to make this")
        print("  script pass - that is how a suite starts lying about what it covers.")
        return 1

    for path in listed:
        e = entries[path]
        review_by = dt.date.fromisoformat(str(e["review_by"]))
        overdue = today > review_by
        tag = "[FAIL] REGISTERED BLOCKER IS OVERDUE" if overdue else "[WARN] REGISTERED BLOCKER"
        print(f"{tag}: {path}")
        print(f"  defect   : {e.get('defect', '?')}  slice: {e.get('slice', '?')}")
        print(f"  owner    : {e['owner']}")
        print(f"  raised   : {e['raised_on']}   review_by: {review_by}"
              f"{'  <-- OVERDUE' if overdue else ''}")
        print(f"  why      : {' '.join(str(e['why']).split())}")
        print(f"  impact   : {' '.join(str(e.get('impact', '')).split())}")
        print(f"  to clear : {' '.join(str(e['how_to_clear']).split())}")
        print()
        if overdue:
            print("  A blocker past its review date is a decision, not a delay. Either clear it")
            print("  or re-date it deliberately with a reason in BUILD_STATUS.md.")
            return 1

    print("  The suite CANNOT be collected. Every test count is qualified by this.")
    print("  AGENTS.md 2 Step 6 is not satisfiable until this clears.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
