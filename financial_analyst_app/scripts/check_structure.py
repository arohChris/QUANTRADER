#!/usr/bin/env python3
"""
M1.3 — Structure Guard — verifies folder tree matches Bible §3 canonical tree.
CI fails if someone adds top-level folder without updating bible.

Usage: python scripts/check_structure.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    "shared/contracts/base.py",
    "shared/contracts/market_data.py",
    "shared/contracts/analytics.py",
    "shared/contracts/signal.py",
    "shared/contracts/trade_plan.py",
    "shared/contracts/portfolio.py",
    "shared/contracts/risk.py",
    "shared/contracts/order.py",
    "shared/contracts/execution.py",
    "shared/contracts/reconciliation.py",
    "shared/contracts/errors.py",
    "shared/core/engine_interface.py",
    "shared/core/clock.py",
    "shared/core/lifecycle.py",
    "shared/core/config.py",
    "shared/core/tenant_context.py",
    "shared/core/permissions.py",
    "shared/core/retry_policy.py",
    "shared/core/broker_vault.py",
    "config/system/system.yaml",
    "config/risk/limits.yaml",
    "config/assets/forex.yaml",
    "config/brokers/deriv.yaml",
]

FORBIDDEN_IMPORTS = [
    # Bible §33 — checked here + via import-linter
    ("shared/core", "from modules"),
    ("shared/contracts", "from modules"),
    ("shared/contracts", "from shared.core"),
]


def check_required() -> list[str]:
    missing = []
    for rel in REQUIRED:
        if not (ROOT / rel).exists():
            missing.append(rel)
    return missing


def check_forbidden_imports() -> list[str]:
    violations = []
    for src_dir, needle in FORBIDDEN_IMPORTS:
        for py in (ROOT / src_dir).rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if needle in text:
                violations.append(f"{py.relative_to(ROOT)} contains forbidden '{needle}'")
    return violations


def main() -> int:
    # ASCII-only output: Windows cp1252 console cannot encode emoji/symbols
    missing = check_required()
    violations = check_forbidden_imports()
    if missing:
        print("[FAIL] Missing required files (Bible Sec 3):")
        for m in missing:
            print(f"  - {m}")
    if violations:
        print("[FAIL] Forbidden imports (Bible Sec 33):")
        for v in violations:
            print(f"  - {v}")
    if missing or violations:
        return 1
    print("[OK] Structure matches Bible Sec 3 canonical tree (M1 Foundation)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
