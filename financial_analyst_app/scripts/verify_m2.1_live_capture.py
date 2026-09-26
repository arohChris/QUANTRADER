#!/usr/bin/env python3
"""M2.1-live capture — REAL network capture -> production pipeline -> real lake -> read-back.

WHY: closes the G0 Known Bug *"the data layer has never held real data"* — no script in
this repo has ever taken live Deriv ticks, pushed them through the frozen M2 chain
(TickIngestor -> Normalizer -> validate), stored them via M2.8 LakeWriter in the real
`data/lake/` tree, and asserted the read-back equals what the wire delivered.

Spec: docs/specs/m2.1_live_capture.spec.md (LGTM'd tests: test_m2.1_live_capture_spec.py).
Endpoint: config/brokers/deriv.yaml ws_url + app_id (A41 signed) — never hardcoded.
Frame: capture.build_capture_frame — app_id in URL ONLY (body app_id rejected by feed).

Usage (from financial_analyst_app):
    python scripts/verify_m2.1_live_capture.py [--symbol cryBTCUSD] [--ticks 50]
                                               [--timeout-s 60] [--connect-retries 8]
                                               [--lake-root data/lake] [--out-dir data/live_capture]
Exit: 0 = PASS (captured + stored + read-back asserted)
      1 = verify fail (empty capture / pipeline / read-back mismatch)
      2 = connect or protocol failure (DNS, TLS, API error)
      3 = market closed (MarketIsClosed with open-time printed)
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

import yaml
from websockets.sync.client import connect

from modules.m2_market_data import capture
from modules.m2_market_data.ingest.ingest import TickIngestor
from modules.m2_market_data.normalize import Normalizer
from modules.m2_market_data.storage import LakeReader, LakeWriter
from modules.m2_market_data.validate import validate
from modules.m2_market_data.watermark import WatermarkRegistry
from modules.reference import reference
from shared.contracts.drop import Drop
from shared.contracts.internal_tick import InternalTick
from shared.contracts.market_data import MarketData, Tick
from shared.core.clock import SimulatedClock

EXIT_OK, EXIT_FAIL, EXIT_CONNECT, EXIT_CLOSED = 0, 1, 2, 3


class _ReferenceFacade:
    """Same facade pattern as verify_m2.4 — real config reads, no fixture stubs."""

    def current_snapshot_id(self) -> str | None:
        return reference.current_snapshot_id()

    def to_canonical(self, native: str, broker: str) -> str:
        return reference.to_canonical(native, broker)

    def exists(self, instrument_id: str, broker: str) -> bool:
        return reference.exists(instrument_id, broker)

    def get_instrument(self, instrument_id: str) -> dict:
        return reference.get_instrument(instrument_id)


def _load_endpoint() -> str:
    cfg_path = _ROOT / "config" / "brokers" / "deriv.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    ws_url = cfg.get("ws_url")
    app_id = str(cfg.get("app_id") or "")
    if not ws_url or not app_id:
        raise SystemExit(f"[EXIT 2] deriv.yaml missing ws_url/app_id: {cfg_path}")
    sep = "&" if "?" in str(ws_url) else "?"
    return f"{ws_url}{sep}app_id={app_id}"


def _collect(url: str, symbol: str, n_ticks: int, timeout_s: float, retries: int) -> list[str]:
    """Connect (retried — local DNS is flaky), subscribe, collect up to n live tick frames."""
    frame = capture.build_capture_frame(symbol)
    raws: list[str] = []
    # WHY sync client: CLI verify tool; retry helper is sync by spec/test contract.
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
                err = (msg.get("error") or msg.get("tick", {}).get("error") or {})
                opens = (err.get("code_args") or ["unknown"])[0]
                ws.close()
                raise SystemExit(
                    f"[EXIT 3] MarketIsClosed for {symbol!r} — market opens {opens} (UTC per feed)"
                )
            elif kind == "error":
                err = msg.get("error") or {}
                ws.close()
                raise SystemExit(
                    f"[EXIT 2] API error: {err.get('code')}: {err.get('message', '')[:200]}"
                )
            # kind == "other": heartbeat/subscription ack — ignored (spec E2)
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001, S110 — close is best-effort, data already collected
            pass
    return raws


async def _store(lake_root: Path, mds: list[MarketData]) -> None:
    await LakeWriter(lake_root=lake_root).write_batch(mds)


async def _readback(lake_root: Path, instrument_id: str, day: str) -> list[MarketData]:
    return await LakeReader().read_partition(
        lake_root=lake_root,
        provider="deriv",
        instrument_id=instrument_id,
        date=day,
        data_type="ticks",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="cryBTCUSD", help="broker-native symbol (24/7 default)")
    ap.add_argument("--ticks", type=int, default=50)
    ap.add_argument("--timeout-s", type=float, default=60.0)
    ap.add_argument("--connect-retries", type=int, default=8)
    ap.add_argument("--lake-root", type=Path, default=_ROOT / "data" / "lake")
    ap.add_argument("--out-dir", type=Path, default=_ROOT / "data" / "live_capture")
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
        print(f"[EXIT 1] empty capture: 0 ticks within {args.timeout_s}s — run NEVER passes (E3)")
        return EXIT_FAIL

    # --- artifact: raw frames as captured (with captured_at lineage) ------------
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    out_dir = args.out_dir / day
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"raw_{symbol}.jsonl"
    captured: list[dict] = []
    with raw_path.open("w", encoding="utf-8") as fh:
        for raw in raws:
            obj = json.loads(raw)
            obj["captured_at"] = datetime.now(UTC).isoformat()
            fh.write(capture.to_jsonl_line(obj) + "\n")
            captured.append(obj)
    print(f"[ok] captured={len(captured)} -> {raw_path}")

    # --- production pipeline: TickIngestor -> Normalizer -> validate -----------
    ing = TickIngestor(provider="deriv", clock=SimulatedClock(datetime.now(UTC)))
    norm = Normalizer(reference=_ReferenceFacade(), watermark_registry=WatermarkRegistry())
    accepted: list[MarketData] = []
    drops: list[dict] = []
    for obj, raw in zip(captured, raws):
        ep = obj.get("tick", {}).get("epoch")
        ep_int = ep if isinstance(ep, int) and not isinstance(ep, bool) else None
        ing_result = ing.ingest(raw)
        if isinstance(ing_result, Drop):
            drops.append({"epoch": ep_int, "reason": str(ing_result.reason), "detail": ing_result.detail})
            continue
        if not isinstance(ing_result, InternalTick):
            drops.append({"epoch": ep_int, "reason": "ingest_rejected", "detail": repr(ing_result)[:160]})
            continue
        norm_result = norm.normalize(ing_result)
        if not isinstance(norm_result, MarketData):
            drops.append({"epoch": ep_int, "reason": str(norm_result.reason), "detail": norm_result.detail})
            continue
        v = validate(norm_result)
        if not isinstance(v, MarketData):
            drops.append({"epoch": ep_int, "reason": str(v.reason), "detail": v.detail})
            continue
        accepted.append(v)

    print(f"[pipeline] accepted={len(accepted)} dropped={len(drops)}")
    if not accepted:
        for d in drops[:5]:
            print(f"  drop epoch={d['epoch']} reason={d['reason']} detail={str(d['detail'])[:140]}")
        drops_path = out_dir / f"drops_{symbol}.jsonl"
        drops_path.write_text(
            "".join(capture.to_jsonl_line(d) + "\n" for d in drops), encoding="utf-8"
        )
        print(f"[EXIT 1] pipeline accepted 0/{len(captured)} — see {drops_path}")
        return EXIT_FAIL

    # --- store into the REAL lake (frozen M2.8 partition formula) ---------------
    args.lake_root.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(_store(args.lake_root, accepted))
    except Exception as exc:  # noqa: BLE001 — durable path failure must be loud, not silent
        print(f"[EXIT 1] LakeWriter failed: {type(exc).__name__}: {exc}")
        return EXIT_FAIL
    print(f"[ok] wrote {len(accepted)} MarketData rows -> {args.lake_root}")

    # --- read-back + asserts (spec E4-E8) ---------------------------------------
    instrument_id = accepted[0].instrument_id
    if not instrument_id:
        print(f"[EXIT 1] pipeline produced no canonical instrument_id for {symbol!r}")
        return EXIT_FAIL
    try:
        md_rows = asyncio.run(_readback(args.lake_root, instrument_id, day))
    except Exception as exc:  # noqa: BLE001
        print(f"[EXIT 1] read-back failed: {type(exc).__name__}: {exc}")
        return EXIT_FAIL
    rows = [
        {
            "epoch": int(md.exchange_timestamp.timestamp()),
            "price": md.payload.price,
            "reference_snapshot_id": md.reference_snapshot_id,
        }
        for md in md_rows
        if md.exchange_timestamp is not None and isinstance(md.payload, Tick)
    ]
    run_epochs = {
        o["tick"]["epoch"]
        for o in captured
        if isinstance(o.get("tick", {}).get("epoch"), int)
    }
    rows = capture.filter_run_rows(rows, run_epochs)
    inst = reference.get_instrument(instrument_id)
    tick_size = inst["tick_size"]

    drops_path = out_dir / f"drops_{symbol}.jsonl"
    drops_path.write_text(
        "".join(capture.to_jsonl_line(d) + "\n" for d in drops), encoding="utf-8"
    )
    try:
        report = capture.assert_readback(
            captured=captured, rows=rows, drops=drops, tick_size=tick_size
        )
    except capture.CaptureMismatchError as exc:
        print(f"[EXIT 1] READBACK FAIL — {exc}")
        return EXIT_FAIL

    print(
        f"[PASS] captured={report.captured} accepted={report.accepted} dropped={report.dropped} "
        f"first={report.first_price} last={report.last_price} "
        f"instrument={instrument_id} tick_size={tick_size}"
    )
    print(f"[PASS] lake={args.lake_root} raw={raw_path} drops={drops_path}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
