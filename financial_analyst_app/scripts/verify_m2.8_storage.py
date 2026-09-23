"""M2.8 real-data verify — write sample ticks → read back → P14 rowcount/checksum.

Usage (from financial_analyst_app): python scripts/verify_m2.8_storage.py [--root PATH]
Path lock: scripts/ → _ROOT parents[1] → from modules.m2_market_data...
Exit 0 only if catalog.rowcount == read count and checksum recomputed from file matches.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.m2_market_data.storage import LakeReader, LakeWriter  # noqa: E402
from shared.contracts.market_data import MarketData, Tick  # noqa: E402

BASE = datetime(2026, 9, 23, 14, 2, 0, tzinfo=timezone.utc)
DAY2 = datetime(2026, 9, 24, 10, 0, 0, tzinfo=timezone.utc)


def _tick(price: float, wm: int, sei: int, *, exchange: datetime | None = None) -> MarketData:
    ts = exchange or BASE
    return MarketData(
        instrument_id="FX.EURUSD",
        symbol="EURUSD",
        broker="deriv",
        kind="tick",
        payload=Tick(price=price, bid=price - 0.00005, ask=price + 0.00005, spread=0.0001),
        watermark=wm,
        exchange_timestamp=ts,
        received_at=ts,
        provider="deriv",
        provider_symbol="frxEURUSD",
        source_sequence=sei,
        reference_snapshot_id="REF-VERIFY",
    )


def _sha(rows: list[MarketData]) -> str:
    joined = "\n".join(md.model_dump_json() for md in rows)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _belongs(m: MarketData, e) -> bool:
    # Full partition-key membership (provider, instrument, date, data_type, interval)
    if m.exchange_timestamp is None:
        return False
    # Same effective-provider rule as storage contract (md.provider or md.broker)
    if (m.provider or m.broker) != e.provider:
        return False
    if m.instrument_id != e.instrument_id:
        return False
    if m.exchange_timestamp.date().isoformat() != e.date:
        return False
    kind_map = {"tick": "ticks", "quote": "ticks", "trade": "ticks", "candle": "candles"}
    if kind_map.get(m.kind) != e.data_type:
        return False
    if e.data_type == "candles":
        interval = getattr(m.payload, "interval", None)
        return interval == e.interval
    return e.interval is None


def _key_of(row: dict) -> tuple:
    return (
        row["provider"],
        row["instrument_id"],
        row["date"],
        row["data_type"],
        row["interval"],
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=None, help="lake root (default temp)")
    args = parser.parse_args()

    # Realistic-ish sample from data/sample_ticks_realistic.jsonl if present, else hand fixtures
    sample_path = _ROOT / "data" / "sample_ticks_realistic.jsonl"
    rows: list[MarketData] = []
    if sample_path.is_file():
        for i, line in enumerate(sample_path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            raw = json.loads(line)
            # sample is raw broker frames — use fixed UTC stamps + watermark sequence
            price = float(raw.get("bid") or raw.get("ask") or raw.get("price") or 1.1)
            ts = BASE.replace(minute=i % 60, second=i % 60)
            rows.append(
                MarketData(
                    instrument_id="FX.EURUSD",
                    symbol="EURUSD",
                    broker="deriv",
                    kind="tick",
                    payload=Tick(
                        price=price,
                        bid=price - 0.00005,
                        ask=price + 0.00005,
                        spread=0.0001,
                    ),
                    watermark=1000 + i,
                    exchange_timestamp=ts,
                    received_at=ts,
                    provider="deriv",
                    provider_symbol="frxEURUSD",
                    source_sequence=80000 + i,
                    reference_snapshot_id="REF-SAMPLE",
                )
            )
            if len(rows) >= 20:
                break
    if not rows:
        rows = [_tick(1.10000 + i * 0.0001, 1000 + i, 80000 + i) for i in range(5)]
    # Nit: force a second date so verify demos 2 partitions → 2 catalog keys
    rows.append(_tick(1.10990, 1100, 80100, exchange=DAY2))

    tmp_ctx = None
    if args.root is not None:
        lake_root = args.root
        lake_root.mkdir(parents=True, exist_ok=True)
    else:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="m28_verify_")
        lake_root = Path(tmp_ctx.name)

    try:
        w = LakeWriter(lake_root=lake_root)
        r = LakeReader()
        entries = await w.write_batch(rows)
        print(f"source\tn={len(rows)}\tlake={lake_root}")
        ok = True
        for e in entries:
            back = await r.read_partition(
                lake_root=lake_root,
                provider=e.provider,
                instrument_id=e.instrument_id,
                date=e.date,
                data_type=e.data_type,
                interval=e.interval,
            )
            recomputed = _sha(back)
            row_ok = e.rowcount == len(back)
            sum_ok = e.checksum == recomputed
            # order: exact partition-key rows in write order (not date-only filter)
            expected = [m for m in rows if _belongs(m, e)]
            order_ok = [m.model_dump_json() for m in back] == [
                m.model_dump_json() for m in expected
            ]
            print(
                f"partition\t{e.relative_path}\n"
                f"rowcount\tcatalog={e.rowcount}\tread={len(back)}\tok={row_ok}\n"
                f"checksum\t{e.checksum[:16]}…\tre={recomputed[:16]}…\tok={sum_ok}\n"
                f"order\twrite_order_preserved\tpartition_key_rows={len(expected)}\tok={order_ok}\n"
                f"watermark\t{e.watermark_min}..{e.watermark_max}\n"
                f"source_seq\t{e.source_sequence_min}..{e.source_sequence_max}\n"
                f"schema\t{e.schema_version}\tsnapshot={e.reference_snapshot_id}"
            )
            if not (row_ok and sum_ok and order_ok):
                ok = False
        # catalog: one physical line per exact 5-tuple key; keys unique + match entries
        cat = lake_root / "catalog" / "catalog.jsonl"
        lines = [ln for ln in cat.read_text(encoding="utf-8").splitlines() if ln.strip()]
        cat_keys = [_key_of(json.loads(ln)) for ln in lines]
        expected_keys = [
            (e.provider, e.instrument_id, e.date, e.data_type, e.interval) for e in entries
        ]
        unique_ok = len(cat_keys) == len(set(cat_keys))
        match_ok = sorted(cat_keys) == sorted(expected_keys)
        print(
            f"catalog\tlines={len(lines)}\tunique_keys={unique_ok}\t"
            f"match_entries={match_ok}\tfile={cat}"
        )
        if len(lines) != len(entries) or not unique_ok or not match_ok:
            ok = False
        print(f"partitions\t{len(entries)}")
        print("EXIT\t0" if ok else "EXIT\t1")
        return 0 if ok else 1
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
