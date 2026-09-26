#!/usr/bin/env python3
"""Step 7 live verify: M2.11 end-to-end — Deriv WS -> pipeline -> HOT -> WARM -> read-back.

Run:     python scripts/verify_m2.11_live_e2e.py [--symbol cryBTCUSD] [--ticks 50] [--timeout-s 90]
Needs:   Valkey :6379 + Timescale :5432 Up (infra/docker-compose.yml),
         QUANTRADER2/.env TIMESCALE_DEV_PASSWORD (value never printed), .env not logged.
Spec:    docs/specs/m2.11_live_e2e.spec.md — §4 C1-C7, §5 E1-E12, §7 exit codes.
Covers:  real WS collect -> LivePipeline (frozen ingest/normalize/validate/builder)
         -> HOT set_latest/get_latest (live Valkey) -> WARM flush_batch/read_1m
         (live Timescale, DDL idempotent first) -> lake lineage rows (A42) ->
         C1-C5 via evaluate() + C6 rerun-evidence + C7 no-password-in-artifacts,
         ADVISORY per-stage timings (E12, never gated).
Exit:    0 = all C hold / 1 = reconcile-or-store fail / 2 = connect-or-API fail /
         3 = market closed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import asyncpg
import yaml
from websockets.sync.client import connect

from modules.m2_market_data import capture
from modules.m2_market_data.hot import HotStore
from modules.m2_market_data.ingest.ingest import TickIngestor
from modules.m2_market_data.normalize import Normalizer
from modules.m2_market_data.pipeline import (
    LivePipeline,
    PipelineError,
    evaluate,
    report_to_json,
    run_e2e,
    write_artifacts,
)
from modules.m2_market_data.storage import LakeReader, LakeWriter
from modules.m2_market_data.warm import Candles1mStore, WarmConfig, apply_ddl, redact_dsn
from modules.m2_market_data.watermark import WatermarkRegistry
from modules.reference import reference
from shared.core.clock import SimulatedClock

EXIT_OK, EXIT_FAIL, EXIT_CONNECT, EXIT_CLOSED = 0, 1, 2, 3
VALKEY_URL = "redis://127.0.0.1:6379/0"  # infra/docker-compose.yml, loopback only
_REPO = _ROOT.parent
_fails = 0


class _ReferenceFacade:
    """Same facade pattern as verify_m2.1_live_capture — real config reads."""

    def current_snapshot_id(self) -> str | None:
        return reference.current_snapshot_id()

    def to_canonical(self, native: str, broker: str) -> str:
        return reference.to_canonical(native, broker)

    def exists(self, instrument_id: str, broker: str) -> bool:
        return reference.exists(instrument_id, broker)

    def get_instrument(self, instrument_id: str) -> dict:
        return reference.get_instrument(instrument_id)


def _check(label: str, ok: bool, detail: str = "") -> bool:
    global _fails
    if not ok:
        _fails += 1
    suffix = f" ({detail})" if detail else ""
    print(f"[{'OK' if ok else 'FAIL'}] {label}{suffix}")
    return ok


def _password() -> str:
    env = _REPO / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("TIMESCALE_DEV_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"TIMESCALE_DEV_PASSWORD missing from {env}")


def _load_endpoint() -> str:
    cfg_path = _ROOT / "config" / "brokers" / "deriv.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    ws_url, app_id = cfg.get("ws_url"), str(cfg.get("app_id") or "")
    if not ws_url or not app_id:
        raise SystemExit(f"[EXIT 2] deriv.yaml missing ws_url/app_id: {cfg_path}")
    sep = "&" if "?" in str(ws_url) else "?"
    return f"{ws_url}{sep}app_id={app_id}"


def _collect(url: str, symbol: str, n_ticks: int, timeout_s: float, retries: int) -> list[str]:
    """Sync WS collect — m2.1 pattern (classify: tick/closed/error/other)."""
    frame = capture.build_capture_frame(symbol)
    raws: list[str] = []
    ws = capture.connect_with_retry(
        lambda: connect(url, open_timeout=10), attempts=retries, sleep=time.sleep
    )
    try:
        ws.send(frame)
        deadline = time.monotonic() + timeout_s
        while len(raws) < n_ticks and time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = ws.recv(timeout=remaining)
            except TimeoutError:
                break
            if not isinstance(raw, str):
                continue
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            kind = capture.classify(msg)
            if kind == "tick":
                raws.append(raw)
            elif kind == "closed":
                err = msg.get("error") or {}
                opens = (err.get("code_args") or ["unknown"])[0]
                ws.close()
                raise SystemExit(f"[EXIT 3] MarketIsClosed for {symbol!r} — market opens {opens}")
            elif kind == "error":
                err = msg.get("error") or {}
                ws.close()
                raise SystemExit(
                    f"[EXIT 2] API error: {err.get('code')}: {err.get('message', '')[:200]}"
                )
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001, S110 — close is best-effort, data already collected
            pass
    return raws


def _run_epochs(raws: list[str]) -> set[int]:
    epochs: set[int] = set()
    for raw in raws:
        try:
            ep = (json.loads(raw).get("tick") or {}).get("epoch")
        except (json.JSONDecodeError, ValueError, AttributeError):
            continue
        if isinstance(ep, int) and not isinstance(ep, bool):
            epochs.add(ep)
    return epochs


async def _verify(args: argparse.Namespace, symbol: str, raws: list[str]) -> int:
    # --- E7 HOT infra (fail-loud — verify must not mask missing Valkey) ---------
    hot = HotStore(args.valkey, default_ttl_seconds=60)
    await hot.connect()

    # --- E8 WARM infra: DDL idempotent first, connect, typed redacted failures ---
    try:
        pw = _password()
        dsn = f"postgresql://quantrader:{pw}@127.0.0.1:5432/quantrader"
        print("target: postgresql://quantrader:***@127.0.0.1:5432/quantrader (never printed)")
        conn = await asyncpg.connect(dsn, timeout=10)
        n1 = await apply_ddl(conn)
        n2 = await apply_ddl(conn)
        _check("WARM DDL idempotent on live Timescale", n1 == 7 and n2 == 7, f"stmts {n1}/{n2}")
        store = Candles1mStore(WarmConfig(dsn=dsn, connect_timeout_s=5), pool=conn)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — infra failure must be loud + redacted (E8/C7)
        print(f"[EXIT 1] WARM unavailable: {redact_dsn(str(exc))}")
        return EXIT_FAIL

    def _fresh() -> LivePipeline:
        # Fresh ingest/registry per run: run2 must not trip run1's A44 dedup keys.
        return LivePipeline(
            ingest=TickIngestor(provider="deriv", clock=SimulatedClock(datetime.now(UTC))),
            normalizer=Normalizer(
                reference=_ReferenceFacade(), watermark_registry=WatermarkRegistry()
            ),
            hot=hot,
            warm=store,
            provider="deriv",
        )

    pipe = _fresh()
    try:
        await pipe.check_infra()  # E7
        report = await run_e2e(
            collect=lambda: raws,
            pipeline=pipe,
            reference_snapshot_id=reference.current_snapshot_id(),
        )
    except PipelineError as exc:
        print(f"[EXIT 1] {redact_dsn(str(exc))}")
        return EXIT_FAIL

    print(
        f"[run1] captured={report.captured} accepted={report.accepted} "
        f"dropped={report.dropped} candles={report.candles_flushed} "
        f"warm_inserted={report.warm_inserted} hot_ok={report.hot_latest_ok}"
    )
    for d in report.drops[:5]:  # C5: first 5 reasons printed
        print(f"  drop epoch={d['epoch']} reason={d['reason']} detail={str(d['detail'])[:120]}")

    # --- S6 lake lineage (A42: lineage rows only, m2.1 filter_run_rows) ---------
    lake_ok = False
    try:
        args.lake_root.mkdir(parents=True, exist_ok=True)
        await LakeWriter(lake_root=args.lake_root).write_batch(pipe.accepted)
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        rows = await LakeReader().read_partition(
            lake_root=args.lake_root,
            provider="deriv",
            instrument_id=pipe.instrument_id or "",
            date=day,
            data_type="ticks",
        )
        kept = capture.filter_run_rows(
            [
                {"epoch": int(m.exchange_timestamp.timestamp())}
                for m in rows
                if m.exchange_timestamp is not None
            ],
            _run_epochs(raws),
        )
        lake_ok = len(kept) == len(_run_epochs(raws))
        _check(f"lake lineage rows for run epochs ({len(kept)})", lake_ok)
    except Exception as exc:  # noqa: BLE001 — durable-path failure is loud, never silent
        print(f"[FAIL] lake lineage: {type(exc).__name__}: {redact_dsn(str(exc))}")

    # --- C1-C5 straight from the approved evaluator -----------------------------
    violations = evaluate(report)
    labels = {
        "C1": f"captured {report.captured} == accepted {report.accepted} + dropped {report.dropped}",
        "C2": f"accepted {report.accepted} >= 1",
        "C3": f"candles {report.candles_flushed} flushed + read-back {report.warm_readback_ok}",
        "C4": f"HOT latest == last accepted md ({report.hot_latest_ok})",
        "C5": f"every drop has reason+detail ({len(report.drops)} drops)",
    }
    for code, label in labels.items():
        _check(f"{code} {label}", code not in violations)

    # --- C6 rerun evidence: same frames, fresh pipeline -> idempotent flush ------
    c6_ok = False
    try:
        pipe2 = _fresh()
        report2 = await run_e2e(collect=lambda: raws, pipeline=pipe2)
        fr1 = await pipe2.flush(report2.instrument_id or "") if report2.instrument_id else None
        fr2 = await pipe2.flush(report2.instrument_id or "") if report2.instrument_id else None
        cursor_ok = fr1 is None or fr2 is None or (
            fr2.cursor.watermark is not None
            and (fr1.cursor.watermark is None or fr2.cursor.watermark >= fr1.cursor.watermark)
        )
        c6_ok = (
            report2.accepted == report.accepted
            and 0 <= report2.warm_inserted <= report.candles_flushed
            and report2.warm_readback_ok
            and cursor_ok
        )
        _check(
            "C6 rerun idempotent: superset read-back + inserted 0..n + cursor monotonic",
            c6_ok,
            f"inserted {report2.warm_inserted}/{report.candles_flushed}, readback {report2.warm_readback_ok}",
        )
    except PipelineError as exc:
        print(f"[FAIL] C6 rerun: {redact_dsn(str(exc))}")

    # --- artifacts (C5: drops file written even when empty) + C7 ---------------
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    paths = write_artifacts(args.out_dir / day, symbol, raws, report.drops, report)
    print(f"[ok] artifacts: raw={paths['raw']} drops={paths['drops']}")
    c7_ok = all(pw not in p.read_text(encoding="utf-8") for p in paths.values())
    _check("C7 no password in artifacts (redaction)", c7_ok)

    # --- E12 ADVISORY timings (never gated) -------------------------------------
    stages = report_to_json(report)["stages"]
    print(
        "[advisory] timings(s): "
        + " ".join(f"{k}={v:.4f}" for k, v in stages.items())
    )

    passed = not violations and c6_ok and c7_ok and lake_ok and _fails == 0
    if passed:
        print(
            f"[PASS] C1-C7 hold — captured={report.captured} candles={report.candles_flushed} "
            f"instrument={report.instrument_id} report={paths['report']}"
        )
    else:
        print(f"[FAIL] violations={violations} c6={c6_ok} c7={c7_ok} lake={lake_ok} fails={_fails}")
    await store.close()
    await hot.close()
    return EXIT_OK if passed else EXIT_FAIL


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="cryBTCUSD", help="24/7 symbol (avoids weekend E3)")
    ap.add_argument("--ticks", type=int, default=50)
    ap.add_argument("--timeout-s", type=float, default=90.0)
    ap.add_argument("--connect-retries", type=int, default=8)
    ap.add_argument("--valkey", default=VALKEY_URL)
    ap.add_argument("--lake-root", type=Path, default=_ROOT / "data" / "lake")
    ap.add_argument("--out-dir", type=Path, default=_ROOT / "data" / "live_e2e")
    args = ap.parse_args()

    try:
        symbol, n_ticks = capture.validate_args(args.symbol, args.ticks)
    except ValueError as exc:
        print(f"[EXIT 2] bad args: {exc}")
        return EXIT_CONNECT

    url = _load_endpoint()
    print(f"[info] endpoint={url.split('?')[0]} symbol={symbol} want={n_ticks} ticks")
    try:
        raws = _collect(url, symbol, n_ticks, args.timeout_s, args.connect_retries)
    except SystemExit as exc:
        print(exc)
        code = str(exc)
        return EXIT_CLOSED if "[EXIT 3]" in code else EXIT_CONNECT
    if not raws:
        print(f"[EXIT 1] empty capture: 0 ticks within {args.timeout_s}s — never passes (E1)")
        return EXIT_FAIL

    return asyncio.run(_verify(args, symbol, raws))


if __name__ == "__main__":
    raise SystemExit(main())
