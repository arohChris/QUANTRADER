#!/usr/bin/env python3
"""
Naming guard — mX.Y_<short>.spec.md / test_mX.Y_spec.py / verify_mX.Y_*.py
WHY: human-directed naming law 2026-09-22 (BUILD_STATUS Discrepancies).
Usage: python scripts/check_naming.py [--root PATH] [--specs PATH]
Exit: 0 = OK; 1 = illegal name.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]  # financial_analyst_app
DEFAULT_REPO = Path(__file__).resolve().parents[2]
DEFAULT_SPECS = DEFAULT_REPO / "docs" / "specs"

# m2b.1 / m10.1 / m3.0 — optional letter after major (m2b.1_instrument_symbol.spec.md)
RE_SPEC = re.compile(r"^m\d+[a-z]?\.\d+_[a-z0-9_]+\.spec\.md$", re.IGNORECASE)
# test_m2.1_connection_spec.py, test_m2b.1_spec.py, test_m3.0_spec.py
RE_TEST_M = re.compile(r"^test_m\d+[a-z]?\.\d+.*_spec\.py$")
RE_VERIFY_M = re.compile(r"^verify_m\d+[a-z]?\.\d+_[a-z0-9_]+\.py$")

# Foundation / non-mX.Y tests — grandfathered (M1/M10, not module slices)
TEST_ALLOWLIST = {
    "test_clock.py",
    "test_config.py",
    "test_health.py",
    "test_retry.py",
    "test_tenant_isolation.py",
    "test_account_switch.py",
    "test_account_mode.py",
    "test_account_mode_two_gate.py",
    "test_account_mode_page.py",
    "test_contracts.py",
    "__init__.py",
    "conftest.py",
}

# CI self-tests live under tests/ci/ (test_check_*.py) — not market-data slices
CI_TEST_PREFIX = "test_check_"

# Non-m* verify scripts that are tools, not slice verifies
VERIFY_ALLOWLIST = {
    "check_structure.py",
    "check_pit_guard.py",
    "check_retention.py",
    "check_build_order.py",
    "check_naming.py",
    "__init__.py",
}


def check_specs(specs_dir: Path) -> list[str]:
    fails: list[str] = []
    if not specs_dir.is_dir():
        return [f"specs dir missing: {specs_dir}"]
    for p in sorted(specs_dir.glob("*.md")):
        if not RE_SPEC.match(p.name):
            fails.append(f"docs/specs/{p.name}: expected mX.Y_<short>.spec.md")
    return fails


def check_tests(root: Path) -> list[str]:
    fails: list[str] = []
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return []
    for p in sorted(tests_dir.rglob("test_*.py")):
        name = p.name
        # tests/ci/* guard self-tests — separate folder, not mX.Y slices
        if "ci" in p.relative_to(tests_dir).parts or name.startswith(CI_TEST_PREFIX):
            continue
        if name in TEST_ALLOWLIST:
            continue
        # Any test_m* must match full slice pattern; other test_* foundation names must be allowlisted
        if name.startswith("test_m"):
            if not RE_TEST_M.match(name):
                fails.append(f"tests/.../{name}: expected test_mX.Y_spec.py")
        elif name not in TEST_ALLOWLIST:
            # Non-module test not on allowlist — require mX.Y pattern or add allowlist
            fails.append(
                f"tests/.../{name}: not allowlisted — use test_mX.Y_spec.py or add to TEST_ALLOWLIST"
            )
    return fails


def check_verify(root: Path) -> list[str]:
    fails: list[str] = []
    scripts = root / "scripts"
    if not scripts.is_dir():
        return []
    for p in sorted(scripts.glob("verify_*.py")):
        if not RE_VERIFY_M.match(p.name):
            fails.append(f"scripts/{p.name}: expected verify_mX.Y_<short>.py")
    for p in sorted(scripts.glob("*.py")):
        if p.name in VERIFY_ALLOWLIST or p.name.startswith("check_"):
            continue
        # other loose scripts (not check_*, not verify_*) — ignore non-slice tools
        if (
            p.name.startswith("verify")
            and not RE_VERIFY_M.match(p.name)
            and f"scripts/{p.name}" not in fails
        ):
            fails.append(f"scripts/{p.name}: expected verify_mX.Y_<short>.py")
    return fails


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Naming guard for specs/tests/verify")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="financial_analyst_app root")
    parser.add_argument("--specs", type=Path, default=DEFAULT_SPECS, help="docs/specs directory")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    specs = args.specs.resolve()
    fails = check_specs(specs) + check_tests(root) + check_verify(root)
    if fails:
        for f in fails:
            print(f"[FAIL] {f}")
        return 1
    print("[OK] naming: mX.Y specs, test_mX.Y_spec.py, verify_mX.Y_*.py conform")
    return 0


if __name__ == "__main__":
    sys.exit(main())
