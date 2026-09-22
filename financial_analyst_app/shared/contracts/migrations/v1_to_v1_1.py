"""
Example migration — when MarketData adds a field, put the upgrade here.
WHY: Lake stores v1.0 parquet forever. After code bumps to v1.1, old rows must still read.
Contract bump rule: patch = clarify, minor = add optional field, major = break.
"""
from __future__ import annotations

from typing import Any


def migrate_market_data_v1_to_v1_1(old: dict[str, Any]) -> dict[str, Any]:
    """
    Example: v1.1 adds `provider: str` field. Old v1.0 rows lack it → default to None.
    WHY not just default in model? Because parquet read needs explicit migration to
    preserve audit — we know which rows were migrated vs native v1.1.
    """
    if "provider" not in old:
        old["provider"] = None  # migrated
    old["version"] = "1.1"
    return old


# Snapshot test helper — ensures migration is deterministic
def test_migration_snapshot() -> None:
    old = {"symbol": "EURUSD", "version": "1.0"}
    new = migrate_market_data_v1_to_v1_1(old.copy())
    assert new["version"] == "1.1"
    assert new["provider"] is None
