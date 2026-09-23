"""M2.6 real-data verify — QualityGate on sample ticks (no network).

Usage: python scripts/verify_m2.6_quality.py [--input PATH]
Prints per-tick: watermark, sei, quality, drop reason (if any).
Forces one duplicate (repeat first sei) and one synthetic spike.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# app root on path when run as scripts/verify_*.py
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.m2_market_data.quality import QualityGate  # noqa: E402
from shared.contracts.drop import Drop  # noqa: E402
from shared.contracts.market_data import DataQuality, MarketData, Tick  # noqa: E402

BASE = datetime(2026, 9, 23, 14, 2, 0, tzinfo=timezone.utc)


def _md(price: float, sei: str, wm: int, quality: DataQuality = DataQuality.pristine) -> MarketData:
    return MarketData(
        instrument_id="FX.EURUSD",
        symbol="EURUSD",
        broker="deriv",
        kind="tick",
        payload=Tick(price=price, bid=price - 0.00005, ask=price + 0.00005, spread=0.0001),
        quality=quality,
        watermark=wm,
        exchange_timestamp=BASE,
        received_at=BASE,
        provider="deriv",
        source_event_id=sei,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=None, help="optional JSONL of MarketData-like dicts")
    args = ap.parse_args()

    gate = QualityGate()
    samples: list[MarketData]
    if args.input and args.input.exists():
        samples = []
        for i, line in enumerate(args.input.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            samples.append(
                _md(float(row["price"]), str(row.get("source_event_id") or f"evt-{i}"), i)
            )
    else:
        # synthetic: accept ×2, forced duplicate, small move, 6% spike, gapped passthrough
        # Hand calc spike: prev 1.11100 → 1.17700, Δ=0.066, 0.066/1.111≈0.0594 > 0.05 → suspect
        samples = [
            _md(1.10000, "evt-1", 1),
            _md(1.10000, "evt-1", 2),  # forced duplicate
            _md(1.11100, "evt-3", 3),  # ~1% → pristine
            _md(1.17700, "evt-4", 4),  # ~5.94% → suspect
            _md(1.17710, "evt-5", 5, quality=DataQuality.gapped),  # never clear
        ]

    print("wm\tsei\tquality\tresult")
    accepted = dropped = 0
    for md in samples:
        out = gate.on_md(md)
        sei = md.source_event_id or "-"
        if isinstance(out, Drop):
            dropped += 1
            print(f"{md.watermark}\t{sei}\t-\tDROP {out.reason.value}")
        else:
            accepted += 1
            print(f"{out.watermark}\t{sei}\t{out.quality.value}\tACCEPT")
    print(f"# accepted={accepted} dropped={dropped}")
    return 0 if accepted >= 3 and dropped >= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
