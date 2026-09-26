"""
M2.11 — pipeline — live E2E composition (the ONLY new runtime module this slice).
WHY: one tested composition unit proves the built data layer is wired end-to-end:
raw frame -> ingest -> normalize -> validate -> CandleBuilder -> HOT set_latest ->
WARM flush_batch -> read-back reconcile (C1-C7, spec m2.11 §4). Injected deps so
unit tests run on in-memory fakes (no network/DB); the verify script injects real
Valkey/Timescale. Frozen components consumed as-is — zero edits (spec §2).

Notes: run_e2e takes a sync `collect()` closure (script binds --ticks/--timeout-s
into it — tests A50 are the contract); lake lineage read-back lives in the verify
script (m2.1 filter_run_rows), not here; S4 forming_key is NEVER written (A49-a:
A33 finalized-never-in-HOT + CandleBuilder has no forming accessor).
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from modules.m2_market_data.candles import CandleBuilder
from modules.m2_market_data.ingest.ingest import TickIngestor
from modules.m2_market_data.normalize import Normalizer
from modules.m2_market_data.validate import validate
from modules.m2_market_data.warm import FlushReport, redact_dsn
from shared.contracts.drop import Drop
from shared.contracts.market_data import Candle, MarketData

# E12 advisory stage keys — exact set; report_to_json fills missing with 0.0.
STAGES = ("collect", "process", "candles", "hot", "warm", "readback")


class PipelineError(RuntimeError):
    """Typed fail-loud for infra problems — message ALWAYS pre-redacted (C7)."""


class _HotPort(Protocol):
    """Structural type for M2.9 HotStore (real or test fake)."""

    async def set_latest(self, md: MarketData) -> bool: ...

    async def get_latest(self, provider: str, instrument_id: str) -> MarketData | None: ...

    async def ping(self) -> bool: ...


class _WarmPort(Protocol):
    """Structural type for M2.10 Candles1mStore (real or test fake)."""

    async def flush_batch(
        self,
        provider: str,
        instrument_id: str,
        candles: Sequence[Candle],
        reference_snapshot_id: str | None = None,
    ) -> FlushReport: ...

    async def read_1m(
        self,
        provider: str,
        instrument_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[Candle]: ...


def process_frame(
    raw: str | bytes, ingest: TickIngestor, normalizer: Normalizer
) -> MarketData | Drop:
    """S2: one raw frame through the frozen chain; never raises on junk."""
    tick = ingest.ingest(raw)
    if isinstance(tick, Drop):
        return tick
    md = normalizer.normalize(tick)
    if isinstance(md, Drop):
        return md
    return validate(md)


def _drop_record(raw: str | bytes, drop: Drop) -> dict:
    """C5 record: epoch (best-effort from raw frame) + reason + detail."""
    epoch: int | None = None
    try:
        obj = json.loads(raw) if isinstance(raw, (str, bytes)) else None
        if isinstance(obj, dict):
            ep = obj.get("tick", {}).get("epoch")
            if isinstance(ep, int) and not isinstance(ep, bool):
                epoch = ep
    except (ValueError, TypeError, AttributeError):
        epoch = None
    return {"epoch": epoch, "reason": drop.reason.value, "detail": drop.detail}


class LivePipeline:
    """Composition unit for stages S2-S6 (spec §3) — one instance per run."""

    def __init__(
        self,
        *,
        ingest: TickIngestor,
        normalizer: Normalizer,
        hot: _HotPort,
        warm: _WarmPort,
        provider: str = "deriv",
        builder_factory: Callable[[str], CandleBuilder] | None = None,
    ) -> None:
        self.ingest = ingest
        self.normalizer = normalizer
        self.hot = hot
        self.warm = warm
        self.provider = provider

        # WHY injected: tests swap fakes; script relies on the default real builder.
        def _default_builder(instrument_id: str) -> CandleBuilder:
            return CandleBuilder(instrument_id, provider, "1m")

        self._builder_factory = builder_factory or _default_builder
        self._builder: CandleBuilder | None = None
        self.finalized: list[Candle] = []
        self.accepted: list[MarketData] = []
        self.drops: list[dict] = []
        self.errors: list[str] = []
        self.last_md: MarketData | None = None

    @property
    def instrument_id(self) -> str | None:
        return self.last_md.instrument_id if self.last_md is not None else None

    async def push(self, raw: str | bytes) -> MarketData | Drop:
        """S2->S3->S4: accepted ONLY when the builder does not reject it (C1)."""
        md = process_frame(raw, self.ingest, self.normalizer)
        if isinstance(md, Drop):
            self.drops.append(_drop_record(raw, md))
            return md
        if self._builder is None:
            # WHY assert: Normalizer guarantees instrument_id (M2.2 steps 2-4) —
            # contract field stays optional, this narrows for the builder seam.
            assert md.instrument_id is not None
            self._builder = self._builder_factory(md.instrument_id)
        closed = self._builder.on_tick(md)
        if isinstance(closed, Drop):
            self.drops.append(_drop_record(raw, closed))
            return closed
        if closed is not None:
            self.finalized.append(closed)  # boundary-cross candle (S3)
        if not await self.hot.set_latest(md):
            # M2.9 runtime is fail-open by design; record, never mask (C4/report).
            self.errors.append("hot set_latest returned False")
        self.accepted.append(md)
        self.last_md = md
        return md

    def finish(self) -> list[Candle]:
        """Run end: close() the open bucket (E5 guarantee) + expose finalized list."""
        if self._builder is not None:
            closed = self._builder.close()
            if closed is not None:
                self.finalized.append(closed)
        return list(self.finalized)

    async def flush(
        self, instrument_id: str, candles: Sequence[Candle] | None = None
    ) -> FlushReport:
        """S5: one run-sized batch (spec §3); buffer NOT cleared so a rerun is
        idempotent via ON CONFLICT (C6). Errors redacted before escaping (C7)."""
        batch = list(self.finalized if candles is None else candles)
        ref = self.last_md.reference_snapshot_id if self.last_md else None
        try:
            return await self.warm.flush_batch(self.provider, instrument_id, batch, ref)
        except Exception as exc:
            raise PipelineError(f"warm flush failed: {redact_dsn(str(exc))}") from exc

    async def readback(
        self, instrument_id: str, window_start: datetime, window_end: datetime
    ) -> list[Candle]:
        """S6: A46 verbatim inclusive window + defensive re-filter (never expand)."""
        try:
            rows = await self.warm.read_1m(
                self.provider, instrument_id, window_start, window_end
            )
        except Exception as exc:
            raise PipelineError(f"warm read failed: {redact_dsn(str(exc))}") from exc
        return [c for c in rows if window_start <= c.interval_start <= window_end]

    async def check_infra(self) -> None:
        """E7: verify must NOT mask missing infra (product fail-open ≠ verify)."""
        if not await self.hot.ping():
            raise PipelineError("HOT unavailable — verify requires live Valkey")


def reconcile(*, captured: int, accepted: int, dropped: int) -> bool:
    """C1: every captured frame ends in exactly one bucket."""
    return captured == accepted + dropped


def hot_latest_matches(expected: MarketData | None, got: MarketData | None) -> bool:
    """C4: last accepted md equals HOT value — instrument + price (fail-closed)."""
    if expected is None or got is None:
        return False
    exp_price = getattr(expected.payload, "price", None)
    got_price = getattr(got.payload, "price", None)
    return (
        exp_price is not None
        and got_price == exp_price
        and got.instrument_id == expected.instrument_id
    )


def warm_readback_matches(flushed: Sequence[Candle], rows: Sequence[Candle]) -> bool:
    """C3/C6: every flushed candle present field-by-field; extra rows allowed
    (rerun superset OK — the A46 window filter already bounds `rows`)."""
    for f in flushed:
        if not any(
            r.interval_start == f.interval_start
            and r.open == f.open
            and r.high == f.high
            and r.low == f.low
            and r.close == f.close
            and r.tick_count == f.tick_count
            for r in rows
        ):
            return False
    return True


@dataclass
class PipelineReport:
    """Run summary — mirrors capture.ReadbackReport pattern (spec §2)."""

    captured: int = 0
    accepted: int = 0
    dropped: int = 0
    drops: list[dict] = field(default_factory=list)
    candles_flushed: int = 0
    warm_inserted: int = 0
    hot_latest_ok: bool = False
    warm_readback_ok: bool = False
    instrument_id: str | None = None
    symbol: str | None = None
    reference_snapshot_id: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    errors: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=lambda: dict.fromkeys(STAGES, 0.0))


def evaluate(report: PipelineReport) -> list[str]:
    """C1-C5 violations (C6 needs a rerun — script prints it; C7 = test suite)."""
    violations: list[str] = []
    if not reconcile(
        captured=report.captured, accepted=report.accepted, dropped=report.dropped
    ):
        violations.append("C1")
    if report.accepted < 1:
        violations.append("C2")
    if report.candles_flushed < 1 or not report.warm_readback_ok:
        violations.append("C3")
    if not report.hot_latest_ok:
        violations.append("C4")
    if any(not d.get("reason") or "detail" not in d for d in report.drops):
        violations.append("C5")
    return violations


def report_to_json(report: PipelineReport) -> dict:
    """JSON-safe report — stages keys EXACT (E12 advisory timings, never gated)."""
    return {
        "stages": {name: float(report.timings.get(name, 0.0)) for name in STAGES},
        "counts": {
            "captured": report.captured,
            "accepted": report.accepted,
            "dropped": report.dropped,
            "candles_flushed": report.candles_flushed,
            "warm_inserted": report.warm_inserted,
        },
        "checks": {
            "hot_latest_ok": report.hot_latest_ok,
            "warm_readback_ok": report.warm_readback_ok,
        },
        "window": {
            "start": report.window_start.isoformat() if report.window_start else None,
            "end": report.window_end.isoformat() if report.window_end else None,
        },
        "instrument_id": report.instrument_id,
        "symbol": report.symbol,
        "reference_snapshot_id": report.reference_snapshot_id,
        "drops": report.drops,
        "errors": [redact_dsn(e) for e in report.errors],
        "violations": evaluate(report),
    }


async def run_e2e(
    *,
    collect: Callable[[], Sequence[str | bytes]],
    pipeline: LivePipeline,
    reference_snapshot_id: str | None = None,
) -> PipelineReport:
    """S1-S6 orchestrator — flush ONCE at end (run-sized batch, spec §3)."""
    report = PipelineReport()
    report.reference_snapshot_id = reference_snapshot_id

    t0 = time.perf_counter()
    raws = list(collect())
    report.timings["collect"] = time.perf_counter() - t0
    report.captured = len(raws)

    t1 = time.perf_counter()
    for raw in raws:
        await pipeline.push(raw)
    # S2+S3+S4 all happen inside push — candles/hot stay 0.0 (E12 advisory only).
    report.timings["process"] = time.perf_counter() - t1

    candles = pipeline.finish()
    report.instrument_id = pipeline.instrument_id
    report.symbol = pipeline.last_md.symbol if pipeline.last_md else None
    report.accepted = len(pipeline.accepted)
    report.dropped = len(pipeline.drops)
    report.drops = list(pipeline.drops)
    report.errors = list(pipeline.errors)
    if candles:
        report.window_start = candles[0].interval_start
        report.window_end = candles[-1].interval_start

    inst = pipeline.instrument_id
    if pipeline.last_md is not None and inst is not None:
        got = await pipeline.hot.get_latest(pipeline.provider, inst)
        report.hot_latest_ok = hot_latest_matches(pipeline.last_md, got)

    t_warm = time.perf_counter()
    if candles and inst is not None:
        flush_report = await pipeline.flush(inst)
        report.candles_flushed = len(candles)
        report.warm_inserted = flush_report.inserted
    report.timings["warm"] = time.perf_counter() - t_warm

    t_rb = time.perf_counter()
    if candles and inst is not None and report.window_start is not None:
        assert report.window_end is not None  # set together when candles non-empty
        rows = await pipeline.readback(inst, report.window_start, report.window_end)
        report.warm_readback_ok = warm_readback_matches(candles, rows)
    report.timings["readback"] = time.perf_counter() - t_rb
    return report


def write_artifacts(
    out_dir: Path,
    symbol: str,
    raws: Sequence[str | bytes],
    drops: Sequence[dict],
    report: PipelineReport,
) -> dict[str, Path]:
    """C5 artifacts — drops file ALWAYS written (empty file when 0 drops, honest)."""
    day = Path(out_dir)
    day.mkdir(parents=True, exist_ok=True)
    raw_path = day / f"raw_{symbol}.jsonl"
    drops_path = day / f"drops_{symbol}.jsonl"
    report_path = day / f"e2e_report_{symbol}.json"
    raw_path.write_text(
        "".join(
            (r if isinstance(r, str) else r.decode("utf-8", "replace")) + "\n" for r in raws
        ),
        encoding="utf-8",
    )
    drops_path.write_text("".join(json.dumps(d) + "\n" for d in drops), encoding="utf-8")
    report_path.write_text(
        json.dumps(report_to_json(report), indent=2, default=str), encoding="utf-8"
    )
    return {"raw": raw_path, "drops": drops_path, "report": report_path}


__all__ = [
    "STAGES",
    "LivePipeline",
    "PipelineError",
    "PipelineReport",
    "evaluate",
    "hot_latest_matches",
    "process_frame",
    "reconcile",
    "report_to_json",
    "run_e2e",
    "warm_readback_matches",
    "write_artifacts",
]
