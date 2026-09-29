"""Root conftest: make a registered collection blocker IMPOSSIBLE to miss.

WHY this exists (defect D13): a missing module made the whole suite uncollectable, so
day-to-day `pytest` could not run at all. The blocker is excluded in `pyproject.toml`
`addopts` so work can continue - but a silent exclusion is exactly the "works, and is quietly
wrong" failure this project forbids. So every run prints who owns the blocker, what it costs,
and how to clear it.

The exclusion is NOT a permanent state:
  * `scripts/check_collection.py` FAILS on any collection error that is not registered;
  * ...FAILS if a registered blocker passes its `review_by` date;
  * ...FAILS if a registered blocker no longer blocks anything (stale exemption), so a real
    regression cannot hide behind an out-of-date entry.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
REGISTER = ROOT / "config" / "known_collection_blockers.yaml"


def _registered_blockers() -> list[dict[str, object]]:
    if not REGISTER.exists():
        return []
    try:
        data = yaml.safe_load(REGISTER.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    return [e for e in (data.get("blockers") or []) if isinstance(e, dict)]


def pytest_report_header(config: object) -> str | None:
    entries = _registered_blockers()
    if not entries:
        return None
    lines = [
        "",
        "!" * 78,
        "!!  INCOMPLETE SUITE - " + str(len(entries)) + " REGISTERED COLLECTION BLOCKER(S)",
        "!" * 78,
        "    Tests below EXCLUDE the blocked file(s). This run does NOT describe the whole",
        "    system, and AGENTS.md 2 Step 6 is not satisfied while this stands.",
    ]
    for e in entries:
        lines += [
            "",
            f"  file      : {e.get('file')}",
            f"  defect    : {e.get('defect', '?')}   slice: {e.get('slice', '?')}",
            f"  owner     : {e.get('owner')}",
            f"  raised    : {e.get('raised_on')}   review_by: {e.get('review_by')}",
            f"  impact    : {' '.join(str(e.get('impact', '')).split())}",
            f"  to clear  : {' '.join(str(e.get('how_to_clear', '')).split())}",
        ]
    lines += [
        "",
        "  Verify with:  python scripts/check_collection.py",
        "!" * 78,
        "",
    ]
    return "\n".join(lines)


def pytest_collection_finish(session: object) -> None:
    """Belt-and-braces: the header can be suppressed with -q, so warn on stderr too."""
    entries = _registered_blockers()
    if entries:
        names = ", ".join(str(e.get("file")) for e in entries)
        print(
            f"\n[INCOMPLETE SUITE] {len(entries)} registered collection blocker(s) excluded: "
            f"{names}\n"
            f"    This run does NOT cover the whole system. "
            f"See config/known_collection_blockers.yaml.\n",
            file=sys.stderr,
        )
