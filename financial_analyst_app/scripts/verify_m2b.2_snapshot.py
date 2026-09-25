#!/usr/bin/env python3
"""
M2b.2 snapshot lineage — real-config verify (spec Step 7 artifact).
WHY: prove the repo-seeded config/reference/snapshot.yaml loads, formats correctly,
     equals current_snapshot_id(), and that failure paths return None (never raise).
Usage: python scripts/verify_m2b.2_snapshot.py
Exit: 0 = OK; 1 = first failed check (prints which).
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # financial_analyst_app

from modules.reference import reference

_SNAPSHOT_RE = re.compile(r"^REF-\d{4}-\d{2}-\d{2}-\d{3}$")
_REPO_PATH = Path(__file__).resolve().parents[1] / "config" / "reference" / "snapshot.yaml"


def _check(label: str, ok: bool) -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}")
    return ok


def main() -> int:
    ok = True
    loaded = reference.load_snapshot_id(_REPO_PATH)
    ok &= _check(f"repo seed loads: {loaded}", loaded is not None)
    ok &= _check("repo seed format REF-YYYY-MM-DD-NNN", bool(loaded and _SNAPSHOT_RE.fullmatch(loaded)))

    reference.current_snapshot_id.cache_clear()
    current = reference.current_snapshot_id()
    ok &= _check(f"current_snapshot_id() == repo seed ({current})", current == loaded)
    ok &= _check("deterministic across two calls", reference.current_snapshot_id() == current)

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # failure paths → None, never raise (consumer contract with M2.2)
        ok &= _check("missing file -> None", reference.load_snapshot_id(tmp / "nope.yaml") is None)
        bad = tmp / "bad.yaml"
        bad.write_text("snapshot_id: not-a-ref\neffective_from: 2026-01-01T00:00:00+00:00\n", encoding="utf-8")
        ok &= _check("bad format -> None", reference.load_snapshot_id(bad) is None)
        naive = tmp / "naive.yaml"
        naive.write_text("snapshot_id: REF-2026-01-01-001\neffective_from: 2026-01-01T00:00:00\n", encoding="utf-8")
        ok &= _check("naive effective_from -> None", reference.load_snapshot_id(naive) is None)
        ok &= _check("directory path -> None (no raise)", reference.load_snapshot_id(tmp) is None)

    print(f"snapshot={loaded} current={current} — {'ALL OK' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
