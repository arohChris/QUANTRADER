#!/usr/bin/env python3
"""
Retention guard — blocks per-tick AnalyticsResult cold-parquet writes.
WHY: v1.5 §24/§31 — keep RAW+candle+Decision lineage; recompute via PIT+M3.2;
     AnalyticsResult is Redis-ephemeral cache, not cold lake per tick.
Usage: python scripts/check_retention.py [--root PATH]
Exit: 0 = clean; 1 = banned cold analytics write pattern.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]

SCAN_GLOBS = (
    "modules/**/*.py",
    "shared/**/*.py",
    "scripts/**/*.py",
)

# Banned: AnalyticsResult (or features dict) written to parquet/cold lake path
RE_ANNOT = re.compile(r"\bAnalyticsResult\b")
RE_COLD_WRITE = re.compile(
    r"(?:to_parquet|write_parquet|write_batch|pq\.write_table|"
    r"analytics[a-z_]*\.parquet|lake/[^\s'\"]*analytics)",
    re.IGNORECASE,
)
# Explicit path with both analytics + parquet near each other
RE_ANALYTICS_PARQUET = re.compile(
    r"analytics[^\n]{0,80}parquet|parquet[^\n]{0,80}analytics",
    re.IGNORECASE,
)
# A features dict written cold — banned by the docstring regardless of path.
RE_FEATURES_COLD = re.compile(
    r"\bfeatures?\b\s*\.\s*(?:to_parquet|write_parquet|to_feather|write_table)",
    re.IGNORECASE,
)

MSG_PATH = (
    "analytics parquet path written (cold) — "
    "use lineage IDs only; recompute via PIT+M3.2"
)
MSG_FEATURES = (
    "features dict cold write (parquet/lake) — "
    "cache in Redis with TTL, not cold parquet"
)
MSG_ANNOT = (
    "AnalyticsResult cold write pattern (parquet/lake analytics) — "
    "use lineage IDs only; recompute via PIT+M3.2"
)

# Allowed signals in same file — lineage / market-data / cache (does not clear Annotation+cold if both present alone)
# We do NOT auto-allow on these: only structural allow is: no AnalyticsResult+write together.


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _is_guard_script(path: Path) -> bool:
    """WHY scoped to scripts/: `path.name.startswith('check_')` alone let any
    `modules/**/check_*.py` name itself out of the scan."""
    return path.name.startswith("check_") and "scripts" in path.parts


def file_violations(path: Path) -> list[str]:
    if _is_guard_script(path):
        return []
    text = _read(path)
    # WHY these fire without RE_ANNOT: the annotation gate hid plain
    # `df.to_parquet('.../analytics/...')` and `features.to_parquet(...)`,
    # which are exactly the retention leaks §24 bans.
    if RE_ANALYTICS_PARQUET.search(text):
        return [MSG_PATH]
    if RE_FEATURES_COLD.search(text):
        return [MSG_FEATURES]
    if RE_ANNOT.search(text) and RE_COLD_WRITE.search(text):
        return [MSG_ANNOT]
    return []


def run(root: Path) -> int:
    files: list[Path] = []
    for pattern in SCAN_GLOBS:
        files.extend(root.glob(pattern))
    files = sorted({p.resolve() for p in files if p.is_file()})

    failed = False
    for path in files:
        for msg in file_violations(path):
            failed = True
            rel = path.relative_to(root) if path.is_relative_to(root) else path
            print(f"[FAIL] {rel}: {msg}")
            print("# FIX: store RAW ticks + candles + Decision/analytics_refs only;")
            print("#      cache AnalyticsResult in Redis with TTL (e.g. setex 60), not cold parquet.")

    if failed:
        return 1
    print(f"[OK] retention: scanned {len(files)} file(s), no AnalyticsResult cold write")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retention guard — no per-tick analytics cold write")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="app root (default: financial_analyst_app)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        print(f"[FAIL] root not found: {root}")
        return 1
    return run(root)


if __name__ == "__main__":
    sys.exit(main())
