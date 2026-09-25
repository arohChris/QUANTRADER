#!/usr/bin/env python3
"""
M2.4 Step 7 — Realistic tick replay verify (tick → Candle 1m).
Usage: python scripts/verify_m2.4_candles.py --input data/sample_ticks_realistic.jsonl --output /tmp/m24_out.txt
Reads JSONL → M2.1 → M2.2 → M2.3 → M2.4 CandleBuilder → prints candle summary + first 5.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.m2_market_data.candles import CandleBuilder  # noqa: E402
from modules.m2_market_data.ingest.ingest import TickIngestor  # noqa: E402
from modules.m2_market_data.normalize import Normalizer  # noqa: E402
from modules.m2_market_data.validate import validate  # noqa: E402
from modules.m2_market_data.watermark import WatermarkRegistry  # noqa: E402
from modules.reference import reference  # noqa: E402
from shared.contracts.drop import Drop  # noqa: E402
from shared.contracts.internal_tick import InternalTick  # noqa: E402
from shared.contracts.market_data import Candle, MarketData, Tick  # noqa: E402
from shared.core.clock import SimulatedClock  # noqa: E402


class _ReferenceFacade:
    def current_snapshot_id(self) -> str | None:
        # M2b.2: real facade reads config/reference/snapshot.yaml (no fixture stub)
        return reference.current_snapshot_id()

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
    candles: list[Candle] = []
    builders: dict[str, CandleBuilder] = {}

    lines = Path(args.input).read_text(encoding="utf-8").splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        ing_result = ing.ingest(line)
        if not isinstance(ing_result, InternalTick):
            continue
        norm_result = norm.normalize(ing_result)
        if not isinstance(norm_result, MarketData):
            continue
        v = validate(norm_result)
        if not isinstance(v, MarketData):
            validate_drops.append(v)
            continue
        accepted.append(v)

        key = f"{v.instrument_id}|{v.broker}"
        if key not in builders:
            builders[key] = CandleBuilder(
                instrument_id=v.instrument_id or "unknown",
                broker=v.broker,
                interval="1m",
            )
        r = builders[key].on_tick(v)
        if isinstance(r, Candle):
            candles.append(r)

    # flush all open buckets
    for b in builders.values():
        c = b.close()
        if c is not None:
            candles.append(c)

    out: list[str] = []
    out.append(f"input={args.input}")
    out.append(f"validate_accepts={len(accepted)} validate_drops={len(validate_drops)}")
    out.append(f"candles_closed={len(candles)} builders={len(builders)}")

    out.append("--- first 5 candles ---")
    for c in candles[:5]:
        out.append(
            f"inst_interval={c.interval} start={c.interval_start.isoformat()} "
            f"O={c.open} H={c.high} L={c.low} C={c.close} "
            f"n={c.tick_count} avg_sp={c.average_spread} "
            f"min_sp={c.min_spread} max_sp={c.max_spread} "
            f"quality={c.quality_status} ver={c.aggregation_version}"
        )

    # hand-check: first candle must have tick_count >= 1 and O/H/L/C set
    ok = len(candles) > 0 and all(
        c.tick_count >= 1 and c.open > 0 and c.high >= c.low for c in candles
    )
    out.append(f"candle_sanity_ok={ok}")

    text = "\n".join(out) + "\n"
    if args.output == "-":
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
