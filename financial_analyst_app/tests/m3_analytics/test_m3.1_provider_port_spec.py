"""
M3.1 Provider Port (Temporal Resolver + PIT retrieval) — Spec tests (WRITTEN BEFORE CODE).
WHY: AGENTS.md §2 Step 3 — tests first against docs/specs/m3.1_provider_port.spec.md
(ACTION 67 "lgtm" 2026-09-26).

Direct impl import ONLY — no importorskip. Collection RED until Step 5
(honest ModuleNotFoundError: shared.temporal / pit_provider; house style per
test_m3.1_spec.py); after impl, 100% pass, 0 skips (§2 Step 6).

Covers spec §Test plan: resolver E1-E9 + H2 (11 fns), provider P1-P9 (9 fns),
adversarial X1-X3 (3 fns), engine delegation/integration (2 fns) = 25.
Hand-calc comments H1 (864@5m) / H2 (2200h@4h) / H3 (1min@1m) inline (no money
path -> no Decimal). No shared/contracts change -> no bump (spec §Contracts).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.analytics_registry import (
    AnalyticsRegistry,
    DataRequirement,
)
from modules.m3_analytics.engine.feature_engine import FeatureEngine, FeatureRequest
from modules.m3_analytics.engine.pit_provider import PITCandleProvider
from modules.m3_analytics.engine.profiles import Profiles
from shared.contracts.market_data import Candle
from shared.temporal.temporal_resolver import (
    TemporalRequirement,
    TemporalResolution,
    resolve,
)

_ROOT = Path(__file__).resolve().parents[2]
_CAP_YAML = _ROOT / "config" / "capabilities.yaml"
_ASSETS_DIR = _ROOT / "config" / "assets"
_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _req(
    min_history: int = 10,
    warmup: int = 0,
    history_duration: str | None = None,
) -> TemporalRequirement:
    return TemporalRequirement(
        minimum_history=min_history,
        warmup=warmup,
        history_duration=history_duration,
    )


# --- resolver edges E1-E9 + H2 (spec §Test plan) ---


def test_resolver_empty_requirements_rejected():
    # E1: no requirement -> no window; fail loud, never a zero-span default.
    with pytest.raises(ValueError):
        resolve(requirements=(), as_of=_T0, timeframe="1m")


def test_resolver_naive_as_of_rejected():
    # E2a: naive datetime rejected (Time law — no silent UTC).
    with pytest.raises(ValueError):
        resolve(
            requirements=(_req(),),
            as_of=datetime(2026, 1, 1, 12, 0),  # noqa: DTZ001 — WHY naive: the rejection case
            timeframe="1m",
        )


def test_resolver_unknown_timeframe_rejected():
    # E2b: calendar buckets / off-registry strings are not resolvable spans.
    for tf in ("7m", "1w", "1mo"):
        with pytest.raises(ValueError):
            resolve(requirements=(_req(),), as_of=_T0, timeframe=tf)


def test_resolver_invalid_history_duration_rejected():
    # E3: fullmatch (\d+)([mhd]) + nonzero — parity with the old engine parser.
    for bad in ("0d", "3w", "abc", "-1h"):
        with pytest.raises(ValueError):
            resolve(
                requirements=(_req(history_duration=bad),),
                as_of=_T0,
                timeframe="5m",
            )


def test_resolver_bars_only_span_exact():
    # E4: bars = minimum_history + warmup; window_end = as_of; span = bars * tf.
    res = resolve(requirements=(_req(20, 5),), as_of=_T0, timeframe="5m")
    assert res.needed_bars == 25
    assert res.window_end == _T0
    assert res.window_start == _T0 - timedelta(minutes=125)  # 25 * 5 = 125 min
    assert res.window_end - res.window_start == timedelta(minutes=125)


def test_resolver_duration_ceil_3d_exact_at_5m_and_15m():
    # E5a + H1 hand calc: ceil(3d / 5m) = ceil(4320 / 5) = 864 bars ->
    # window span 864 * 5 = 4320 min = 3d exactly. At 15m: ceil(4320/15) = 288.
    res5 = resolve(requirements=(_req(1, history_duration="3d"),), as_of=_T0, timeframe="5m")
    assert res5.needed_bars == 864
    assert res5.window_end - res5.window_start == timedelta(days=3)
    res15 = resolve(requirements=(_req(1, history_duration="3d"),), as_of=_T0, timeframe="15m")
    assert res15.needed_bars == 288
    assert res15.window_end - res15.window_start == timedelta(days=3)


def test_resolver_duration_1d_at_24h_exactly_one_bar():
    # E5b: 1d of 24h bars = 1 bar (integer ceil, no fractional bar).
    res = resolve(requirements=(_req(1, history_duration="1d"),), as_of=_T0, timeframe="24h")
    assert res.needed_bars == 1
    assert res.window_end - res.window_start == timedelta(hours=24)


def test_resolver_union_max_widest_wins_regardless_of_order():
    # E6: union over closure — widest requirement wins, tuple order irrelevant.
    narrow = _req(25)
    wide = _req(1, history_duration="3d")  # 864 @5m
    mid = _req(40)
    a = resolve(requirements=(narrow, wide, mid), as_of=_T0, timeframe="5m")
    b = resolve(requirements=(mid, wide, narrow), as_of=_T0, timeframe="5m")
    assert a.needed_bars == 864
    assert b.needed_bars == 864
    assert a.window_start == b.window_start


def test_resolver_warmup_widens_window_200_plus_50():
    # E7: m3.1 §8b example — 200 sufficient / 250 pristine -> planner fetches 250.
    res = resolve(requirements=(_req(200, 50),), as_of=_T0, timeframe="4h")
    assert res.needed_bars == 250
    assert res.window_end - res.window_start == timedelta(hours=1000)


def test_resolver_as_of_shift_keeps_depth_span_constant():
    # E8: PIT-vs-Depth — as_of moves the window, never the span; window_end = as_of.
    reqs = (_req(10, 0),)
    r0 = resolve(requirements=reqs, as_of=_T0, timeframe="5m")
    r1 = resolve(requirements=reqs, as_of=_T0 + timedelta(hours=1), timeframe="5m")
    assert r0.window_end == _T0
    assert r1.window_end == _T0 + timedelta(hours=1)
    span0 = r0.window_end - r0.window_start
    span1 = r1.window_end - r1.window_start
    assert span0 == span1 == timedelta(minutes=50)


def test_resolver_minimum_one_bar_tick_rate_hand_calc_h3():
    # E9 + H3 hand calc: min=1 @1m -> 1 bar -> span exactly 1 min.
    res = resolve(requirements=(_req(1, 0),), as_of=_T0, timeframe="1m")
    assert res.needed_bars == 1
    assert res.window_end - res.window_start == timedelta(minutes=1)


def test_resolver_hand_calc_h2_550_bars_at_4h_spans_2200h():
    # H2 hand calc: 500 + 50 = 550 bars * 4h = 2200h exactly (int minutes only).
    res = resolve(requirements=(_req(500, 50),), as_of=_T0, timeframe="4h")
    assert res.needed_bars == 550
    assert res.window_end - res.window_start == timedelta(hours=2200)


def test_resolver_output_frozen_and_extra_forbid():
    # Frozen contract: mutation and unknown fields both rejected (§2 Step 2 shape).
    res = resolve(requirements=(_req(),), as_of=_T0, timeframe="1m")
    assert isinstance(res, TemporalResolution)
    with pytest.raises(ValidationError):
        res.window_end = _T0  # type: ignore[misc]
    with pytest.raises(ValidationError):
        TemporalRequirement(minimum_history=1, warmup=0, bogus="x")  # type: ignore[call-arg]


# --- provider edges P1-P9 (spec §Test plan) ---


class FakeRangeStore:
    """Structural CandleRangeStore — records read_1m calls, serves/misbehaves on demand."""

    def __init__(self, rows: list[Candle] | None = None, error: Exception | None = None):
        self.rows = rows or []
        self.error = error
        self.calls: list[dict] = []

    async def read_1m(self, provider, instrument_id, window_start, window_end) -> list[Candle]:
        self.calls.append(
            {
                "provider": provider,
                "instrument_id": instrument_id,
                "window_start": window_start,
                "window_end": window_end,
            }
        )
        if self.error is not None:
            raise self.error
        return list(self.rows)  # intentionally NOT window-filtered (misbehaving store)


def _c1m(start: datetime, *, interval: str = "1m") -> Candle:
    return Candle(
        open=1.0,
        high=1.1,
        low=0.9,
        close=1.05,
        interval=interval,
        interval_start=start,
        interval_end=start + timedelta(minutes=1),
    )


def _series(n: int, base: datetime = _T0) -> list[Candle]:
    return [_c1m(base + timedelta(minutes=i)) for i in range(n)]


async def test_provider_happy_maps_broker_and_sorts_output():
    # P1: §6a.4 args reach the store (Day-1 provider == broker), unsorted store
    # rows come back chronological ascending.
    rows = [_c1m(_T0 + timedelta(minutes=m)) for m in (2, 0, 1)]
    store = FakeRangeStore(rows)
    pit = PITCandleProvider(store)
    out = await pit.get_candles(
        instrument_id="CRYPTO.BTCUSD",
        timeframe="1m",
        window_start=_T0,
        window_end=_T0 + timedelta(minutes=3),
        as_of=_T0 + timedelta(minutes=3),
        broker="deriv",
    )
    assert store.calls[0] == {
        "provider": "deriv",
        "instrument_id": "CRYPTO.BTCUSD",
        "window_start": _T0,
        "window_end": _T0 + timedelta(minutes=3),
    }
    starts = [c.interval_start for c in out]
    assert starts == sorted(starts)
    assert len(out) == 3


async def test_provider_no_look_ahead_as_of_mid_series_golden():
    # P2 NO-LOOK-AHEAD golden: rows exist past as_of; none may be returned.
    rows = _series(21, base=_T0)  # 10:00..10:20 starts, ends ..10:21
    as_of = _T0 + timedelta(minutes=10)
    pit = PITCandleProvider(FakeRangeStore(rows))
    out = await pit.get_candles(
        instrument_id="CRYPTO.BTCUSD",
        timeframe="1m",
        window_start=_T0,
        window_end=as_of,
        as_of=as_of,
        broker="deriv",
    )
    assert len(out) == 10  # bars ending <= 10:10 (starts ..10:09)
    assert max(c.interval_end for c in out) <= as_of
    assert min(c.interval_start for c in out) == _T0


async def test_provider_forming_candle_excluded():
    # P3: interval_end > window_end = forming-candle leak -> filtered out.
    rows = _series(7, base=_T0)  # ends 10:01..10:07
    window_end = _T0 + timedelta(minutes=5)
    pit = PITCandleProvider(FakeRangeStore(rows))
    out = await pit.get_candles(
        instrument_id="CRYPTO.BTCUSD",
        timeframe="1m",
        window_start=_T0,
        window_end=window_end,
        as_of=_T0 + timedelta(minutes=30),
        broker="deriv",
    )
    assert len(out) == 5
    assert max(c.interval_end for c in out) == window_end


async def test_provider_window_end_after_as_of_rejected_pit():
    # P4: window reaching past as_of would serve future data -> fail closed.
    pit = PITCandleProvider(FakeRangeStore())
    with pytest.raises(ValueError, match="PIT"):
        await pit.get_candles(
            instrument_id="CRYPTO.BTCUSD",
            timeframe="1m",
            window_start=_T0,
            window_end=_T0 + timedelta(minutes=5),
            as_of=_T0,
            broker="deriv",
        )


async def test_provider_zero_width_window_and_naive_inputs_rejected():
    # P5: degenerate window and naive datetimes both reject loudly.
    pit = PITCandleProvider(FakeRangeStore())
    with pytest.raises(ValueError):
        await pit.get_candles(
            instrument_id="CRYPTO.BTCUSD",
            timeframe="1m",
            window_start=_T0,
            window_end=_T0,
            as_of=_T0,
            broker="deriv",
        )
    with pytest.raises(ValueError):
        await pit.get_candles(
            instrument_id="CRYPTO.BTCUSD",
            timeframe="1m",
            window_start=datetime(2026, 1, 1, 12, 0),  # noqa: DTZ001 — naive case
            window_end=_T0,
            as_of=_T0,
            broker="deriv",
        )


async def test_provider_out_of_window_rows_filtered_defensively():
    # P6: store over-returning (window_start below request) -> provider re-filters.
    inside = _c1m(_T0 + timedelta(minutes=1))
    outside = _c1m(_T0 - timedelta(hours=1))
    pit = PITCandleProvider(FakeRangeStore([outside, inside]))
    out = await pit.get_candles(
        instrument_id="CRYPTO.BTCUSD",
        timeframe="1m",
        window_start=_T0,
        window_end=_T0 + timedelta(minutes=5),
        as_of=_T0 + timedelta(minutes=5),
        broker="deriv",
    )
    assert [c.interval_start for c in out] == [inside.interval_start]


async def test_provider_store_error_propagates_untouched():
    # P7: provider never swallows — engine flow 6 wraps; same exception type.
    pit = PITCandleProvider(FakeRangeStore(error=RuntimeError("warm read_1m down")))
    with pytest.raises(RuntimeError, match="warm read_1m down"):
        await pit.get_candles(
            instrument_id="CRYPTO.BTCUSD",
            timeframe="1m",
            window_start=_T0,
            window_end=_T0 + timedelta(minutes=5),
            as_of=_T0 + timedelta(minutes=5),
            broker="deriv",
        )


async def test_provider_as_of_before_all_rows_returns_empty():
    # P8: empty is valid — quality gate downstream marks INSUFFICIENT, no raise.
    rows = _series(5, base=_T0)
    pit = PITCandleProvider(FakeRangeStore(rows))
    out = await pit.get_candles(
        instrument_id="CRYPTO.BTCUSD",
        timeframe="1m",
        window_start=_T0 - timedelta(hours=1),
        window_end=_T0,
        as_of=_T0,  # every row ends AFTER as_of (starts at/after _T0)
        broker="deriv",
    )
    assert out == []


async def test_provider_does_not_recheck_interval_series_guard_stays_in_engine():
    # P9: division of responsibility — wrong-series guard belongs to engine flow 6.
    foreign = _series(3, base=_T0)  # interval="1m" while caller asks "5m"
    pit = PITCandleProvider(FakeRangeStore(foreign))
    out = await pit.get_candles(
        instrument_id="CRYPTO.BTCUSD",
        timeframe="5m",
        window_start=_T0,
        window_end=_T0 + timedelta(minutes=10),
        as_of=_T0 + timedelta(minutes=10),
        broker="deriv",
    )
    assert len(out) == 3  # passed through; engine's wrong-series guard decides


# --- adversarial X1-X3 (spec §Test plan) ---


async def test_x1_injection_string_passed_verbatim_as_parameter():
    # X1: SQL-shaped instrument_id is data, never code — no crash, verbatim arg.
    evil = "'; DROP TABLE candles_1m;--"
    store = FakeRangeStore()
    pit = PITCandleProvider(store)
    out = await pit.get_candles(
        instrument_id=evil,
        timeframe="1m",
        window_start=_T0,
        window_end=_T0 + timedelta(minutes=5),
        as_of=_T0 + timedelta(minutes=5),
        broker="deriv",
    )
    assert out == []
    assert store.calls[0]["instrument_id"] == evil  # opaque string, unchanged


def test_x2_bigint_duration_exact_int_math_no_overflow():
    # X2: 999999999d at 1m — exact Python int needed_bars (no float/precision loss).
    # window_start underflows datetime 1..9999 (year ~ -2.7M) -> resolver fails
    # closed with ValueError carrying the EXACT bar count (999999999*1440), never
    # clamped — spec X2 "exact needed_bars, no overflow/float". (Test-bug fix post-
    # A68: original window_start equality was self-contradictory — its own RHS
    # `_T0 - timedelta(...)` raises OverflowError inside the test.)
    with pytest.raises(ValueError, match="needed_bars=1439999998560"):
        resolve(
            requirements=(_req(1, history_duration="999999999d"),),
            as_of=_T0,
            timeframe="1m",
        )


async def test_x3_ten_thousand_rows_filtered_and_sorted_complete():
    # X3: bounded adversarial volume — complete pass, exact count, no truncation.
    n = 10_000
    rows = [_c1m(_T0 + timedelta(minutes=i)) for i in range(n)]
    store = FakeRangeStore(list(reversed(rows)))
    pit = PITCandleProvider(store)
    out = await pit.get_candles(
        instrument_id="CRYPTO.BTCUSD",
        timeframe="1m",
        window_start=_T0,
        window_end=_T0 + timedelta(minutes=n),
        as_of=_T0 + timedelta(minutes=n),
        broker="deriv",
    )
    assert len(out) == n
    starts = [c.interval_start for c in out]
    assert starts == sorted(starts)
    assert starts[0] == _T0 and starts[-1] == _T0 + timedelta(minutes=n - 1)


# --- engine delegation + integration (spec §Test plan) ---


class _Analytic:
    """Local fixture analytic — exactly NAME / REQUIREMENTS / compute (Isolation Law)."""

    NAME: str
    REQUIREMENTS: DataRequirement
    REGIME_LABEL = None

    def __init__(self, name: str, requirements: DataRequirement) -> None:
        self.NAME = name
        self.REQUIREMENTS = requirements

    def compute(self, candles, params) -> float:
        return 42.0


class _CaptureProvider:
    """§6a.4 recording provider serving a self-consistent 5m series inside the window."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def get_candles(self, instrument_id, timeframe, window_start, window_end, as_of, broker):
        self.calls.append(
            {
                "instrument_id": instrument_id,
                "timeframe": timeframe,
                "window_start": window_start,
                "window_end": window_end,
                "as_of": as_of,
                "broker": broker,
            }
        )
        return [
            Candle(
                open=1.0,
                high=1.1,
                low=0.9,
                close=1.05,
                interval=timeframe,
                interval_start=window_end - timedelta(minutes=5 * (i + 1)),
                interval_end=window_end - timedelta(minutes=5 * i),
            )
            for i in range(60)
        ]


def _engine_request(features: tuple[str, ...]) -> FeatureRequest:
    return FeatureRequest(
        instrument_id="FX.EURUSD",
        broker="deriv",
        timeframe="5m",
        features=features,
        as_of=_T0,
        asset_class="forex",
        param_overrides=None,
    )


async def test_delegation_engine_window_equals_resolver_output():
    # D1: flow 5 must BE resolve() — same requirements -> identical window.
    reg = AnalyticsRegistry()
    reg.register(
        _Analytic(
            "rsi_14",
            DataRequirement(minimum_history=50, warmup=0, dependencies=(), defaults={}),
        )
    )
    reg.register(
        _Analytic(
            "ema_20",
            DataRequirement(minimum_history=5, warmup=0, dependencies=("rsi_14",), defaults={}),
        )
    )
    provider = _CaptureProvider()
    engine = FeatureEngine(
        provider=provider,
        capability_registry=CapabilityRegistry.from_yaml(_CAP_YAML),
        registry=reg,
        profiles=Profiles.from_dir(_ASSETS_DIR),
    )

    res = await engine.get_features(_engine_request(("ema_20",)))

    expected = resolve(
        requirements=(_req(50), _req(5, history_duration=None)),
        as_of=_T0,
        timeframe="5m",
    )
    call = provider.calls[0]
    assert call["window_start"] == expected.window_start
    assert call["window_end"] == expected.window_end
    assert call["window_end"] == _T0
    assert res.features == {"ema_20": 42.0}


async def test_engine_e2e_features_through_pit_provider():
    # D2: full path — PIT provider over a (misbehaving) store -> filtered window
    # -> engine guards -> features computed. Store serves MORE rows than the window.
    reg = AnalyticsRegistry()
    reg.register(
        _Analytic(
            "rsi_14",
            DataRequirement(minimum_history=3, warmup=0, dependencies=(), defaults={}),
        )
    )
    rows = [
        Candle(
            open=1.0,
            high=1.1,
            low=0.9,
            close=1.05,
            interval="5m",
            interval_start=_T0 - timedelta(minutes=45 - 5 * i),
            interval_end=_T0 - timedelta(minutes=40 - 5 * i),
        )
        for i in range(10)
    ]
    engine = FeatureEngine(
        provider=PITCandleProvider(FakeRangeStore(rows)),
        capability_registry=CapabilityRegistry.from_yaml(_CAP_YAML),
        registry=reg,
        profiles=Profiles.from_dir(_ASSETS_DIR),
    )

    res = await engine.get_features(_engine_request(("rsi_14",)))

    # window = 3 bars * 5m = 15min -> provider keeps starts >= _T0-15 and ends <= _T0
    # (3 bars: _T0-15, _T0-10, _T0-5; the _T0-starting bar is still forming).
    assert res.features == {"rsi_14": 42.0}
    assert res.capability_warning is None or "INSUFFICIENT" not in (res.capability_warning or "")
