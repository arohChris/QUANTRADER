#!/usr/bin/env python3
"""
M2.1 Step 7 — Realistic tick replay verify.
Usage: python scripts/verify_tick_ingest.py --input data/sample_ticks_realistic.jsonl --output /tmp/m21_out.txt
Reads JSONL frames (Deriv-shaped tick schema), runs TickIngestor, prints accept/drop summary + first accepts.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.m2_market_data.ingest.tick_ingest import TickIngestor  # noqa: E402
from shared.contracts.drop import Drop  # noqa: E402
from shared.contracts.internal_tick import InternalTick  # noqa: E402
from shared.core.clock import SimulatedClock  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="-")
    ap.add_argument(
        "--clock",
        default="2026-03-22T19:47:00+00:00",
        help="ISO clock now(); default is just after sample epochs 1774208800-8810 UTC",
    )
    args = ap.parse_args()

    clock = SimulatedClock(datetime.fromisoformat(args.clock))
    ing = TickIngestor(provider="deriv", clock=clock)

    accepts: list[InternalTick] = []
    drops: list[Drop] = []
    lines = Path(args.input).read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        # Each JSONL line may be a full frame string or a JSON object — wrap as frame
        frame = line if line.startswith("{") else json.dumps(json.loads(line))
        # If line is already a JSON object of the tick body, re-encode as frame string
        if line.startswith("{") and '"symbol"' in line:
            frame = line
        result = ing.ingest(frame)
        if isinstance(result, InternalTick):
            accepts.append(result)
        else:
            drops.append(result)

    out: list[str] = []
    out.append(f"input={args.input} frames={len(accepts) + len(drops)}")
    out.append(f"accepts={len(accepts)} drops={len(drops)}")
    drop_counts: dict[str, int] = {}
    for d in drops:
        drop_counts[d.reason.value] = drop_counts.get(d.reason.value, 0) + 1
    if drop_counts:
        out.append(f"drop_reasons={drop_counts}")
    out.append("--- first 5 accepts ---")
    for t in accepts[:5]:
        out.append(
            f"ps={t.processing_sequence} src_ev={t.source_event_id} "
            f"sym={t.symbol} bid={t.bid} ask={t.ask} quote={t.quote} pip={t.pip_size} "
            f"q={t.quality} epoch_ts={t.timestamp.isoformat()} observed={t.observed_at.isoformat()}"
        )
    if drops:
        out.append("--- first 5 drops ---")
        for d in drops[:5]:
            out.append(f"reason={d.reason.value} detail={d.detail!r}")

    text = "\n".join(out) + "\n"
    if args.output == "-":
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
