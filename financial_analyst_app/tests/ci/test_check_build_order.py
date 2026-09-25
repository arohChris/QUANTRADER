"""Self-tests for scripts/check_build_order.py — tests/ci folder."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_build_order import evaluate, main

GOOD = """# BUILD_STATUS — test
## Current Gate: G0
## Sub-Modules
| Module | Sub | Spec | Contract | Tests | Pass | Hand | Real | Review | Status | Commit |
|--------|-----|------|----------|-------|------|------|------|--------|--------|--------|
| M3 | M3.0 Capability | x | n/a | - | - | - | - | - | spec | - |
| M4 | M4.1 Strategy | - | - | - | - | - | - | - | not started | - |
| M6 | M6.1 Execution | - | - | - | - | - | - | - | not started | - |
## Gates
- [ ] **G0 Edge Validation:** not done
"""

M6_EARLY = GOOD.replace("| M6 | M6.1 Execution | - | - | - | - | - | - | - | not started | - |",
                        "| M6 | M6.1 Execution | - | - | - | - | - | - | - | complete | abc |")

M4_BEFORE_M30 = GOOD.replace(
    "| M3 | M3.0 Capability | x | n/a | - | - | - | - | - | spec | - |",
    "| M3 | M3.0 Capability | x | n/a | - | - | - | - | - | spec | - |",
).replace(
    "| M4 | M4.1 Strategy | - | - | - | - | - | - | - | not started | - |",
    "| M4 | M4.1 Strategy Registry | - | - | - | - | - | - | - | complete | def |",
)


def test_good_status_passes():
    assert evaluate(GOOD) == []


def test_missing_gate_header_fails():
    fails = evaluate("no gate here\n")
    assert fails and "Current Gate" in fails[0]


def test_m6_before_g0_exit_fails():
    fails = evaluate(M6_EARLY)
    assert any("M6" in f and "G0" in f for f in fails)


def test_m41_before_m30_complete_fails():
    fails = evaluate(M4_BEFORE_M30)
    assert any("M3.0" in f or "P15" in f for f in fails)


def test_g0_exit_checked_allows_m6():
    text = M6_EARLY.replace(
        "- [ ] **G0 Edge Validation:** not done",
        "- [x] **G0 Edge Validation:** done",
    )
    assert evaluate(text) == []


def test_adversarial_empty_status_fails(tmp_path: Path):
    p = tmp_path / "BUILD_STATUS.md"
    p.write_text("", encoding="utf-8")
    assert main(["--status", str(p)]) == 1


def test_status_file_missing_fails(tmp_path: Path):
    assert main(["--status", str(tmp_path / "nope.md")]) == 1


def test_real_build_status_passes():
    # run discovery from real tree
    assert main([]) == 0


# --- Regression: guard bugs found 2026-09-24 (tests written first) ---

M41_COMPLETE = GOOD.replace(
    "| M4 | M4.1 Strategy | - | - | - | - | - | - | - | not started | - |",
    "| M4 | M4.1 Strategy Registry | - | - | - | - | - | - | - | complete | def |",
)

# Statuses that are NOT "complete" — P15 requires M3.0 complete (AGENTS §1 Rule 5)
NOT_COMPLETE_STATUSES = [
    "contract",
    "tests",
    "tests approved",
    "implemented",
    "implemented, not verified",
]


@pytest.mark.parametrize("status", NOT_COMPLETE_STATUSES)
def test_m30_not_complete_blocks_m41(status: str):
    """P15 → P22: M3.0 must be *complete*, not merely advanced.

    BUG 2026-09-24: evaluate() reused _row_is_advanced() for m30_complete, so
    M3.0 at 'tests'/'contract'/'implemented' wrongly unblocked M4.1.
    """
    text = M41_COMPLETE.replace(
        "| M3 | M3.0 Capability | x | n/a | - | - | - | - | - | spec | - |",
        f"| M3 | M3.0 Capability | x | n/a | 12 | 12/12 | 3/3 | - | - | {status} | abc |",
    )
    fails = evaluate(text)
    assert any("M3.0" in f or "P15" in f for f in fails), fails


def test_m30_complete_unblocks_m41():
    text = M41_COMPLETE.replace(
        "| M3 | M3.0 Capability | x | n/a | - | - | - | - | - | spec | - |",
        "| M3 | M3.0 Capability | x | n/a | 12 | 12/12 | 3/3 | - | - | complete | abc |",
    )
    assert evaluate(text) == []


def test_gate_beyond_g0_does_not_imply_g0_exit():
    """BUG 2026-09-24: `g0_done = ... or gate != "G0"` let G1..G3+ pass with no G0 sign-off."""
    text = M6_EARLY.replace("## Current Gate: G0", "## Current Gate: G1")
    fails = evaluate(text)
    assert any("M6" in f and "G0" in f for f in fails), fails


def test_gate_beyond_g0_with_g0_checkbox_passes():
    text = M6_EARLY.replace("## Current Gate: G0", "## Current Gate: G1").replace(
        "- [ ] **G0 Edge Validation:** not done",
        "- [x] **G0 Edge Validation:** done",
    )
    assert evaluate(text) == []


def test_g0_checked_without_artifact_fails(tmp_path: Path):
    """AGENTS §1 Rule 3: every gate produces a re-runnable artifact."""
    status = tmp_path / "BUILD_STATUS.md"
    status.write_text(
        M6_EARLY.replace(
            "- [ ] **G0 Edge Validation:** not done",
            "- [x] **G0 Edge Validation:** done",
        ),
        encoding="utf-8",
    )
    app = tmp_path / "app"
    app.mkdir()
    assert main(["--status", str(status), "--root", str(app)]) == 1


def test_g0_checked_with_artifact_passes(tmp_path: Path):
    status = tmp_path / "BUILD_STATUS.md"
    status.write_text(
        M6_EARLY.replace(
            "- [ ] **G0 Edge Validation:** not done",
            "- [x] **G0 Edge Validation:** done",
        ),
        encoding="utf-8",
    )
    app = tmp_path / "app"
    (app / "scripts").mkdir(parents=True)
    (app / "scripts" / "gate0_walkforward.py").write_text("# g0\n", encoding="utf-8")
    assert main(["--status", str(status), "--root", str(app)]) == 0
