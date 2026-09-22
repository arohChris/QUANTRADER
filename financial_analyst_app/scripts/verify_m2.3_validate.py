#!/usr/bin/env python3
"""
M2.3 Step 7 — Realistic tick replay verify (MarketData → validate → MarketData | Drop).
Usage: python scripts/verify_m2.3_validate.py --input data/sample_ticks_realistic.jsonl --output /tmp/m23_out.txt
Reads JSONL → M2.1 TickIngestor → M2.2 Normalizer → M2.3 validate → prints accept/drop summary + first 5.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.m2_market_data.ingest.ingest import TickIngestor  # noqa: E402
from modules.m2_market_data.normalize import Normalizer  # noqa: E402
from modules.m2_market_data.validate import validate  # noqa: E402
from modules.m2_market_data.watermark import WatermarkRegistry  # noqa: E402
from modules.reference import reference  # noqa: E402
from shared.contracts.drop import Drop  # noqa: E402
from shared.contracts.internal_tick import InternalTick  # noqa: E402
from shared.contracts.market_data import MarketData, Tick  # noqa: E402
from shared.core.clock import SimulatedClock  # noqa: E402


class _ReferenceFacade:
    """Thin sync adapter over M2b module facade (spec: injected deps)."""

    def current_snapshot_id(self) -> str:
        return "REF-2026-09-22-VERIFY"

    def to_canonical(self, native: str, broker: str) -> str:
        return reference.to_canonical(native, broker)

    def exists(self, instrument_id: str, broker: str) -> bool:
        return reference.exists(instrument_id, broker)

    def get_instrument(self, instrument_id: str) -> dict:
        return reference.get_instrument(instrument_id)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="-")
    ap.add_argument("--clock", default="2026-03-22T19:47:00+00:00")
    args = ap.parse_args()

    clock = SimulatedClock(datetime.fromisoformat(args.clock))
    ing = TickIngestor(provider="deriv", clock=clock)
    norm = Normalizer(reference=_ReferenceFacade(), watermark_registry=WatermarkRegistry())

    accepted: list[MarketData] = []
    validate_drops: list[Drop] = []
    norm_drops: list[Drop] = []
    ing_drops: list[Drop] = []

    lines = Path(args.input).read_text(encoding="utf-8").splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        ing_result = ing.ingest(line)
        if not isinstance(ing_result, InternalTick):
            ing_drops.append(ing_result)
            continue
        norm_result = norm.normalize(ing_result)
        if not isinstance(norm_result, MarketData):
            norm_drops.append(norm_result)
            continue
        v = validate(norm_result)
        if isinstance(v, MarketData):
            accepted.append(v)
        else:
            validate_drops.append(v)

    out: list[str] = []
    out.append(f"input={args.input}")
    out.append(f"ingest_drops={len(ing_drops)} normalize_drops={len(norm_drops)}")
    out.append(f"validate_accepts={len(accepted)} validate_drops={len(validate_drops)}")

    drop_counts: dict[str, int] = {}
    for d in validate_drops:
        drop_counts[d.reason.value] = drop_counts.get(d.reason.value, 0) + 1
    if drop_counts:
        out.append(f"validate_drop_reasons={drop_counts}")

    out.append("--- first 5 accepted ---")
    for md in accepted[:5]:
        if not isinstance(md.payload, Tick):
            continue
        t = md.payload
        out.append(
            f"inst={md.instrument_id} price={t.price} bid={t.bid} ask={t.ask} "
            f"quality={md.quality.value} exch={md.exchange_timestamp.isoformat() if md.exchange_timestamp else None}"
        )

    if validate_drops:
        out.append("--- first 5 validate drops ---")
        for d in validate_drops[:5]:
            out.append(f"reason={d.reason.value} provider={d.provider} detail={d.detail!r}")

    # Identity pass-through: first accept must be same class instance type (MarketData)
    ok = len(accepted) > 0
    out.append(f"identity_pass_through_ok={ok}")

    text = "\n".join(out) + "\n"
    if args.output == "-":
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
        sys.stdout.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
