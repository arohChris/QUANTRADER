"""
M2.11 Live E2E — Spec tests for the live pipeline composition (WRITTEN BEFORE CODE).

WHY: AGENTS.md §2 Step 3 — docs/specs/m2.11_live_e2e.spec.md (A49 LGTM'd; A49-a
     amendments applied same turn: forming out of scope per A33, ingest-stage
     reasons for tests 4/5). Only NEW impl imported = modules.m2_market_data.pipeline
     (honest collection RED until Step 5). Frozen chain components (ingest,
     normalize, validate-via-pipeline, candles, warm report types) are imported
     as-is — this slice tests the COMPOSITION, not their internals.

Gates: C1-C7 (spec §4) + edges E1-E12 (spec §5). HOT/WARM faked; ingest +
     normalize + CandleBuilder real so the composed chain is exercised end-to-end
     inside the unit tests. No network, no DB, no money math (spec §6 n/a).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

# New impl — fails collection until Step 5 (honest RED, spec §6).
from modules.m2_market_data.pipeline import (
    LivePipeline,
    PipelineError,
    PipelineReport,
    evaluate,
    hot_latest_matches,
    process_frame,
    reconcile,
    report_to_json,
    run_e2e,
    warm_readback_matches,
    write_artifacts,
)

from modules.m2_market_data.ingest.ingest import TickIngestor
from modules.m2_market_data.normalize import Normalizer
from modules.m2_market_data.warm import FlushCursor, FlushReport
from modules.m2_market_data.watermark import WatermarkRegistry
from modules.reference import reference as _refmod
from shared.contracts.drop import Drop, DropReason
from shared.contracts.market_data import MarketData, Tick
from shared.core.clock import SimulatedClock

# Human Deriv sample epoch (m2.1 fixtures): 2026-03-22T19:46:40Z.
EPOCH = 1774208800
BASE_TS = datetime.fromtimestamp(EPOCH, tz=UTC)
# WHY +60s: every fixture exchange must sit behind BOTH future gates (ingest 1s,
# validate 5s) so only the explicit future test pins the clock back.
CLOCK = BASE_TS + timedelta(seconds=60)
SUB_ID = "a9b8c7d6-1234-5678-90ab-cdef12345678"


def _clock(t: datetime | None = None) -> SimulatedClock:
    return SimulatedClock(t if t is not None else CLOCK)


def _ing(clock: datetime | None = None) -> TickIngestor:
    return TickIngestor(provider="deriv", clock=_clock(clock))


class _ReferenceFacade:
    """Same facade pattern as verify_m2.1_live_capture — real config reads."""

    def current_snapshot_id(self) -> str | None:
        return _refmod.current_snapshot_id()

    def to_canonical(self, native: str, broker: str) -> str:
        return _refmod.to_canonical(native, broker)

    def exists(self, instrument_id: str, broker: str) -> bool:
        return _refmod.exists(instrument_id, broker)

    def get_instrument(self, instrument_id: str) -> dict:
        return _refmod.get_instrument(instrument_id)


def _norm() -> Normalizer:
    return Normalizer(
        reference=_ReferenceFacade(), watermark_registry=WatermarkRegistry()
    )


def _frame(**tick_overrides) -> str:
    """Realistic Deriv msg_type=tick frame (m2.1 fixture shape, 2026-09-25 endpoint)."""
    body: dict = {
        "ask": 1.08542,
        "bid": 1.08538,
        "epoch": EPOCH,
        "id": SUB_ID,
        "pip_size": 5,
        "quote": 1.08540,
        "symbol": "frxEURUSD",
    }
    body.update(tick_overrides)
    return json.dumps(
        {
            "echo_req": {"req_id": 101, "subscribe": 1, "ticks": body["symbol"]},
            "msg_type": "tick",
            "req_id": 101,
            "subscription": {"id": SUB_ID},
            "tick": body,
        }
    )


class _FakeHot:
    """In-memory HotStore double — records every call the pipeline makes."""

    def __init__(self, ping_ok: bool = True) -> None:
        self._ping_ok = ping_ok
        self.latest: list[MarketData] = []
        self.forming: list = []  # MUST stay empty: A33 finalized-never-in-HOT

    async def ping(self) -> bool:
        return self._ping_ok

    async def set_latest(self, md: MarketData) -> bool:
        self.latest.append(md)
        return True

    async def set_forming(self, provider, instrument_id, timeframe, candle) -> bool:
        self.forming.append(candle)
        return True

    async def get_latest(self, provider, instrument_id) -> MarketData | None:
        return self.latest[-1] if self.latest else None


class _FakeWarm:
    """In-memory Candles1mStore double — dedup on (provider, inst, interval_start)
    mirrors ON CONFLICT DO NOTHING; read_1m mirrors the A46 inclusive window."""

    def __init__(self) -> None:
        self.store: dict = {}
        self.flush_calls: list[tuple] = []
        self.inserted_history: list[int] = []
        self.watermarks: list = []
        self.read_args: tuple | None = None
        self.fail_with: Exception | None = None

    async def flush_batch(
        self, provider, instrument_id, candles, reference_snapshot_id=None
    ) -> FlushReport:
        if self.fail_with is not None:
            raise self.fail_with
        self.flush_calls.append(
            (provider, instrument_id, tuple(candles), reference_snapshot_id)
        )
        inserted = 0
        for c in candles:
            key = (provider, instrument_id, c.interval_start)
            if key not in self.store:
                inserted += 1
            self.store[key] = c
        wm = max((k[2] for k in self.store), default=None)
        self.inserted_history.append(inserted)
        self.watermarks.append(wm)
        return FlushReport(
            acked=True,
            inserted=inserted,
            cursor=FlushCursor(provider, instrument_id, wm),
            evicted=tuple(candles),
        )

    async def read_1m(self, provider, instrument_id, window_start, window_end):
        self.read_args = (provider, instrument_id, window_start, window_end)
        return [
            c
            for (p, i, s), c in sorted(self.store.items(), key=lambda kv: kv[0][2])
            if p == provider
            and i == instrument_id
            and window_start <= s <= window_end
        ]


class _LeakyWarm(_FakeWarm):
    """Adversarial store: returns a row OUTSIDE the requested window — the
    pipeline must filter it before comparing (defensive double-filter)."""

    async def read_1m(self, provider, instrument_id, window_start, window_end):
        rows = await super().read_1m(
            provider, instrument_id, window_start, window_end
        )
        leak = rows[0].model_copy(
            update={"interval_start": window_end + timedelta(minutes=5)}
        )
        return [*rows, leak]


def _pipe(hot=None, warm=None, ing=None, norm=None) -> LivePipeline:
    return LivePipeline(
        ingest=ing if ing is not None else _ing(),
        normalizer=norm if norm is not None else _norm(),
        hot=hot if hot is not None else _FakeHot(),
        warm=warm if warm is not None else _FakeWarm(),
        provider="deriv",
    )


# --- 1-5: process_frame (S2: ingest -> normalize -> validate) -----------------


def test_process_frame_accepts_valid() -> None:
    result = process_frame(_frame(), _ing(), _norm())
    assert isinstance(result, MarketData)
    assert result.instrument_id == "FX.EURUSD"
    assert result.kind == "tick"
    assert isinstance(result.payload, Tick)  # narrows payload union (LSP) + asserts shape
    assert result.payload.price == 1.0854


def test_process_frame_ingest_drop_on_junk_json() -> None:
    result = process_frame("{oops", _ing(), _norm())
    assert isinstance(result, Drop)
    assert result.reason == DropReason.invalid_json
    assert result.detail == "malformed JSON"


def test_process_frame_normalize_drop_on_unknown_symbol() -> None:
    result = process_frame(_frame(symbol="xyzNOTREAL"), _ing(), _norm())
    assert isinstance(result, Drop)
    assert result.reason == DropReason.unknown_symbol


def test_process_frame_drops_negative_price() -> None:
    # A49-a: ingest gates quote <= 0 first (invalid_price) — validate's
    # negative_price stays a second layer for direct-md callers (m2.3).
    result = process_frame(_frame(quote=-1), _ing(), _norm())
    assert isinstance(result, Drop)
    assert result.reason == DropReason.invalid_price


def test_process_frame_returns_drop_not_exception_on_future_timestamp() -> None:
    # A49-a: ingest future gate (1s) fires before validate's (5s) — clock -60s.
    ing = _ing(clock=BASE_TS - timedelta(seconds=60))
    result = process_frame(_frame(), ing, _norm())
    assert isinstance(result, Drop)
    assert result.reason == DropReason.timestamp_ahead


# --- 6-7: S3 candles (real CandleBuilder) + drop propagation ------------------


async def test_candle_builder_close_finalizes_open_bucket() -> None:
    pipe = _pipe()
    r1 = await pipe.push(_frame(quote=1.08540, epoch=EPOCH))
    r2 = await pipe.push(_frame(quote=1.08545, epoch=EPOCH + 1))
    assert isinstance(r1, MarketData) and isinstance(r2, MarketData)
    candles = pipe.finish()
    assert len(candles) == 1
    c = candles[0]
    # Hand calc: quotes 1.08540, 1.08545 in the same 19:46 bucket ->
    # O=1.08540 H=1.08545 L=1.08540 C=1.08545, tick_count=2
    assert (c.open, c.high, c.low, c.close, c.tick_count) == (
        1.08540,
        1.08545,
        1.08540,
        1.08545,
        2,
    )
    assert c.interval_start == BASE_TS.replace(second=0, microsecond=0)


async def test_builder_drop_propagates_to_drops() -> None:
    pipe = _pipe()
    first = await pipe.push(_frame(quote=1.08540, epoch=EPOCH + 20))  # bucket 19:47
    late = await pipe.push(_frame(quote=1.08545, epoch=EPOCH))  # bucket 19:46 -> late
    assert isinstance(first, MarketData)
    assert isinstance(late, Drop)
    assert late.reason == DropReason.invalid_timestamp
    assert pipe.drops[-1]["reason"] == "invalid_timestamp"
    assert pipe.drops[-1]["detail"] == "late tick prior open bucket"


# --- 8-11: S4 hot (set_latest wiring; forming NEVER written — A49-a/A33) ------


async def test_hot_set_latest_called_per_accepted_md() -> None:
    hot = _FakeHot()
    pipe = _pipe(hot=hot)
    results = [
        await pipe.push(_frame(epoch=EPOCH + i, quote=1.08540 + i * 0.00001))
        for i in range(3)
    ]
    assert all(isinstance(r, MarketData) for r in results)
    assert len(hot.latest) == 3
    assert len(hot.forming) == 0


async def test_hot_get_latest_equals_last_accepted() -> None:
    hot = _FakeHot()
    pipe = _pipe(hot=hot)
    await pipe.push(_frame(quote=1.08540, epoch=EPOCH))
    last = await pipe.push(_frame(quote=1.08560, epoch=EPOCH + 1))
    assert isinstance(last, MarketData)
    got = await hot.get_latest("deriv", "FX.EURUSD")
    assert hot_latest_matches(last, got) is True
    assert hot_latest_matches(last, hot.latest[0]) is False  # older price


async def test_forming_key_never_written_by_pipeline() -> None:
    # A49-a/A33: finalized candles never land in HOT forming_key; the frozen
    # CandleBuilder exposes no forming accessor, so this slice writes NONE.
    hot = _FakeHot()
    pipe = _pipe(hot=hot)
    await pipe.push(_frame(quote=1.08540, epoch=EPOCH))
    await pipe.push(_frame(quote=1.08545, epoch=EPOCH + 1))
    pipe.finish()
    assert hot.forming == []


async def test_boundary_candle_flushed_not_stored_hot() -> None:
    hot = _FakeHot()
    warm = _FakeWarm()
    pipe = _pipe(hot=hot, warm=warm)
    await pipe.push(_frame(quote=1.08540, epoch=EPOCH))
    r2 = await pipe.push(_frame(quote=1.08545, epoch=EPOCH + 20))  # crosses 19:47
    assert isinstance(r2, MarketData)
    candles = pipe.finish()
    assert len(candles) == 2  # boundary-closed 19:46 + close() 19:47
    await pipe.flush("FX.EURUSD")
    assert hot.forming == []  # forming never written, boundary or not
    assert len(warm.store) == 2


# --- 12-15: S5/S6 warm flush + read-back (A46 window law) ---------------------


async def test_flush_receives_all_finalized_candles() -> None:
    warm = _FakeWarm()
    pipe = _pipe(warm=warm)
    await pipe.push(_frame(quote=1.08540, epoch=EPOCH))  # bucket 19:46
    await pipe.push(_frame(quote=1.08545, epoch=EPOCH + 20))  # bucket 19:47
    pipe.finish()
    await pipe.flush("FX.EURUSD")
    sent = warm.flush_calls[0][2]
    assert len(sent) == 2
    starts = [c.interval_start for c in sent]
    assert starts == sorted(starts)  # FIFO ascending interval_start
    assert starts[0] < starts[1]


async def test_warm_readback_equals_flushed() -> None:
    pipe = _pipe()
    await pipe.push(_frame())
    candles = pipe.finish()
    await pipe.flush("FX.EURUSD")
    rows = await pipe.readback(
        "FX.EURUSD", candles[0].interval_start, candles[0].interval_start
    )
    assert warm_readback_matches(candles, rows) is True
    tampered = [rows[0].model_copy(update={"close": 9.99999})]
    assert warm_readback_matches(candles, tampered) is False


async def test_warm_readback_window_is_verbatim_inclusive() -> None:
    warm = _FakeWarm()
    pipe = _pipe(warm=warm)
    await pipe.push(_frame())
    candles = pipe.finish()
    await pipe.flush("FX.EURUSD")
    bound = candles[0].interval_start  # window == exact single bucket bound
    rows = await pipe.readback("FX.EURUSD", bound, bound)
    assert warm.read_args == ("deriv", "FX.EURUSD", bound, bound)  # A46 verbatim
    assert rows == candles  # inclusive: row AT the bound is returned


async def test_readback_never_expands_window() -> None:
    warm = _LeakyWarm()
    pipe = _pipe(warm=warm)
    await pipe.push(_frame())
    candles = pipe.finish()
    await pipe.flush("FX.EURUSD")
    start = candles[0].interval_start
    end = candles[0].interval_end
    rows = await pipe.readback("FX.EURUSD", start, end)
    assert all(start <= c.interval_start <= end for c in rows)
    assert len(rows) == 1  # leaked out-of-window row filtered before compare


# --- 16-19: C1 reconcile + run-level failures (E1/E4) -------------------------


def test_reconcile_captured_equals_accepted_plus_dropped() -> None:
    assert reconcile(captured=10, accepted=7, dropped=3) is True
    assert reconcile(captured=5, accepted=5, dropped=0) is True


def test_reconcile_fails_on_unaccounted_frame() -> None:
    assert reconcile(captured=10, accepted=7, dropped=2) is False
    assert reconcile(captured=10, accepted=11, dropped=0) is False


async def test_empty_capture_fails_never_passes() -> None:
    report = await run_e2e(collect=list, pipeline=_pipe())
    assert report.captured == 0
    violations = evaluate(report)
    assert "C2" in violations and "C3" in violations and "C4" in violations
    assert "C1" not in violations  # 0 == 0+0 reconciles; failures are C2/C3/C4


async def test_all_dropped_fails_with_reasons() -> None:
    frames = ["{oops", json.dumps({"msg_type": "heartbeat", "req_id": 1})]
    report = await run_e2e(collect=lambda: list(frames), pipeline=_pipe())
    assert report.captured == 2
    assert report.accepted == 0 and report.dropped == 2
    assert [d["reason"] for d in report.drops] == ["invalid_json", "missing_field"]
    assert "C2" in evaluate(report)


# --- 20-21: E7/E8 fail-loud + C7 redaction ------------------------------------


async def test_valkey_down_fails_loud() -> None:
    pipe = _pipe(hot=_FakeHot(ping_ok=False))
    with pytest.raises(PipelineError, match="HOT unavailable"):
        await pipe.check_infra()


async def test_flush_error_propagates_redacted() -> None:
    warm = _FakeWarm()
    warm.fail_with = RuntimeError(
        "connect failed: postgresql://quantrader:supersecret123@127.0.0.1:5432/quantrader"
    )
    pipe = _pipe(warm=warm)
    await pipe.push(_frame())
    pipe.finish()
    with pytest.raises(PipelineError) as ei:
        await pipe.flush("FX.EURUSD")
    msg = str(ei.value)
    assert "supersecret123" not in msg
    assert "quantrader:***@" in msg


# --- 22-23: C6 idempotent rerun + monotonic cursor -----------------------------


async def test_second_run_idempotent_superset_passes() -> None:
    warm = _FakeWarm()
    pipe = _pipe(warm=warm)
    await pipe.push(_frame())
    candles = pipe.finish()
    r1 = await pipe.flush("FX.EURUSD")
    r2 = await pipe.flush("FX.EURUSD")  # rerun same window (C6)
    assert r1.inserted == 1 and r2.inserted == 0  # ON CONFLICT DO NOTHING shape
    rows = await pipe.readback(
        "FX.EURUSD", candles[0].interval_start, candles[0].interval_start
    )
    assert warm_readback_matches(candles, rows) is True


async def test_cursor_never_regresses() -> None:
    warm = _FakeWarm()
    pipe = _pipe(warm=warm)
    await pipe.push(_frame(epoch=EPOCH))
    pipe.finish()
    r1 = await pipe.flush("FX.EURUSD")
    await pipe.push(_frame(quote=1.08545, epoch=EPOCH + 20))
    pipe.finish()
    r2 = await pipe.flush("FX.EURUSD")
    assert r1.cursor.watermark is not None and r2.cursor.watermark is not None
    assert r1.cursor.watermark <= r2.cursor.watermark
    assert warm.watermarks == sorted(warm.watermarks)  # monotonic across flushes


# --- 24-26: artifacts + report shape + adversarial empty frame -----------------


def test_drops_artifact_written_even_when_empty(tmp_path) -> None:
    paths = write_artifacts(
        tmp_path, "cryBTCUSD", raws=[], drops=[], report=PipelineReport()
    )
    assert paths["drops"].exists()
    assert paths["drops"].read_text(encoding="utf-8") == ""  # honest empty file
    assert paths["raw"].exists()
    doc = json.loads(paths["report"].read_text(encoding="utf-8"))
    assert "stages" in doc


def test_report_json_has_all_stages() -> None:
    doc = report_to_json(PipelineReport())
    assert set(doc["stages"]) == {
        "collect",
        "process",
        "candles",
        "hot",
        "warm",
        "readback",
    }
    assert isinstance(doc["violations"], list)


def test_adversarial_empty_raw_frame() -> None:
    result = process_frame("", _ing(), _norm())
    assert isinstance(result, Drop)
    assert result.reason == DropReason.empty
