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
    # --- API surface (added 2026-09-27) ---
    # WHY: the API location was correct only by human effort. Nothing failed if it moved
    # or vanished, because the previous REQUIRED list covered only shared/* and config/*.
    # `app/main.py` is the composition root (bible 3); every HTTP route lives under
    # `modules/experience/api/v1/` (bible 3, 10_M10_Experience.md:52).
    "app/main.py",
    "modules/experience/README.md",
    "modules/experience/api/v1/router.py",
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
    # --- M1.5 trading preferences (Step 5, 2026-09-30) ---
    "shared/contracts/strategy_admin.py",
    "shared/contracts/trading_preferences.py",
    "shared/contracts/strategy_subscription.py",
    # --- M4.8 strategy approval (Step 5, 2026-10-03) ---
    "shared/contracts/strategy_approval.py",
    "shared/core/engine_interface.py",
    "shared/core/clock.py",
    "shared/core/lifecycle.py",
    "shared/core/config.py",
    "shared/core/tenant_context.py",
    "shared/core/permissions.py",
    "shared/core/retry_policy.py",
    "shared/core/broker_vault.py",
    "shared/core/analysis_schedule.py",
    "shared/core/trading_run_rule.py",
    "shared/core/trading_prefs_store.py",
    "shared/core/strategy_approval_store.py",
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
            # WHY line-anchored, not `needle in text`: prose/comments that merely
            # MENTION a forbidden path (base.py's WHY note about shared.core.money)
            # are not violations — only actual import statements are. Real imports
            # stay authoritative in import-linter (lint-imports) anyway.
            if any(ln.lstrip().startswith(needle) for ln in text.splitlines()):
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
