"""WHY: M4.2 Step 7 — real-config verification of the 7 strategy files
(spec docs/specs/m4.2_strategy_files.spec.md §Verify). NOT unit tests: proves all
7 files import + discover, the hand goldens hold outside pytest, the REAL
config/capabilities.yaml manifest lists all 7 (donchian stub fulfilled), and the
M3.0 eligibility gate returns 6 × True / microstructure_flow × False on deriv
honestly naming the unavailable order_book_imbalance.

Usage: python scripts/verify_m4.2_strategies.py [--config config/capabilities.yaml]
Exit: 0 = all checks OK · 1 = any check failed. Latency line is ADVISORY only.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml

from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.strategies import (
    donchian_breakout,
    mean_reversion,
    microstructure_flow,
    pullback,
    regime_based,
    rsi_overbought_oversold,
    trend_following,
)

MODULES = (
    trend_following,
    rsi_overbought_oversold,
    mean_reversion,
    donchian_breakout,
    pullback,
    regime_based,
    microstructure_flow,
)

_failures: list[str] = []


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(label)
    return ok


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="Verify M4.2 strategy files (Step 7)")
    parser.add_argument("--config", default="config/capabilities.yaml")
    args = parser.parse_args()
    config = Path(args.config)

    # --- 1. discovery: all 7 import + expose the contract ----------------------
    names = sorted(mod.NAME for mod in MODULES)
    print(f"discovery (7): {names}")
    _check(
        "7 strategy files import with names == capabilities ids",
        names
        == [
            "donchian_breakout",
            "mean_reversion",
            "microstructure_flow",
            "pullback",
            "regime_based",
            "rsi_overbought_oversold",
            "trend_following",
        ],
    )
    for mod in MODULES:
        _check(
            f"contract: {mod.NAME} slots={sorted(mod.FEATURE_SLOTS)} "
            f"params={sorted(mod.PARAM_DEFAULTS)}",
            bool(mod.FEATURE_SLOTS) and bool(mod.PARAM_DEFAULTS)
            and len({n for n, _ in mod.FEATURE_SLOTS.values()}) == len(mod.FEATURE_SLOTS),
        )

    # --- 2. golden evaluates (outside pytest, one per strategy, hand calc) -----
    goldens = [
        # Hand calc: gap 1% = full -> progress 1.0 -> 0.5+0.5 = 1.0 LONG
        ("trend_following long full", trend_following.evaluate(
            {"fast": 101.0, "slow": 100.0, "rsi": 65.0}, {}), 1.0),
        # Hand calc: rsi 85 -> progress (85-70)/30 = 0.5 -> -0.75 short
        ("rsi_ob/os 85 -> -0.75", rsi_overbought_oversold.evaluate({"rsi": 85.0}, {}), -0.75),
        # Hand calc: z=3 entry 2 -> progress 1/2 = 0.5 -> -0.75 fade
        ("mean_reversion z3 -> -0.75", mean_reversion.evaluate({"z": 3.0}, {}), -0.75),
        # Hand calc: chan 150 chop 20 -> progress 0.5 -> 0.75 LONG
        ("donchian 150/20 -> 0.75", donchian_breakout.evaluate(
            {"chan": 150.0, "chop": 20.0}, {}), 0.75),
        # Hand calc: adx 30 z=-2 -> progress (2-1)/1 = 1.0 -> +1.0 LONG
        ("pullback 30/-2 -> 1.0", pullback.evaluate({"trend": 30.0, "entry": -2.0}, {}), 1.0),
        # categorical: chop 30 < 38 + hurst 0.7 > 0.5 -> trend regime ON
        ("regime 30/0.7 -> 1.0", regime_based.evaluate(
            {"chop": 30.0, "hurst": 0.7}, {}), 1.0),
        # Hand calc: imb 1.0 -> progress (1-0.6)/(1-0.6) = 1.0 -> +1.0 exact
        ("micro imb 1.0 -> 1.0", microstructure_flow.evaluate({"imb": 1.0}, {}), 1.0),
        # dead band: no signal case
        ("micro imb 0.0 -> 0.0", microstructure_flow.evaluate({"imb": 0.0}, {}), 0.0),
    ]
    for label, got, want in goldens:
        _check(f"golden: {label}", got == want, f"got {got!r} want {want!r}")

    # --- 3. REAL manifest + deriv eligibility matrix ---------------------------
    manifest = yaml.safe_load(config.read_text(encoding="utf-8"))["strategies"]
    print(f"manifest ({config}): {sorted(manifest)}")
    _check("real yaml lists all 7 strategies", sorted(manifest) == names)
    _check(
        "donchian stub fulfilled: [donchian_20, choppiness]",
        manifest["donchian_breakout"]["requires_analytics"] == ["donchian_20", "choppiness"],
        f"got {manifest['donchian_breakout']['requires_analytics']!r}",
    )
    registry = CapabilityRegistry.from_yaml(config)
    expected = {mod.NAME: True for mod in MODULES}
    expected["microstructure_flow"] = False
    print("\nstrategy eligibility @deriv (real config):")
    for strategy_id, want in expected.items():
        eligible, reason = registry.is_strategy_eligible(strategy_id, "deriv")
        print(f"  {strategy_id:<28} deriv -> ({eligible!s:<5}, {reason!r})")
        _check(f"eligibility {strategy_id}@deriv == {want}", eligible is want, reason)
    _, reason = registry.is_strategy_eligible("microstructure_flow", "deriv")
    _check(
        "microstructure reason names the unavailable feature",
        reason.startswith("needs order_book_imbalance"),
        reason,
    )

    # --- 4. ADVISORY latency: 100 x evaluate x 7 files -------------------------
    fixtures = {
        "trend_following": {"fast": 101.0, "slow": 100.0, "rsi": 65.0},
        "rsi_overbought_oversold": {"rsi": 85.0},
        "mean_reversion": {"z": 3.0},
        "donchian_breakout": {"chan": 150.0, "chop": 20.0},
        "pullback": {"trend": 30.0, "entry": -2.0},
        "regime_based": {"chop": 30.0, "hurst": 0.7},
        "microstructure_flow": {"imb": 1.0},
    }
    medians: dict[str, float] = {}
    for mod in MODULES:
        samples: list[float] = []
        for _ in range(100):
            t0 = time.perf_counter_ns()
            mod.evaluate(fixtures[mod.NAME], {})
            samples.append((time.perf_counter_ns() - t0) / 1e6)
        medians[mod.NAME] = statistics.median(samples)
    slowest = max(medians, key=lambda k: medians[k])
    print(
        f"\n[ADVISORY] 7 evaluate() median range {min(medians.values()):.4f}.."
        f"{medians[slowest]:.4f}ms (slowest={slowest}) over 100 runs each vs "
        f"§4.1 context — reported NOT gated"
    )

    if _failures:
        for failure in _failures:
            print(f"[FAIL] {failure}")
        print("\nRESULT: FAILED")
        return 1
    print("\nRESULT: ALL OK (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
