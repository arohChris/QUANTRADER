"""WHY: M3.0 Step 7 — real-config verification of the P15 capability gate.

Prints the feature × broker matrix + strategy eligibility rows from the REAL
config/capabilities.yaml (no mocks, no network) so the human can eyeball the
UNAVAILABLE/PROXY badges — spec docs/specs/m3.0_capability.spec.md §Real-data verify.

Usage: python scripts/verify_m3.0_capability.py [--config config/capabilities.yaml]
Exit: 0 = all sanity checks OK · 1 = any check failed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # financial_analyst_app

from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from shared.contracts.market_data import CapabilityStatus

FEATURES = ("rsi_14", "order_book_imbalance", "tick_rate")
BROKERS = ("deriv", "mt5", "headway")  # headway = global defaults path (no overrides)
STRATEGIES = ("rsi_overbought_oversold", "microstructure_flow")

_BADGE = {
    CapabilityStatus.FULLY_SUPPORTED: "✓ FULLY_SUPPORTED",
    CapabilityStatus.DERIVED: "✓ DERIVED",
    CapabilityStatus.PROXY_LIMITED: "⚠ PROXY_LIMITED",
    CapabilityStatus.UNAVAILABLE: "✗ UNAVAILABLE",
    CapabilityStatus.REQUIRES_EXTERNAL_DATA: "? REQUIRES_EXTERNAL_DATA",
}


def main() -> int:
    # WHY: Windows cp1252 console cannot encode ✓/✗/≠ — force UTF-8 output.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="Verify M3.0 capability matrix")
    parser.add_argument("--config", default="config/capabilities.yaml")
    args = parser.parse_args()

    config = Path(args.config)
    registry = CapabilityRegistry.from_yaml(config)
    print(f"[OK] loaded {config} (fail-loud startup passed)")

    failures: list[str] = []

    print("\nfeature × broker matrix:")
    header = f"{'feature':<24}" + "".join(f"{b:>26}" for b in BROKERS)
    print(header)
    for feature in FEATURES:
        row = f"{feature:<24}"
        for broker in BROKERS:
            entry = registry.get(feature, broker)
            row += f"{_BADGE[entry.status]:>26}"
        print(row)

    # P15 gate sanity: Deriv order-book must be UNAVAILABLE, mt5 override available.
    p15 = registry.get("order_book_imbalance", "deriv")
    if p15.status is not CapabilityStatus.UNAVAILABLE or registry.is_available(
        "order_book_imbalance", "deriv"
    ):
        failures.append("P15: order_book_imbalance must be UNAVAILABLE on deriv")
    else:
        print(f"\n[OK] P15 gate: order_book_imbalance@deriv = UNAVAILABLE ({p15.reason})")

    mt5 = registry.get("order_book_imbalance", "mt5")
    if not registry.is_available("order_book_imbalance", "mt5"):
        failures.append("order_book_imbalance@mt5 override must be available")
    else:
        print(f"[OK] mt5 override: order_book_imbalance@mt5 = {mt5.status.value}")

    proxy = registry.get("tick_rate", "deriv")
    if proxy.status is not CapabilityStatus.PROXY_LIMITED or registry.is_available(
        "tick_rate", "deriv"
    ):
        failures.append("tick_rate must be PROXY_LIMITED and unavailable by default")
    else:
        print(f"[OK] tick_rate@deriv = PROXY_LIMITED, warning={proxy.warning!r}")

    print("\nstrategy eligibility:")
    for strategy_id in STRATEGIES:
        eligible, reason = registry.is_strategy_eligible(strategy_id, "deriv")
        print(f"  {strategy_id:<28} deriv → ({eligible!s:<5}, {reason!r})")

    expected = {"rsi_overbought_oversold": True, "microstructure_flow": False}
    for strategy_id, want in expected.items():
        got, reason = registry.is_strategy_eligible(strategy_id, "deriv")
        if got is not want:
            failures.append(f"strategy {strategy_id}@deriv expected {want}: {reason}")

    if failures:
        for failure in failures:
            print(f"[FAIL] {failure}")
        print("\nRESULT: FAILED")
        return 1
    print("\nRESULT: ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
