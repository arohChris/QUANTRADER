"""WHY: M3.2 Step 7 — real-data verification of the 10 Book Feature Library
batch-1 analytics (spec docs/specs/m3.2_book_feature_library.spec.md §9).
NOT unit tests: (1) D4 discovery == 34 (24 P17+P18+Book + 10 m4.7 — declared
amendment 2, m4.7_playbook_tier1 spec); (2) every §5
golden re-printed OUTSIDE pytest; (3) engine e2e over a 300-bar walk seeded from
the REAL sample close with the shipped forex profile — all 10 present,
FULLY_SUPPORTED; (4) timeframe-consistency proof: ONE FeatureRequest (timeframe +
as_of declared once) -> exactly ONE provider call / one candle window carrying
all 10 values; (5) real-sample run on data/sample_ticks_realistic.jsonl (M2.4
output) printing values for human eyeball — the 10-tick sample is thin, so the
honest output is anchored_twap present + exactly 9 INSUFFICIENT warnings, and
that is asserted, not papered over.

Usage: python scripts/verify_m3.2_book_feature_library.py
Exit: 0 = all checks OK · 1 = any check failed.
"""
from __future__ import annotations

import asyncio
import json
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.m3_analytics.analytics import (
    anchored_twap,
    atr_percentile_rank,
    bollinger_bandwidth,
    bollinger_bandwidth_percentile,
    efficiency_ratio,
    market_state,
    ols_r_squared,
    swing_high,
    swing_low,
    variance_ratio,
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
_REAL_SAMPLE = ROOT / "data" / "sample_ticks_realistic.jsonl"
_WALK_SEED = 20260925
_REAL_SAMPLE_CLOSE = 1.08548  # from data/sample_ticks_realistic.jsonl (M2.4 output)

_BOOK10 = [
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
]
# Same 34-name lock as the discovery tests and verify_m3.2_{p18,analytics}.py.
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

_failures: list[str] = []


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(label)
    return ok


def _mk(closes: list[float], highs: list[float] | None = None, lows: list[float] | None = None) -> list[Candle]:
    """1m candles for direct computes. highs/lows default to close (h==l==c);
    WHY no tick_count: Candle 2.0.0 removed the field (extra=forbid)."""
    end = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)  # tz-aware: as_of validator
    if highs is None:
        highs = closes
    if lows is None:
        lows = closes
    out = []
    for i, close in enumerate(closes):
        start = end - timedelta(minutes=len(closes) - i)
        out.append(
            Candle(
                open=close,
                high=highs[i],
                low=lows[i],
                close=close,
                interval="1m",
                interval_start=start,
                interval_end=start + timedelta(minutes=1),
            )
        )
    return out


def _walk_closes(count: int) -> list[float]:
    """Deterministic 1m random walk seeded from the REAL sample close (spec §9.3)."""
    import random

    rng = random.Random(_WALK_SEED)
    price = _REAL_SAMPLE_CLOSE
    closes: list[float] = []
    for _ in range(count):
        price += rng.uniform(-0.0004, 0.0004)
        closes.append(round(price, 5))
    return closes


class _CountingProvider:
    """CandleProvider protocol wrapper recording every get_candles call —
    §9(4) timeframe-consistency proof (exactly ONE provider call per request)."""

    def __init__(self, inner: StaticCandleProvider) -> None:
        self._inner = inner
        self.calls: list[tuple] = []

    async def get_candles(
        self,
        instrument_id: str,
        timeframe: str,
        window_start: datetime,
        window_end: datetime,
        as_of: datetime,
        broker: str,
    ) -> list[Candle]:
        self.calls.append((instrument_id, timeframe, window_start, window_end, as_of, broker))
        return await self._inner.get_candles(
            instrument_id, timeframe, window_start, window_end, as_of, broker
        )


def _request(as_of: datetime, features: tuple[str, ...]) -> FeatureRequest:
    return FeatureRequest(
        instrument_id="FX.EURUSD",
        broker="deriv",
        timeframe="1m",
        features=features,
        as_of=as_of,
        asset_class="forex",
    )


def _engine(provider: _CountingProvider | StaticCandleProvider) -> FeatureEngine:
    return FeatureEngine(
        provider=provider,
        capability_registry=CapabilityRegistry.from_yaml(_CAP_YAML),
        registry=None,
        resolver=None,
        cache=None,
        profiles=Profiles.from_dir(_ASSETS_DIR),
    )


def _load_real_candles() -> tuple[list[Candle], int]:
    """M2.4 sample ticks -> 1m candles (o/h/l/c from quote per minute bucket).
    Returns (candles, non_tick_frames_skipped) — a real Deriv dump also carries
    the `subscription` confirm frame, which holds no price and is skipped."""
    by_minute: dict[int, list[float]] = {}
    skipped = 0
    with _REAL_SAMPLE.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if "tick" not in payload:
                skipped += 1
                continue
            tick = payload["tick"]
            minute = int(tick["epoch"]) // 60
            by_minute.setdefault(minute, []).append(float(tick["quote"]))
    out: list[Candle] = []
    for minute in sorted(by_minute):
        quotes = by_minute[minute]
        start = datetime.fromtimestamp(minute * 60, tz=UTC)
        out.append(
            Candle(
                open=quotes[0],
                high=max(quotes),
                low=min(quotes),
                close=quotes[-1],
                interval="1m",
                interval_start=start,
                interval_end=start + timedelta(minutes=1),
            )
        )
    return out, skipped


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

    # --- 1. D4 discovery -------------------------------------------------------
    names = sorted(AnalyticsRegistry.from_package().names())
    print(f"discovery ({len(names)}): {names}")
    _check("D4 discovery finds exactly the 34 P17+P18+Book+m4.7 names", names == _EXPECTED_34)
    _check("all 10 Book batch-1 analytics discoverable", all(n in names for n in _BOOK10))

    # --- 2. §5 goldens re-printed OUTSIDE pytest -------------------------------
    # ER hand calc (§5.1): net |105-100|=5, path 1+2+1+3=7 -> 5/7
    er = efficiency_ratio.compute(_mk([100.0, 101.0, 103.0, 102.0, 105.0]), {"period": 4.0})
    _check("golden: ER [100,101,103,102,105] p4 -> 5/7", er == 0.7142857142857143, f"got {er!r}")
    _check(
        "golden: ER one-way [100,101,103,105] p3 -> 1.0",
        efficiency_ratio.compute(_mk([100.0, 101.0, 103.0, 105.0]), {"period": 3.0}) == 1.0,
    )
    _check(
        "golden: ER flat -> 0.0 (no movement = no efficiency)",
        efficiency_ratio.compute(_mk([5.0] * 10), {"period": 4.0}) == 0.0,
    )

    # ATR-rank hand calc (§5.2): TRs [10,11,14,12,13,9,8] -> ATRs
    # [11.75, 12.0625, 11.296875, 10.47265625]; #{v<=cur}=1 of window 4 -> 25.0
    atr_rank = atr_percentile_rank.compute(
        _mk(
            [5.0, 10.5, 17.0, 21.0, 26.5, 29.5, 32.0],
            highs=[10.0, 16.0, 24.0, 27.0, 33.0, 34.0, 36.0],
            lows=[0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 28.0],
        ),
        {"period": 4.0, "window": 4.0},
    )
    _check("golden: atr_percentile_rank fixture -> 25.0", atr_rank == 25.0, f"got {atr_rank!r}")
    # constant TRs (l = prior close, h = l+10) -> every ATR equal -> 100.0 (TV ta.percentrank)
    atr_const = atr_percentile_rank.compute(
        _mk([10.0, 15.0, 20.0, 25.0], highs=[20.0, 20.0, 25.0, 30.0], lows=[10.0, 10.0, 15.0, 20.0]),
        {"period": 2.0, "window": 2.0},
    )
    _check("golden: atr_percentile_rank constant TRs -> 100.0", atr_const == 100.0, f"got {atr_const!r}")

    # OLS hand calc (§5.3): means 1.5/2.5, Sxy=4, Sxx=5, Syy=5 -> 16/25 = 0.64
    r2 = ols_r_squared.compute(_mk([1.0, 3.0, 2.0, 4.0]), {"period": 4.0})
    _check("golden: OLS R2 y=[1,3,2,4] p4 -> 0.64", r2 == 0.64, f"got {r2!r}")
    _check(
        "golden: OLS R2 monotone y=[1,2,3,4] -> 1.0",
        ols_r_squared.compute(_mk([1.0, 2.0, 3.0, 4.0]), {"period": 4.0}) == 1.0,
    )
    _check(
        "golden: OLS R2 flat -> 0.0 (Syy==0, gate-safe declared §6 D5)",
        ols_r_squared.compute(_mk([5.0] * 6), {"period": 6.0}) == 0.0,
    )

    # VR hand calc (§5.4): r1 = +/-ln(1.1) alternating, Var(r1) = 0.008720669159359434,
    # rq = [0,0,0,0] -> Var(rq)=0 -> 0.0
    vr = variance_ratio.compute(_mk([10.0, 11.0, 10.0, 11.0, 10.0, 11.0]), {"period": 2.0})
    _check("golden: VR [10,11,...] q2 -> 0.0", vr == 0.0, f"got {vr!r}")
    _check(
        "golden: VR flat -> 1.0 (Var(r1)==0 declared §6 D7)",
        variance_ratio.compute(_mk([5.0] * 8), {"period": 5.0}) == 1.0,
    )

    # Swing hand calcs (§5.5/5.6): j=3 strict max of [12,11,13,9,8] -> 13.0;
    # first 4 bars -> NaN (no confirmable pivot); lows [10,7,8,5,11,12] -> 5.0
    swh = swing_high.compute(_mk([0.0] * 6, highs=[10.0, 12.0, 11.0, 13.0, 9.0, 8.0], lows=[9.0] * 6), {"lookback": 2.0})
    _check("golden: swing_high highs [10,12,11,13,9,8] k2 -> 13.0", swh == 13.0, f"got {swh!r}")
    swh4 = swing_high.compute(_mk([0.0] * 4, highs=[10.0, 12.0, 11.0, 13.0], lows=[9.0] * 4), {"lookback": 2.0})
    _check("golden: swing_high 4 bars -> NaN (confirmation lag, no look-ahead)", math.isnan(swh4))
    swl = swing_low.compute(_mk([0.0] * 6, highs=[11.0, 8.0, 9.0, 6.0, 12.0, 13.0], lows=[10.0, 7.0, 8.0, 5.0, 11.0, 12.0]), {"lookback": 2.0})
    _check("golden: swing_low lows [10,7,8,5,11,12] k2 -> 5.0", swl == 5.0, f"got {swl!r}")

    # market_state hand calcs (§5.7): H=[12,15] HH + L=[7,8] HL -> +1.0;
    # H=[15,13] LH + L=[6,5] LL -> -1.0; HH + LL mixed -> 0.0
    bull_h, bull_l = [10.0, 11.0, 12.0, 11.0, 10.0, 15.0, 14.0, 13.0], [9.0, 8.0, 7.0, 9.0, 9.0, 8.0, 9.0, 10.0]
    bear_h, bear_l = [10.0, 14.0, 15.0, 12.0, 11.0, 13.0, 10.0, 9.0], [9.0, 7.0, 6.0, 8.0, 7.0, 5.0, 7.0, 8.0]
    ms_bull = market_state.compute(_mk([0.0] * 8, highs=bull_h, lows=bull_l), {"lookback": 2.0})
    ms_bear = market_state.compute(_mk([0.0] * 8, highs=bear_h, lows=bear_l), {"lookback": 2.0})
    ms_chop = market_state.compute(_mk([0.0] * 8, highs=bull_h, lows=bear_l), {"lookback": 2.0})
    _check("golden: market_state HH/HL bull -> +1.0", ms_bull == 1.0, f"got {ms_bull!r}")
    _check("golden: market_state LH/LL bear -> -1.0", ms_bear == -1.0, f"got {ms_bear!r}")
    _check("golden: market_state mixed -> 0.0 (chop)", ms_chop == 0.0, f"got {ms_chop!r}")

    # BW hand calc (§5.8): mean 3, pop var 2, sigma sqrt(2) -> 4*sqrt(2)/3
    bw = bollinger_bandwidth.compute(_mk([1.0, 2.0, 3.0, 4.0, 5.0]), {"period": 5.0, "stddev": 2.0})
    _check(
        "golden: BW [1..5] p5 k2 -> 4*sqrt(2)/3",
        math.isclose(bw, 1.885618083164127, abs_tol=1e-12),
        f"got {bw!r}",
    )
    _check(
        "golden: BW flat -> 0.0 exactly (sigma=0)",
        bollinger_bandwidth.compute(_mk([5.0] * 5), {"period": 5.0, "stddev": 2.0}) == 0.0,
    )
    _check(
        "golden: BW mid==0 [2,-2,2,-2] -> NaN (division guard §6 D8)",
        math.isnan(bollinger_bandwidth.compute(_mk([2.0, -2.0, 2.0, -2.0]), {"period": 4.0, "stddev": 2.0})),
    )

    # BWpl hand calc (§5.9): bandwidths [2/11,2/11,1/10.5,1/10.5], #{<=cur}=2 of 4 -> 50.0
    bwpl = bollinger_bandwidth_percentile.compute(
        _mk([10.0, 12.0, 10.0, 11.0, 10.0]),
        {"period": 2.0, "stddev": 1.0, "lookback": 4.0},
    )
    _check("golden: BWpl [10,12,10,11,10] p2 k1 lb4 -> 50.0", bwpl == 50.0, f"got {bwpl!r}")

    # TWAP hand calc (§5.10): (10+11+12)/3 = 11.0 (dyadic, exact); unknown param inert
    tw = anchored_twap.compute(_mk([10.0, 11.0, 12.0]), {})
    _check("golden: TWAP [10,11,12] -> 11.0", tw == 11.0, f"got {tw!r}")
    tw_bad = anchored_twap.compute(_mk([10.0, 11.0, 12.0]), {"period": -5.0})
    _check("golden: TWAP ignores unknown param {period:-5} -> 11.0", tw_bad == 11.0, f"got {tw_bad!r}")

    # --- 3+4. engine e2e + ONE-Request/ONE-call timeframe proof ---------------
    # 300 bars: the union window needs atr_percentile_rank's 263-bar minimum.
    closes = _walk_closes(300)
    walk_candles = _mk(closes)
    as_of = max(c.interval_end for c in walk_candles)  # window anchored at last bar
    counting = _CountingProvider(StaticCandleProvider(walk_candles))
    request = _request(as_of, tuple(_BOOK10))  # timeframe + as_of declared ONCE
    res = await _engine(counting).get_features(request)
    print(
        f"e2e: {len(res.features)}/10 features status={res.capability_status.value} "
        f"warn={res.capability_warning!r}"
    )
    print(f"e2e values: {dict(sorted(res.features.items()))}")
    _check(
        "e2e: all 10 Book features present on the 300-bar walk",
        set(res.features) == set(_BOOK10),
        f"missing={sorted(set(_BOOK10) - set(res.features))}",
    )
    _check(
        "e2e: capability status FULLY_SUPPORTED (all price-only, requires [price])",
        res.capability_status is CapabilityStatus.FULLY_SUPPORTED,
        f"status={res.capability_status.value}",
    )
    _check(
        "e2e: no INSUFFICIENT/invalid warnings (window union covers every minimum)",
        "INSUFFICIENT" not in (res.capability_warning or "")
        and "invalid" not in (res.capability_warning or ""),
        f"warn={res.capability_warning!r}",
    )
    # §9(4) timeframe-consistency proof: ONE request -> exactly ONE provider call
    # (feature_engine.py resolves ONE window union over the closure, then fetches once).
    _check("timeframe proof: exactly ONE provider call for the whole request", len(counting.calls) == 1,
           f"calls={len(counting.calls)}")
    if counting.calls:
        _instrument_id, tf, w_start, w_end, req_as_of, _broker = counting.calls[0]
        print(
            f"timeframe proof: window {w_start.isoformat()} .. {w_end.isoformat()} "
            f"timeframe={tf} bars={w_end - w_start} request_as_of={req_as_of.isoformat()}"
        )
        _check(
            "timeframe proof: single window carries timeframe=1m declared by the request",
            tf == "1m" and req_as_of == as_of,
            f"timeframe={tf!r}",
        )

    # --- 5. real-sample run (M2.4 tick dump -> 1m candles) ---------------------
    if not _REAL_SAMPLE.exists():
        _check("real sample data/sample_ticks_realistic.jsonl exists", False, "MISSING")
    else:
        real_candles, skipped_frames = _load_real_candles()
        real_as_of = max(c.interval_end for c in real_candles)
        total_lines = sum(1 for _ in _REAL_SAMPLE.open(encoding="utf-8"))
        print(
            f"real sample: {total_lines - skipped_frames} ticks ({skipped_frames} non-tick frame(s) skipped) "
            f"-> {len(real_candles)} 1m candle(s) from {_REAL_SAMPLE.name}, as_of={real_as_of.isoformat()}"
        )
        real_res = await _engine(StaticCandleProvider(real_candles)).get_features(
            _request(real_as_of, tuple(_BOOK10))
        )
        print(f"real values: {dict(sorted(real_res.features.items()))}")
        print(f"real warn: {real_res.capability_warning!r}")
        _check(
            "real: anchored_twap present and == the real candle close (human eyeball)",
            real_res.features.get("anchored_twap") == real_candles[-1].close,
            f"twap={real_res.features.get('anchored_twap')!r} close={real_candles[-1].close!r}",
        )
        insufficient = {
            name.removeprefix("INSUFFICIENT:")
            for name in (real_res.capability_warning or "").split("; ")
            if name.startswith("INSUFFICIENT:")
        }
        _check(
            "real: exactly the 9 min>1 analytics report INSUFFICIENT (thin sample, honest)",
            insufficient == set(_BOOK10) - {"anchored_twap"},
            f"insufficient={sorted(insufficient)}",
        )
        _check(
            "real: capability status still FULLY_SUPPORTED (proxy is about data, not thinness)",
            real_res.capability_status is CapabilityStatus.FULLY_SUPPORTED,
            f"status={real_res.capability_status.value}",
        )

    if _failures:
        for failure in _failures:
            print(f"[FAIL] {failure}")
        print("\nRESULT: FAILED")
        return 1
    print("\nRESULT: ALL OK (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
