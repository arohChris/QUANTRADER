"""WHY: M4.7 Step 7 — real-config verification of the Tier-1 playbook cards
(spec docs/specs/m4.7_playbook_tier1.spec.md §Verify, EXIT 0/1). NOT unit
tests: (1) the 5 card modules import, expose the frozen contract, and the
registry + package discovery both see exactly the 12 sorted strategy names;
(2) one inline golden hand per card re-asserted OUTSIDE pytest (exact ==);
(3) the REAL capabilities.yaml gives all 5 new cards deriv -> eligible;
(4) ADVISORY latency of 100 x evaluate x 5. WHY hand values live here too:
pytest can pass while an import path rots — this script proves the same math
through the shipped modules from a bare interpreter (AGENTS 2 Step 7).

Usage: python scripts/verify_m4.7_playbook_tier1.py [--config config/capabilities.yaml]
Exit: 0 = all checks OK · 1 = any check failed. Latency line is ADVISORY only.
"""
from __future__ import annotations

import argparse
import pkgutil
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.m3_analytics import strategies as strategies_pkg
from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.strategies import (
    failed_breakout_sweep_reversal,
    n_bar_donchian_breakout,
    supertrend_adx_filter,
    trend_pullback_ema_stack_rsi,
    trend_pullback_to_dynamic_support,
)
from modules.m4_alpha.strategy.strategy_registry import StrategyRegistry

CARDS = (
    trend_pullback_to_dynamic_support,  # A1
    n_bar_donchian_breakout,  # B1
    failed_breakout_sweep_reversal,  # D1
    trend_pullback_ema_stack_rsi,  # S1
    supertrend_adx_filter,  # S5
)

_failures: list[str] = []


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(label)
    return ok


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    parser = argparse.ArgumentParser(description="Verify M4.7 Tier-1 cards (Step 7)")
    parser.add_argument("--config", default="config/capabilities.yaml")
    args = parser.parse_args()
    config = Path(str(args.config))

    # --- 1. import + registry discovery of 12 --------------------------------
    registry = StrategyRegistry()
    registry.load()  # the P22 gate, re-run live
    names = registry.names()
    discovered = tuple(sorted(
        info.name
        for info in pkgutil.iter_modules(strategies_pkg.__path__)
        if not info.ispkg
    ))
    print(f"registry discovery ({len(names)}): {list(names)}")
    _check(
        "registry.load() == package discovery (12)",
        names == discovered and len(names) == 12,
        f"discovered {list(discovered)}",
    )
    for mod in CARDS:
        _check(
            f"import contract: {mod.NAME} slots={sorted(mod.FEATURE_SLOTS)} "
            f"params={sorted(mod.PARAM_DEFAULTS)}",
            mod.NAME == Path(str(mod.__file__)).stem
            and bool(mod.FEATURE_SLOTS)
            and bool(mod.PARAM_DEFAULTS)
            and len({n for n, _ in mod.FEATURE_SLOTS.values()}) == len(mod.FEATURE_SLOTS),
        )

    # --- 2. inline golden evaluates (one hand per card, exact) ----------------
    goldens = [
        # Hand calc: all gates pass, close==ema -> dist 0 -> progress 1.0
        # -> +1 * (0.5 + 0.5*1) = +1.0 exact
        ("A1 touch full -> 1.0", trend_pullback_to_dynamic_support.evaluate(
            {"state": 1.0, "adx": 50.0, "er": 0.5, "r2": 0.8, "atr": 1.0,
             "ema": 100.0, "rsi": 60.0, "swlow": 95.0, "close": 100.0,
             "low": 96.0, "cpos": 0.7}, {}), 1.0),
        # Hand calc: atr_rk 40>30, er 0.5>0.25, chan 125 -> (125-100)/100
        # = 0.25 -> 0.5 + 0.125 = +0.625 exact
        ("B1 chan 125 -> 0.625", n_bar_donchian_breakout.evaluate(
            {"atr_rk": 40.0, "chan": 125.0, "er": 0.5}, {}), 0.625),
        # Hand calc: atr_rk 75>50, sweep +1 -> (75-50)/(100-50) = 0.5
        # -> 0.5 + 0.25 = +0.75 exact
        ("D1 atr_rk 75 -> 0.75", failed_breakout_sweep_reversal.evaluate(
            {"sweep": 1.0, "atr_rk": 75.0}, {}), 0.75),
        # Hand calc: long full stack 101/100.5/100, rsi 45 in [40,55], chg 0.5
        # > 0 -> spread 0.01/(0.5/100) = 2 -> clamp 1.0 -> +1.0 exact
        ("S1 full stack -> 1.0", trend_pullback_ema_stack_rsi.evaluate(
            {"adx": 30.0, "e8": 101.0, "e21": 100.5, "e55": 100.0, "rsi": 45.0,
             "atr": 1.0, "close": 100.6, "chg": 0.5, "rej": 0.0}, {}), 1.0),
        # Hand calc: flip +1, adx 50 -> progress (50-25)/(50-25) = 1.0
        # -> 0.5 + 0.5 = +1.0 exact
        ("S5 adx 50 -> 1.0", supertrend_adx_filter.evaluate(
            {"flip": 1.0, "adx": 50.0}, {}), 1.0),
        # Hand calc: state -1, dist 0 -> progress 1.0 -> -1.0 sign mirror
        ("A1 mirror -> -1.0", trend_pullback_to_dynamic_support.evaluate(
            {"state": -1.0, "adx": 50.0, "er": 0.5, "r2": 0.8, "atr": 1.0,
             "ema": 100.0, "rsi": 60.0, "swlow": 95.0, "close": 100.0,
             "low": 96.0, "cpos": 0.7}, {}), -1.0),
        # Hand calc: sweep 0 (no event) -> fail-closed 0.0 even with atr_rk 75
        ("D1 no sweep -> 0.0", failed_breakout_sweep_reversal.evaluate(
            {"sweep": 0.0, "atr_rk": 75.0}, {}), 0.0),
        # Hand calc: adx 25 == adx_min strict gate fails -> 0.0
        ("S5 adx==min -> 0.0", supertrend_adx_filter.evaluate(
            {"flip": 1.0, "adx": 25.0}, {}), 0.0),
    ]
    for label, got, want in goldens:
        _check(f"golden: {label}", got == want, f"got {got!r} want {want!r}")

    # --- 3. REAL capabilities eligibility: all 5 new cards @deriv eligible ----
    registry = CapabilityRegistry.from_yaml(config)
    print("\nTier-1 eligibility @deriv (real config):")
    for mod in CARDS:
        eligible, reason = registry.is_strategy_eligible(mod.NAME, "deriv")
        print(f"  {mod.NAME:<34} deriv -> ({eligible!s:<5}, {reason!r})")
        _check(f"eligibility {mod.NAME}@deriv == True", eligible is True, reason)

    # --- 4. ADVISORY latency: 100 x evaluate x 5 ------------------------------
    fixtures = {
        "trend_pullback_to_dynamic_support": {
            "state": 1.0, "adx": 50.0, "er": 0.5, "r2": 0.8, "atr": 1.0,
            "ema": 100.0, "rsi": 60.0, "swlow": 95.0, "close": 100.0,
            "low": 96.0, "cpos": 0.7,
        },
        "n_bar_donchian_breakout": {"atr_rk": 40.0, "chan": 125.0, "er": 0.5},
        "failed_breakout_sweep_reversal": {"sweep": 1.0, "atr_rk": 75.0},
        "trend_pullback_ema_stack_rsi": {
            "adx": 30.0, "e8": 101.0, "e21": 100.5, "e55": 100.0, "rsi": 45.0,
            "atr": 1.0, "close": 100.6, "chg": 0.5, "rej": 0.0,
        },
        "supertrend_adx_filter": {"flip": 1.0, "adx": 50.0},
    }
    medians: dict[str, float] = {}
    for mod in CARDS:
        samples: list[float] = []
        for _ in range(100):
            t0 = time.perf_counter_ns()
            mod.evaluate(fixtures[mod.NAME], {})
            samples.append((time.perf_counter_ns() - t0) / 1e6)
        medians[mod.NAME] = statistics.median(samples)
    slowest = max(medians, key=lambda k: medians[k])
    print(
        f"\n[ADVISORY] {len(medians)} evaluate() median range "
        f"{min(medians.values()):.4f}..{medians[slowest]:.4f}ms "
        f"(slowest={slowest}) over 100 runs each vs §4.1 context — "
        f"reported NOT gated"
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
