"""WHY: M3.1 Provider Port Step 7 — real-data verification of Temporal Resolver +
PIT candle retrieval against LIVE Timescale + today's REAL BTC 1m collector bars.

V1 resolver window math on a real as_of (2d duration beats static bars, exact union)
V2 PIT look-ahead proof: as_of=candles[100].interval_end -> exactly bars 0..100, none future
V3 full 200-bar range read: count, strictly ascending, exact span
V4 unknown instrument -> [] (empty is valid, no crash)
V5 StaticCandleProvider == PITCandleProvider byte-parity (same window, same rows)
V6 FeatureEngine E2E: PIT-path features == static-path features on real BTC bars (rsi_14)

Exit 0 = ALL OK; 1 = failure (printed reason); 2 = data/db unavailable.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import asyncpg

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
sys.path.insert(0, str(APP))

_v210spec = importlib.util.spec_from_file_location(
    "v210", APP / "scripts" / "verify_m2.10_warm.py"
)
assert _v210spec is not None and _v210spec.loader is not None
v210 = importlib.util.module_from_spec(_v210spec)
_v210spec.loader.exec_module(v210)

from modules.m2_market_data.warm import Candles1mStore, WarmConfig
from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.feature_engine import (
    FeatureEngine,
    FeatureRequest,
    StaticCandleProvider,
)
from modules.m3_analytics.engine.pit_provider import PITCandleProvider
from modules.m3_analytics.engine.profiles import Profiles
from shared.contracts.market_data import Candle
from shared.temporal.temporal_resolver import TemporalRequirement, resolve

PROVIDER = "deriv"  # Day-1: provider name == broker name (identity seam, spec P6)
INSTRUMENT = "CRYPTO.BTCUSD"

_fails = 0


def ok(cond: bool, label: str, detail: str = "") -> bool:
    global _fails
    suffix = f" — {detail}" if detail else ""
    print(f"[{'OK' if cond else 'FAIL'}] {label}{suffix}")
    if not cond:
        _fails += 1
    return cond


def _load_history() -> list[Candle] | None:
    paths = sorted((APP / "data" / "live_signals").glob("*/history_1m.jsonl"))
    if not paths:
        return None
    candles: list[Candle] = []
    for line in paths[-1].read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        candles.append(
            Candle(
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                tick_count=row.get("tick_count", 0),
                interval="1m",
                interval_start=datetime.fromisoformat(row["interval_start"]),
                interval_end=datetime.fromisoformat(row["interval_end"]),
            )
        )
    print(f"[data] {paths[-1].relative_to(APP)}: {len(candles)} real BTC 1m bars")
    return candles if len(candles) >= 101 else None


async def main() -> int:
    candles = _load_history()
    if candles is None:
        print("[FAIL] V0 real history unavailable (<101 bars)")
        return 2

    dsn = f"postgresql://quantrader:{v210._password()}@127.0.0.1:5432/quantrader"
    try:
        conn = await asyncpg.connect(dsn, timeout=10)
    except (OSError, TimeoutError, asyncpg.PostgresError) as exc:
        print(f"[FAIL] V0 db unavailable: {type(exc).__name__}")
        return 2
    store = Candles1mStore(WarmConfig(dsn=dsn, connect_timeout_s=5), pool=conn)
    report = await store.flush_batch(
        provider=PROVIDER, instrument_id=INSTRUMENT, candles=candles
    )
    print(f"[seed] flush_batch upserted {len(candles)} bars ({report})")

    # ---- V1: resolver window math on a real as_of -------------------------------
    as_of = candles[150].interval_end
    res = resolve(
        requirements=(
            TemporalRequirement(minimum_history=50, warmup=0),
            TemporalRequirement(minimum_history=5, warmup=10, history_duration="2d"),
        ),
        as_of=as_of,
        timeframe="1m",
    )
    ok(res.needed_bars == 2880, "V1 union: 2d(2880) beats static 15 bars",
       f"needed={res.needed_bars}")
    ok(
        res.window_end == as_of
        and res.window_start == as_of - timedelta(minutes=2880),
        "V1 window bounds exact",
        f"{res.window_start.isoformat()} -> {res.window_end.isoformat()}",
    )

    pit = PITCandleProvider(store)

    # ---- V2: look-ahead proof (as_of cuts the future) ---------------------------
    as_of2 = candles[100].interval_end
    rows2 = await pit.get_candles(
        INSTRUMENT, "1m", candles[0].interval_start, as_of2, as_of2, PROVIDER
    )
    ok(len(rows2) == 101, "V2 exactly bars 0..100 (bar 101 start==as_of excluded)",
       f"got {len(rows2)}")
    ok(
        all(c.interval_end <= as_of2 for c in rows2),
        "V2 no row available after as_of (look-ahead proof)",
        f"max_end={max(c.interval_end for c in rows2).isoformat()}",
    )
    ok(
        all(c.interval_start >= candles[0].interval_start for c in rows2),
        "V2 window_start respected",
    )

    # ---- V3: full range read, ascending, exact span -----------------------------
    ws3, we3 = candles[0].interval_start, candles[-1].interval_end
    rows3 = await pit.get_candles(INSTRUMENT, "1m", ws3, we3, we3, PROVIDER)
    ok(len(rows3) == 200, "V3 full history = 200 rows", f"got {len(rows3)}")
    ok(
        len(rows3) == 200
        and all(
            rows3[i].interval_start < rows3[i + 1].interval_start
            for i in range(len(rows3) - 1)
        ),
        "V3 strictly ascending (chronological law)",
    )
    ok(
        bool(rows3)
        and rows3[0].interval_start == ws3
        and rows3[-1].interval_end == we3,
        "V3 exact span",
        f"{rows3[0].interval_start.isoformat()} .. {rows3[-1].interval_end.isoformat()}",
    )

    # ---- V4: empty is valid -----------------------------------------------------
    empty = await pit.get_candles("CRYPTO.NOSUCH", "1m", ws3, we3, we3, PROVIDER)
    ok(empty == [], "V4 unknown instrument -> [] (no crash)", f"got {len(empty)}")

    # ---- V5: static == PIT byte-parity ------------------------------------------
    static = StaticCandleProvider(candles)
    rows5 = await static.get_candles(INSTRUMENT, "1m", ws3, we3, we3, PROVIDER)
    ok(rows5 == rows3, "V5 Static == PIT byte-parity",
       f"static={len(rows5)} pit={len(rows3)}")

    # ---- V6: engine E2E — PIT path == static path on real bars ------------------
    capability = CapabilityRegistry.from_yaml(APP / "config" / "capabilities.yaml")
    profiles = Profiles.from_dir(APP / "config" / "assets")
    request = FeatureRequest(
        instrument_id=INSTRUMENT,
        broker=PROVIDER,
        timeframe="1m",
        features=("rsi_14",),
        as_of=as_of,
        asset_class="crypto",
        param_overrides=None,
    )
    eng_pit = FeatureEngine(
        provider=pit, capability_registry=capability, profiles=profiles
    )
    eng_static = FeatureEngine(
        provider=static, capability_registry=capability, profiles=profiles
    )
    r_pit = await eng_pit.get_features(request)
    r_static = await eng_static.get_features(request)
    ok(
        r_pit.features == r_static.features,
        "V6 PIT-engine features == static-engine features (rsi_14)",
        f"pit={r_pit.features} static={r_static.features}",
    )
    ok(
        all(isinstance(v, float) for v in r_pit.features.values()),
        "V6 feature values are floats (real RSI on real BTC bars)",
        str(r_pit.features),
    )

    await conn.close()
    print(f"\n{'ALL OK — EXIT 0' if _fails == 0 else f'{_fails} FAIL(S) — EXIT 1'}")
    return 0 if _fails == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
