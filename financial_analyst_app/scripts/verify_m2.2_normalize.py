#!/usr/bin/env python3
"""
M2.2 Step 7 — Realistic tick replay verify (InternalTick → MarketData | Drop).
Usage: python scripts/verify_m2.2_normalize.py --input data/sample_ticks_realistic.jsonl --output /tmp/m22_out.txt
Reads JSONL frames → M2.1 TickIngestor accepts → M2.2 Normalizer → prints accept/drop summary + first MarketData.
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
from modules.m2_market_data.watermark import WatermarkRegistry  # noqa: E402
from modules.reference import reference  # noqa: E402
from shared.contracts.drop import Drop  # noqa: E402
from shared.contracts.internal_tick import InternalTick  # noqa: E402
from shared.contracts.market_data import MarketData, Tick  # noqa: E402
from shared.core.clock import SimulatedClock  # noqa: E402


class _ReferenceFacade:
    """Thin sync adapter over M2b module facade (spec: injected deps)."""

    def current_snapshot_id(self) -> str:
        # Fixture until M2b snapshot rotation lands — lineage non-empty for verify
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

    md_accepts: list[MarketData] = []
    norm_drops: list[Drop] = []
    ing_accepts: list[InternalTick] = []
    ing_drops: list[Drop] = []

    lines = Path(args.input).read_text(encoding="utf-8").splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        frame = line
        ing_result = ing.ingest(frame)
        if isinstance(ing_result, InternalTick):
            ing_accepts.append(ing_result)
            norm_result = norm.normalize(ing_result)
            if isinstance(norm_result, MarketData):
                md_accepts.append(norm_result)
            else:
                norm_drops.append(norm_result)
        else:
            ing_drops.append(ing_result)

    out: list[str] = []
    out.append(f"input={args.input}")
    out.append(f"ingest_accepts={len(ing_accepts)} ingest_drops={len(ing_drops)}")
    out.append(f"normalize_accepts={len(md_accepts)} normalize_drops={len(norm_drops)}")

    drop_counts: dict[str, int] = {}
    for d in norm_drops:
        drop_counts[d.reason.value] = drop_counts.get(d.reason.value, 0) + 1
    if drop_counts:
        out.append(f"normalize_drop_reasons={drop_counts}")

    out.append("--- first 5 MarketData ---")
    for md in md_accepts[:5]:
        if not isinstance(md.payload, Tick):
            continue
        tick = md.payload
        out.append(
            f"wm={md.watermark} inst={md.instrument_id} sym={md.symbol} "
            f"provider_sym={md.provider_symbol} broker={md.broker} "
            f"price={tick.price} bid={tick.bid} ask={tick.ask} spread={tick.spread} "
            f"quality={md.quality.value} snap={md.reference_snapshot_id} "
            f"src_seq={md.source_sequence} src_ev={md.source_event_id} "
            f"exch={md.exchange_timestamp.isoformat() if md.exchange_timestamp else None} "
            f"recv={md.received_at.isoformat() if md.received_at else None}"
        )

    if norm_drops:
        out.append("--- first 5 normalize drops ---")
        for d in norm_drops[:5]:
            out.append(f"reason={d.reason.value} provider={d.provider} detail={d.detail!r}")

    # Hand-check identity: first EURUSD tick must be FX.EURUSD / EURUSD / frxEURUSD
    eur = next((m for m in md_accepts if m.provider_symbol == "frxEURUSD"), None)
    if eur:
        out.append("--- hand-check EURUSD identity ---")
        out.append(
            f"instrument_id={eur.instrument_id} symbol={eur.symbol} "
            f"provider_symbol={eur.provider_symbol} (expect FX.EURUSD / EURUSD / frxEURUSD)"
        )
        ok = (
            eur.instrument_id == "FX.EURUSD"
            and eur.symbol == "EURUSD"
            and eur.provider_symbol == "frxEURUSD"
        )
        out.append(f"identity_ok={ok}")
    else:
        out.append("hand-check EURUSD: no frxEURUSD in accepts")
        ok = False

    text = "\n".join(out) + "\n"
    if args.output == "-":
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
        sys.stdout.write(text)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
