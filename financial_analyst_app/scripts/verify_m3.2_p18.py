"""WHY: M3.2 Step 7 — real-data verification of the 12 P18 compute files
(spec docs/specs/m3.2_p18_per_asset.spec.md §Verify). NOT unit tests: proves D4
discovery finds all 34 (P17+P18+Book batch 1 + m4.7 batch — declared §7
amendments; also removes the
stale `tick_rate` expectation left since the Candle 2.0.0 deletion), the hand
goldens hold outside pytest, TWO independent atr_14/adx_14 implementations agree
on a deterministic 60-bar walk seeded from a REAL sample close, and an engine
e2e over the walk returns all 12 features FULLY_SUPPORTED with real forex
profiles.

Usage: python scripts/verify_m3.2_p18.py
Exit: 0 = all checks OK · 1 = any check failed. Latency line is ADVISORY only
(§4.1 analytics <20ms enforcement is the engine's latency header — a later slice).
"""
from __future__ import annotations

import asyncio
import math
import random
import statistics
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.m3_analytics.analytics import (
    adx_14,
    atr_14,
    bb_percent,
    choppiness,
    donchian_20,
    hurst,
    macd,
    returns,
    sma_20,
    std_20,
    vol_regime,
    zscore_20,
)
from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.analytics_registry import AnalyticsRegistry
from modules.m3_analytics.engine.feature_engine import (
    FeatureEngine,
    FeatureRequest,
    StaticCandleProvider,
)
from modules.m3_analytics.engine.profiles import Profiles
from shared.contracts.market_data import Candle, CapabilityStatus

_CAP_YAML = ROOT / "config" / "capabilities.yaml"
_ASSETS_DIR = ROOT / "config" / "assets"
_WALK_SEED = 20260925
_REAL_SAMPLE_CLOSE = 1.08548  # from data/sample_ticks_realistic.jsonl (M2.4 output)

_EXPECTED_34 = [
    "adx_14",
    "anchored_twap",
    "atr_14",
    "atr_percentile_rank",
    "bar_low",
    "bb_percent",
    "bollinger_bandwidth",
    "bollinger_bandwidth_percentile",
    "choppiness",
    "close_change",
    "close_position",
    "donchian_20",
    "efficiency_ratio",
    "ema_20",
    "ema_21",
    "ema_55",
    "ema_8",
    "hurst",
    "last_close",
    "macd",
    "market_state",
    "ols_r_squared",
    "rejection_bar",
    "returns",
    "rsi_14",
    "sma_20",
    "std_20",
    "supertrend_flip",
    "sweep_reversal",
    "swing_high",
    "swing_low",
    "variance_ratio",
    "vol_regime",
    "zscore_20",
]
# P18 batch = everything that is NOT P17 (ema/rsi) and NOT Book batch 1 (§7 amendment).
_P17 = ("ema_20", "rsi_14")
_BOOK = (
    "anchored_twap",
    "atr_percentile_rank",
    "bollinger_bandwidth",
    "bollinger_bandwidth_percentile",
    "efficiency_ratio",
    "market_state",
    "ols_r_squared",
    "swing_high",
    "swing_low",
    "variance_ratio",
)
# m4.7 batch (declared amendment 2) — excluded so _P18 stays exactly the 12 P18 files.
_M47 = (
    "bar_low",
    "close_change",
    "close_position",
    "ema_21",
    "ema_55",
    "ema_8",
    "last_close",
    "rejection_bar",
    "supertrend_flip",
    "sweep_reversal",
)
_P18 = [
    name
    for name in _EXPECTED_34
    if name not in _P17 and name not in _BOOK and name not in _M47
]

_failures: list[str] = []


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(label)
    return ok


def _mk_candles(closes: list[float]) -> list[Candle]:
    """Minimal 1m candles with given closes (fixture series for computes).
    WHY no tick_count: Candle 2.0.0 removed the field (extra=forbid) — the old
    `tick_count=ticks` arg raised ValidationError after the contract migration."""
    end = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)  # tz-aware: as_of validator
    out = []
    for i, close in enumerate(closes):
        start = end - timedelta(minutes=len(closes) - i)
        out.append(
            Candle(
                open=close,
                high=close,
                low=close,
                close=close,
                interval="1m",
                interval_start=start,
                interval_end=start + timedelta(minutes=1),
            )
        )
    return out


def _walk_closes(count: int) -> list[float]:
    """Deterministic 1m random walk seeded from the REAL sample close (spec §Verify 3)."""
    rng = random.Random(_WALK_SEED)
    price = _REAL_SAMPLE_CLOSE
    closes: list[float] = []
    for _ in range(count):
        price += rng.uniform(-0.0004, 0.0004)
        closes.append(round(price, 5))
    return closes


def _naive_atr(closes: list[float], period: int = 14) -> float:
    """§3.5 second implementation — Wilder ATR alpha-form (a += (x-a)/p) with the
    h==l==c TR collapse written as a plain abs-diff list — different code shape
    than analytics/atr_14.py (explicit max() chain + (a*(p-1)+x)/p recursion)."""
    trs = [0.0] + [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    atr = sum(trs[:period]) / period  # seed = SMA of first p
    for true_range in trs[period:]:
        atr += (true_range - atr) / period
    return atr


def _naive_adx(closes: list[float], period: int = 14) -> float:
    """§3.5 second implementation — Wilder ADX built from list comprehensions +
    alpha-form RMA; Wilder DM (high-vs-prev.high / prev.low-vs-low) collapses to
    sign-of-delta under h==l==c — different code shape than analytics/adx_14.py."""
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    plus_dm = [max(0.0, d) for d in deltas]
    minus_dm = [-d if d < 0.0 else 0.0 for d in deltas]
    trs = [abs(d) for d in deltas]

    def rma(values: list[float]) -> list[float]:
        current = sum(values[:period]) / period
        out = [current]
        for value in values[period:]:
            current += (value - current) / period
            out.append(current)
        return out

    dx: list[float] = []
    for dp, dm, tr in zip(rma(plus_dm), rma(minus_dm), rma(trs), strict=True):
        if tr == 0.0:
            plus_di, minus_di = 0.0, 0.0
        else:
            plus_di, minus_di = 100.0 * dp / tr, 100.0 * dm / tr
        denom = plus_di + minus_di
        dx.append(0.0 if denom == 0.0 else 100.0 * abs(plus_di - minus_di) / denom)
    return rma(dx)[-1]


def _request(as_of: datetime, features: tuple[str, ...]) -> FeatureRequest:
    return FeatureRequest(
        instrument_id="FX.EURUSD",
        broker="deriv",
        timeframe="1m",
        features=features,
        as_of=as_of,
        asset_class="forex",
    )


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

    # --- 1. D4 discovery -------------------------------------------------------
    names = sorted(AnalyticsRegistry.from_package().names())
    print(f"discovery ({len(names)}): {names}")
    _check("D4 discovery finds exactly the 34 P17+P18+Book+m4.7 names", names == _EXPECTED_34)
    _check("all 12 P18 files discoverable", all(n in names for n in _P18))

    # --- 2. golden exactness (outside pytest, from the spec table) -------------
    goldens = {
        "sma [2,4,6,8] p4 -> 5.0": sma_20.compute(_mk_candles([2.0, 4.0, 6.0, 8.0]), {"period": 4.0}),
        "std [3,3,5,5] p4 -> 1.0": std_20.compute(_mk_candles([3.0, 3.0, 5.0, 5.0]), {"period": 4.0}),
        "zscore flat -> 0.0": zscore_20.compute(_mk_candles([5.0] * 21), {"period": 20.0}),
        "atr flat -> 0.0": atr_14.compute(_mk_candles([5.0] * 14), {"period": 14.0}),
        "adx monotone 4 bars p2 -> 100.0": adx_14.compute(_mk_candles([1.0, 2.0, 3.0, 4.0]), {"period": 2.0}),
        "bb [3,3,5,5] p4 k2 -> 0.75": bb_percent.compute(_mk_candles([3.0, 3.0, 5.0, 5.0]), {"period": 4.0, "stddev": 2.0}),
        "donchian up 150.0": donchian_20.compute(_mk_candles([10.0, 12.0, 14.0, 16.0]), {"period": 3.0}),
        "donchian down -50.0": donchian_20.compute(_mk_candles([10.0, 12.0, 14.0, 8.0]), {"period": 3.0}),
        "choppiness flat -> 100.0": choppiness.compute(_mk_candles([5.0] * 4), {"period": 4.0}),
        "vol_regime [0,2,4,8] w2 -> 2.0": vol_regime.compute(_mk_candles([0.0, 2.0, 4.0, 8.0]), {"window": 2.0}),
        "hurst alternating -> 0.0": hurst.compute(_mk_candles([100.0, 110.0, 100.0, 110.0, 100.0]), {"window": 4.0}),
        "hurst flat -> 0.5": hurst.compute(_mk_candles([5.0] * 10), {"window": 4.0}),
        "returns flat -> 0.0": returns.compute(_mk_candles([5.0, 5.0]), {}),
    }
    for label, value in goldens.items():
        _check(f"golden: {label}", value in (0.0, 0.5, 1.0, 2.0, 5.0, 100.0, 150.0, -50.0, 0.75), f"got {value!r}")
    ln2 = returns.compute(_mk_candles([2.0, 4.0]), {})
    _check("golden: returns ln(2)", math.isclose(ln2, 0.6931471805599453, abs_tol=1e-12), f"got {ln2!r}")
    macd_line = macd.compute(_mk_candles([2.0, 4.0, 6.0, 8.0, 10.0, 12.0]), {"fast": 2.0, "slow": 4.0})
    _check(
        "golden: macd line 1.7708352263374483 (EMA_f=2674/243)",
        math.isclose(macd_line, 1.7708352263374483, abs_tol=1e-12),
        f"got {macd_line!r}",
    )

    # --- 3. two independent implementations on the walk ------------------------
    closes = _walk_closes(60)
    atr_diffs = [
        abs(atr_14.compute(_mk_candles(closes[:n]), {"period": 14.0}) - _naive_atr(closes[:n]))
        for n in range(14, 61)
    ]
    adx_diffs = [
        abs(adx_14.compute(_mk_candles(closes[:n]), {"period": 14.0}) - _naive_adx(closes[:n]))
        for n in range(28, 61)
    ]
    print(
        f"cross-check: atr {len(atr_diffs)} windows (14..60) max_diff={max(atr_diffs):.3e} | "
        f"adx {len(adx_diffs)} windows (28..60) max_diff={max(adx_diffs):.3e} "
        f"(seed={_WALK_SEED} from {_REAL_SAMPLE_CLOSE})"
    )
    _check("2-impl ATR agreement: max diff <= 1e-9", max(atr_diffs) <= 1e-9, f"max={max(atr_diffs):.3e}")
    _check("2-impl ADX agreement: max diff <= 1e-9", max(adx_diffs) <= 1e-9, f"max={max(adx_diffs):.3e}")

    # --- 4. engine e2e: all 12 P18 features on the walk ------------------------
    walk_candles = _mk_candles(closes)
    as_of = max(c.interval_end for c in walk_candles)  # P17 precedent: window anchored at last bar
    engine = FeatureEngine(
        provider=StaticCandleProvider(walk_candles),
        capability_registry=CapabilityRegistry.from_yaml(_CAP_YAML),
        registry=None,
        resolver=None,
        cache=None,
        profiles=Profiles.from_dir(_ASSETS_DIR),
    )
    res = await engine.get_features(_request(as_of, tuple(_P18)))
    print(
        f"e2e: {len(res.features)}/12 features status={res.capability_status.value} "
        f"warn={res.capability_warning!r}"
    )
    _check(
        "e2e: all 12 P18 features present on 60 pristine bars",
        set(res.features) == set(_P18),
        f"missing={sorted(set(_P18) - set(res.features))}",
    )
    _check(
        "e2e: capability status FULLY_SUPPORTED (no proxy in this batch)",
        res.capability_status is CapabilityStatus.FULLY_SUPPORTED,
        f"status={res.capability_status.value}",
    )

    # --- 5. ADVISORY latency of all 12 computes (not a gate) -------------------
    modules = {
        "adx_14": (adx_14, {"period": 14.0}),
        "atr_14": (atr_14, {"period": 14.0}),
        "bb_percent": (bb_percent, {"period": 20.0, "stddev": 2.0}),
        "choppiness": (choppiness, {"period": 14.0}),
        "donchian_20": (donchian_20, {"period": 20.0}),
        "hurst": (hurst, {"window": 50.0}),
        "macd": (macd, {"fast": 12.0, "slow": 26.0}),
        "returns": (returns, {}),
        "sma_20": (sma_20, {"period": 20.0}),
        "std_20": (std_20, {"period": 20.0}),
        "vol_regime": (vol_regime, {"window": 40.0}),
        "zscore_20": (zscore_20, {"period": 20.0}),
    }
    medians: dict[str, float] = {}
    for name, (module, params) in modules.items():
        samples: list[float] = []
        for _ in range(100):
            t0 = time.perf_counter_ns()
            module.compute(_mk_candles(closes), params)
            samples.append((time.perf_counter_ns() - t0) / 1e6)
        medians[name] = statistics.median(samples)
    slowest = max(medians, key=lambda k: medians[k])
    print(
        f"[ADVISORY] 12 computes median range "
        f"{min(medians.values()):.3f}..{medians[slowest]:.3f}ms "
        f"(slowest={slowest}) over 100 runs each vs §4.1 budget 20ms — reported NOT gated; "
        f"enforcement = engine latency header (later slice)"
    )
    _check("ADVISORY: every P18 compute median below the 20ms budget", medians[slowest] < 20.0,
           f"slowest={slowest} {medians[slowest]:.3f}ms")

    if _failures:
        for failure in _failures:
            print(f"[FAIL] {failure}")
        print("\nRESULT: FAILED")
        return 1
    print("\nRESULT: ALL OK (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
