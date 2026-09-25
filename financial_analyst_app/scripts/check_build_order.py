#!/usr/bin/env python3
"""
Build-order guard — G0 exit before M6; P15 (M3.0 complete) before M4.1/P22.
WHY: AGENTS.md Rule 5 / v1.5 build order is law; status file is truth.
Usage: python scripts/check_build_order.py [--status PATH] [--root PATH]
Exit: 0 = order OK (including not-started slices); 1 = violation or missing gate.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]  # financial_analyst_app
DEFAULT_STATUS_CANDIDATES = (
    Path(__file__).resolve().parents[2] / "BUILD_STATUS.md",  # repo root
    DEFAULT_ROOT / "BUILD_STATUS.md",
)

GATE_RE = re.compile(r"^##\s*Current Gate:\s*(G[0-9]+\+?)", re.MULTILINE | re.IGNORECASE)
G0_EXIT_RE = re.compile(r"^\s*-\s*\[x\]\s*\*\*G0", re.MULTILINE | re.IGNORECASE)

# Statuses that mean "code landed / advanced"
ADVANCED = {
    "complete",
    "implemented",
    "implemented, not verified",
    "tests approved",
    "tests",
    "contract",
}

# G0 exit artifact — AGENTS §1 Rule 3: a claimed gate exit must be re-runnable
G0_ARTIFACT = Path("scripts") / "gate0_walkforward.py"

# Rows we care about: parse table lines starting with | M6 | or | M4 |
ROW_RE = re.compile(
    r"^\|\s*(M\d+b?(?:\.\d+)?)\s*\|\s*([^|]+?)\s*\|.*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|$",
    re.MULTILINE,
)


def parse_status(text: str) -> tuple[str | None, list[dict[str, str]]]:
    m = GATE_RE.search(text)
    gate = m.group(1).upper() if m else None
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        if re.match(r"^\|\s*Module\s*\|", line, re.IGNORECASE):
            continue
        if re.match(r"^\|\s*-+", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 11:
            continue
        # Module | Sub | Spec | Contract | Tests | Pass | Hand | Real | Review | Status | Commit
        rows.append(
            {
                "module": cells[0],
                "sub": cells[1],
                "status": cells[9].lower() if len(cells) > 9 else "",
            }
        )
    return gate, rows


def _row_is_advanced(status: str) -> bool:
    s = status.strip().strip("*").lower()
    # strip markdown bold
    s = s.replace("**", "")
    if s in ADVANCED:
        return True
    return s.startswith(("complete", "implemented"))


def _row_is_complete(status: str) -> bool:
    """'complete' only. WHY: P15 (M3.0) is a gate, not an increment — 'tests' does not unblock P22."""
    s = status.strip().replace("**", "").lower()
    return s.startswith("complete")


def g0_exit_claimed(status_text: str) -> bool:
    if G0_EXIT_RE.search(status_text):
        return True
    return bool(re.search(r"G0 Exit:\s*\[x\]", status_text, re.IGNORECASE))


def check_g0_artifact(status_text: str, root: Path) -> list[str]:
    """AGENTS §1 Rule 3 — a claimed G0 exit needs a script a third party can re-run."""
    if not g0_exit_claimed(status_text):
        return []
    if (root / G0_ARTIFACT).is_file():
        return []
    msg = (
        f"Build order: G0 exit checked but {G0_ARTIFACT.as_posix()} missing under {root} — "
        "gate exit requires a re-runnable artifact"
    )
    return [msg]


def evaluate(status_text: str) -> list[str]:
    """Return list of failure messages (empty = OK)."""
    fails: list[str] = []
    gate, rows = parse_status(status_text)
    if not gate:
        fails.append("BUILD_STATUS missing '## Current Gate: G0|G1|G2|G3+'")
        return fails

    # WHY no `or gate != "G0"`: a G1..G3+ header with an unticked G0 box would
    # otherwise imply the prior gate was signed off. Checkbox (or "G0 Exit: [x]")
    # is the only evidence — gate number is a label, not a sign-off.
    g0_done = g0_exit_claimed(status_text)

    # M6 advanced without G0 exit
    m6_adv = any(
        r["module"].upper().startswith("M6") and _row_is_advanced(r["status"]) for r in rows
    )
    if m6_adv and not g0_done:
        fails.append(
            "Build order: M6 status advanced but G0 exit not recorded "
            "(need scripts/gate0_walkforward.py + expectancy>0 sign-off)"
        )

    # P15 before P22: M4.1 / strategy registry advanced while M3.0 not complete
    m30 = next((r for r in rows if r["module"].upper() == "M3" and "M3.0" in r["sub"]), None)
    m30_complete = bool(m30 and _row_is_complete(m30["status"]))
    m41_adv = any(
        r["module"].upper().startswith("M4")
        and ("M4.1" in r["sub"] or "Strategy" in r["sub"] or "Registry" in r["sub"])
        and _row_is_advanced(r["status"])
        for r in rows
    )
    if m41_adv and not m30_complete:
        fails.append(
            "Build order: M4.1/Strategy advanced but M3.0 Capability not complete "
            "(P15 must complete before P22)"
        )

    return fails


def find_status(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.is_file() else None
    for cand in DEFAULT_STATUS_CANDIDATES:
        if cand.is_file():
            return cand
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build-order guard")
    parser.add_argument("--status", type=Path, default=None, help="path to BUILD_STATUS.md")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="app root (for default status discovery)")
    args = parser.parse_args(argv)

    status_path = find_status(args.status)
    if status_path is None:
        print("[FAIL] BUILD_STATUS.md not found — cannot verify build order")
        return 1

    text = status_path.read_text(encoding="utf-8", errors="replace")
    fails = evaluate(text)
    fails += check_g0_artifact(text, args.root.resolve())
    if fails:
        for f in fails:
            print(f"[FAIL] {f}")
        return 1

    gate_m = GATE_RE.search(text)
    gate = gate_m.group(1) if gate_m else "?"
    print(f"[OK] build_order: Current Gate {gate}; M6/M4.1 order constraints hold ({status_path.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
