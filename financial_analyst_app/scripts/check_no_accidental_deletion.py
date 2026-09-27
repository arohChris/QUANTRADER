"""Guard against unintended DELETION or EMPTIING of a tracked file.

WHY this exists (2026-09-27, after two real incidents in one session):
  1. `modules/reference/calendar.py` was reduced to a single line by a malformed shell
     command. It was UNTRACKED, so git could not restore it and it had to be rebuilt by hand.
     A coverage/test run caught the regression, but only because a test happened to cover the
     exact guard that was dropped.
  2. `tests/m2b/test_m2b.3_spec.py` (another writer's work) was moved out of the tree by a
     `Move-Item` whose restore step silently failed. It survived only because the backup file
     was still on disk.

Both are the same failure: a destructive command with no verification afterwards, against files
that git could not recover.

WHAT THIS CHECKS (fail conditions only - deliberately narrow, to avoid noise):
  * MISSING : a file in the baseline no longer exists  -> someone/something deleted it.
  * EMPTY   : a file in the baseline is 0 bytes, or under a tiny floor -> corruption, not an
              intentional edit. (Incident 1 produced a 1-line file; this is the exact case.)

Deliberately NOT a failure condition:
  * content change / hash mismatch - normal during development, reported as INFO only.
  * new files - normal, reported as INFO only.

A legitimate deletion is easy to record: re-run with --update once the deletion is intentional
and reviewed, and the baseline follows. An accidental one cannot hide.

Run:  python scripts/check_no_accidental_deletion.py            (verify)
      python scripts/check_no_accidental_deletion.py --update   (re-baseline after a real change)
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "config" / "file_inventory.yaml"

# Surfaces whose loss matters. Deliberately excludes data/, logs, caches, .venv.
INCLUDE_DIRS = ("modules", "shared", "tests", "scripts", "app", "config", "docs/specs")
INCLUDE_SUFFIXES = (".py", ".yaml", ".yml", ".toml", ".md", ".cfg")
EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", "node_modules", ".venv", "venv"}
# A real source or spec file is never this short. Incident 1 left a 1-line file behind.
MIN_BYTES = 40


def _iter_files() -> list[Path]:
    out: list[Path] = []
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in INCLUDE_SUFFIXES:
                continue
            if EXCLUDE_PARTS & set(p.parts):
                continue
            out.append(p)
    return sorted(out)


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _read_baseline() -> dict[str, str]:
    if not BASELINE.exists():
        return {}
    text = BASELINE.read_text(encoding="utf-8")
    data: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        if line.strip().startswith("files:"):
            inside = True
            continue
        if inside:
            if line.startswith("  ") and ":" in line:
                path, _, digest = line.partition(":")
                data[path.strip().strip('"')] = digest.strip()
            elif line.strip() and not line.startswith(" "):
                inside = False
    return data


def _write_baseline() -> int:
    lines = [
        "# FILE INVENTORY - baseline for scripts/check_no_accidental_deletion.py.",
        "# WHY: two real incidents on 2026-09-27 where a file was emptied or moved away and",
        "# git could not recover it (it was untracked). This file makes that detectable.",
        "#",
        "# Regenerate ONLY after a deletion/emptying is intentional and reviewed:",
        "#     python scripts/check_no_accidental_deletion.py --update",
        "# A missing or emptied entry FAILS the check; a changed hash is INFO only.",
        "files:",
    ]
    count = 0
    for p in _iter_files():
        rel = p.relative_to(ROOT).as_posix()
        lines.append(f'  "{rel}": "{_sha256(p)}"')
        count += 1
    BASELINE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--update", action="store_true", help="rewrite the baseline")
    args = ap.parse_args(argv)

    if args.update:
        n = _write_baseline()
        print(f"[OK] Baseline written: {n} files -> {BASELINE.relative_to(ROOT)}")
        return 0

    baseline = _read_baseline()
    if not baseline:
        print("[FAIL] No baseline found. Run with --update to create it.")
        return 1

    missing: list[str] = []
    emptied: list[str] = []
    changed: list[str] = []
    added: list[str] = []

    for rel, digest in sorted(baseline.items()):
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
            continue
        # WHY __init__.py is exempt from the size floor: an EMPTY __init__.py is the correct,
        # conventional state for a package marker. Flagging it would be a false positive on
        # every run, and a guard that cries wolf is a guard that gets ignored - which is worse
        # than having no guard at all.
        if p.name == "__init__.py" and p.stat().st_size == 0:
            continue
        if p.stat().st_size < MIN_BYTES:
            emptied.append(f"{rel} ({p.stat().st_size} bytes)")
            continue
        if _sha256(p) != digest:
            changed.append(rel)

    known = set(baseline)
    for p in _iter_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel not in known:
            added.append(rel)

    print("=" * 78)
    print("ACCIDENTAL-DELETION GUARD")
    print("=" * 78)
    if changed:
        print(f"[INFO] {len(changed)} file(s) changed since baseline (expected during work).")
    if added:
        print(f"[INFO] {len(added)} new file(s) not in baseline.")
        for a in added[:10]:
            print(f"    + {a}")
        if len(added) > 10:
            print(f"    ... and {len(added) - 10} more")

    if not missing and not emptied:
        print(f"[OK] No deletions, no emptied files. {len(baseline)} baseline entries intact.")
        return 0

    if missing:
        print(f"\n[FAIL] {len(missing)} baseline file(s) MISSING - deleted or moved:")
        for m in missing:
            print(f"    - {m}")
        print("\n  If the deletion was INTENTIONAL and reviewed, re-baseline with:")
        print("      python scripts/check_no_accidental_deletion.py --update")
        print("  If it was NOT intentional, restore it now. An untracked file cannot be")
        print("  recovered from git - that is exactly how incident 2 nearly lost 16KB of")
        print("  another writer's work.")
    if emptied:
        print(f"\n[FAIL] {len(emptied)} baseline file(s) EMPTIED or truncated to <{MIN_BYTES}B:")
        for e in emptied:
            print(f"    ! {e}")
        print("\n  This is the signature of a malformed write (incident 1 left calendar.py at")
        print("  1 line). Restore from git/backup, or re-baseline if genuinely intentional.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
