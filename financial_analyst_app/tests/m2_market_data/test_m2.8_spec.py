"""
P14 / M2.8 Storage — parquet lake + catalog — Spec tests (WRITTEN BEFORE CODE).
WHY: AGENTS.md §2 Step 3 — tests first against docs/specs/m2.8_storage.spec.md
(REV1.1 LGTM ACTION 23 2026-09-23 — freezes: partition_key, no 2PC, rows_in_write_order,
order_book ValueError, JSONL one entry per key).

Import modules.m2_market_data.storage only — collection RED until Step 5.
pyarrow required at Step 5 for parquet asserts; tests that need parquet may skip
ONLY if pyarrow missing — partitioner/path/catalog pure asserts still run.

Gate: partition_path frozen formula · write_batch atomic tmp→replace · CatalogEntry
P14 rowcount+checksum · no user_id in path · partition_key upsert · FileNotFoundError read.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from modules.m2_market_data.storage import (  # noqa: F401
    CatalogEntry,
    LakeReader,
    LakeWriter,
    partition_path,
)

from shared.contracts.market_data import Candle, MarketData, OrderBookSnapshot, Tick

BASE = datetime(2026, 9, 23, 14, 2, 0, tzinfo=timezone.utc)
DAY2 = datetime(2026, 9, 24, 10, 0, 0, tzinfo=timezone.utc)


def _tick_md(
    *,
    exchange: datetime | None = None,
    instrument_id: str | None = "FX.EURUSD",
    broker: str = "deriv",
    provider: str | None = "deriv",
    watermark: int = 100,
    source_sequence: int | None = None,
    reference_snapshot_id: str | None = "REF-A",
    user_id: str | None = None,
    price: float = 1.10000,
) -> MarketData:
    # Tick payload fields are float per MarketData contract (spec consumes as-is)
    payload = Tick(price=price, bid=price - 0.0001, ask=price + 0.0001, spread=0.0002)
    return MarketData(
        instrument_id=instrument_id,
        symbol="EURUSD",
        broker=broker,
        kind="tick",
        payload=payload,
        watermark=watermark,
        exchange_timestamp=exchange if exchange is not None else BASE,
        received_at=BASE,
        provider=provider,
        provider_symbol="frxEURUSD",
        source_sequence=source_sequence,
        reference_snapshot_id=reference_snapshot_id,
        user_id=user_id,
    )


def _candle_md(interval: str = "1m", *, exchange: datetime | None = None) -> MarketData:
    start = exchange or BASE
    payload = Candle(
        open=1.10000,
        high=1.10100,
        low=1.09900,
        close=1.10050,
        tick_count=10,
        interval=interval,
        interval_start=start,
        interval_end=start,
    )
    return MarketData(
        instrument_id="FX.EURUSD",
        symbol="EURUSD",
        broker="deriv",
        kind="candle",
        payload=payload,
        watermark=101,
        exchange_timestamp=start,
        received_at=start,
        provider="deriv",
        provider_symbol="frxEURUSD",
        reference_snapshot_id="REF-A",
    )


def _order_book_md() -> MarketData:
    # Minimal order book payload — only used to assert ValueError Day-1
    payload = OrderBookSnapshot(
        bids=[(1.10000, 1.0)],
        asks=[(1.10010, 1.0)],
        sequence=1,
    )
    return MarketData(
        instrument_id="FX.EURUSD",
        symbol="EURUSD",
        broker="deriv",
        kind="order_book",
        payload=payload,
        watermark=50,
        exchange_timestamp=BASE,
        received_at=BASE,
        provider="deriv",
        reference_snapshot_id="REF-A",
    )


def _expected_checksum(rows: list[MarketData]) -> str:
    # Spec: sha256 over "\n".join(md.model_dump_json() for md in rows_in_write_order)
    joined = "\n".join(md.model_dump_json() for md in rows)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _pyarrow() -> bool:
    try:
        import pyarrow  # noqa: F401

        return True
    except ImportError:
        return False


needs_parquet = pytest.mark.skipif(not _pyarrow(), reason="pyarrow required at Step 5 (spec)")


# --- Partitioner (pure) 1–8 ---


def test_tick_path_exact_golden() -> None:
    # Spec Correct 1 / test plan 1 — frozen path formula
    p = partition_path(_tick_md(), lake_root=Path("data/lake"))
    assert str(p).replace("\\", "/") == (
        "data/lake/market-data/provider=deriv/instrument=FX.EURUSD/"
        "date=2026-09-23/data_type=ticks/ticks.parquet"
    )


def test_candle_path_includes_interval() -> None:
    p = partition_path(_candle_md("5m"), lake_root=Path("data/lake"))
    s = str(p).replace("\\", "/")
    assert "data_type=candles/interval=5m/candles.parquet" in s
    # freeze: canonical Candle.interval as-is (no 5m→other)
    assert "interval=5m" in s


def test_path_ignores_user_id() -> None:
    md = _tick_md(user_id="42")
    p = str(partition_path(md, lake_root=Path("data/lake")))
    assert "user_id" not in p
    assert "42" not in p


def test_provider_falls_back_to_broker() -> None:
    md = _tick_md(provider=None, broker="deriv")
    p = str(partition_path(md, lake_root=Path("data/lake")))
    assert "provider=deriv" in p


def test_missing_instrument_id_valueerror() -> None:
    md = _tick_md(instrument_id=None)
    with pytest.raises(ValueError):
        partition_path(md, lake_root=Path("data/lake"))


def test_missing_exchange_timestamp_valueerror() -> None:
    md = _tick_md()
    md = md.model_copy(update={"exchange_timestamp": None})
    with pytest.raises(ValueError):
        partition_path(md, lake_root=Path("data/lake"))


def test_path_injection_equals_or_slash_valueerror() -> None:
    # Adversarial: injection in instrument_id
    for bad in ("../../etc", "FX=EURUSD", "a/b", "a\\b", "has..dots"):
        md = _tick_md(instrument_id=bad)
        with pytest.raises(ValueError):
            partition_path(md, lake_root=Path("data/lake"))


def test_empty_provider_and_broker_valueerror() -> None:
    md = _tick_md(provider="", broker="")
    with pytest.raises(ValueError):
        partition_path(md, lake_root=Path("data/lake"))


# --- LakeWriter + catalog 9–22 ---


async def test_empty_batch_noop(tmp_path: Path) -> None:
    w = LakeWriter(lake_root=tmp_path)
    out = await w.write_batch([])
    assert out == []
    # no dirs created for empty batch (Correct 6) — only lake_root itself may exist
    market = tmp_path / "market-data"
    assert not market.exists()


@needs_parquet
async def test_single_tick_write_creates_parquet_and_catalog(tmp_path: Path) -> None:
    w = LakeWriter(lake_root=tmp_path)
    entries = await w.write_batch([_tick_md()])
    assert len(entries) == 1
    e = entries[0]
    assert e.data_type == "ticks"
    assert e.interval is None
    assert e.rowcount == 1
    abs_path = tmp_path / e.relative_path
    assert abs_path.is_file()
    assert abs_path.suffix == ".parquet"
    cat = tmp_path / "catalog" / "catalog.jsonl"
    assert cat.is_file()


@needs_parquet
async def test_batch_splits_by_date_two_entries(tmp_path: Path) -> None:
    w = LakeWriter(lake_root=tmp_path)
    entries = await w.write_batch([_tick_md(exchange=BASE), _tick_md(exchange=DAY2)])
    assert len(entries) == 2
    dates = {e.date for e in entries}
    assert dates == {"2026-09-23", "2026-09-24"}
    for e in entries:
        assert e.rowcount == 1


@needs_parquet
async def test_user_id_not_in_created_path(tmp_path: Path) -> None:
    w = LakeWriter(lake_root=tmp_path)
    entries = await w.write_batch([_tick_md(user_id="42")])
    assert entries
    for e in entries:
        assert "user_id" not in e.relative_path
        assert "42" not in e.relative_path
    # relative only — tmp_path itself may contain test name substring "user_id"
    for p in tmp_path.rglob("*"):
        rel = str(p.relative_to(tmp_path))
        assert "user_id" not in rel
        assert "42" not in rel


@needs_parquet
async def test_catalog_upsert_single_entry_after_rewrite(tmp_path: Path) -> None:
    # Spec Correct 5 + freeze: exactly one logical entry per partition_key in physical JSONL
    w = LakeWriter(lake_root=tmp_path)
    await w.write_batch([_tick_md(watermark=100)])
    await w.write_batch([_tick_md(watermark=101, price=1.10020)])
    cat = tmp_path / "catalog" / "catalog.jsonl"
    lines = [ln for ln in cat.read_text(encoding="utf-8").splitlines() if ln.strip()]
    keys = []
    for ln in lines:
        row = json.loads(ln)
        keys.append(
            (
                row["provider"],
                row["instrument_id"],
                row["date"],
                row["data_type"],
                row["interval"],
            )
        )
    # physical file: one line per key after two writes to same partition
    assert len(lines) == 1, f"expected 1 physical catalog line, got {len(lines)}: {lines}"
    assert keys.count(keys[0]) == 1


@needs_parquet
async def test_rowcount_matches_read_back(tmp_path: Path) -> None:
    # P14 Correct 4
    w = LakeWriter(lake_root=tmp_path)
    r = LakeReader()
    rows = [_tick_md(watermark=10 + i) for i in range(3)]
    entries = await w.write_batch(rows)
    e = entries[0]
    back = await r.read_partition(
        lake_root=tmp_path,
        provider=e.provider,
        instrument_id=e.instrument_id,
        date=e.date,
        data_type=e.data_type,
        interval=e.interval,
    )
    assert e.rowcount == len(back) == 3


@needs_parquet
async def test_checksum_matches_recompute(tmp_path: Path) -> None:
    # P14 Correct 4 + rows_in_write_order freeze
    w = LakeWriter(lake_root=tmp_path)
    r = LakeReader()
    rows = [
        _tick_md(watermark=200, price=1.10000),
        _tick_md(watermark=201, price=1.10020),
    ]
    entries = await w.write_batch(rows)
    e = entries[0]
    back = await r.read_partition(
        lake_root=tmp_path,
        provider=e.provider,
        instrument_id=e.instrument_id,
        date=e.date,
        data_type=e.data_type,
        interval=e.interval,
    )
    recomputed = _expected_checksum(back)
    assert e.checksum == recomputed
    assert e.checksum == _expected_checksum(rows)
    # rows_in_write_order freeze: parquet round-trip MUST preserve write order
    assert [r.model_dump_json() for r in back] == [r.model_dump_json() for r in rows]


@needs_parquet
async def test_multi_row_same_partition_single_file_rowcount(tmp_path: Path) -> None:
    # Explicit: 3 same-date/same-instrument rows → ONE partition file, rowcount == 3
    w = LakeWriter(lake_root=tmp_path)
    r = LakeReader()
    rows = [_tick_md(watermark=400 + i) for i in range(3)]
    entries = await w.write_batch(rows)
    assert len(entries) == 1
    e = entries[0]
    assert e.rowcount == 3
    parquet_files = list((tmp_path / "market-data").rglob("*.parquet"))
    assert len(parquet_files) == 1
    back = await r.read_partition(
        lake_root=tmp_path,
        provider=e.provider,
        instrument_id=e.instrument_id,
        date=e.date,
        data_type=e.data_type,
        interval=e.interval,
    )
    assert len(back) == 3
    assert e.checksum == _expected_checksum(back)


@needs_parquet
async def test_watermark_minmax_and_source_seq_range(tmp_path: Path) -> None:
    w = LakeWriter(lake_root=tmp_path)
    rows = [
        _tick_md(watermark=50, source_sequence=None),
        _tick_md(watermark=55, source_sequence=800),
        _tick_md(watermark=52, source_sequence=810),
    ]
    e = (await w.write_batch(rows))[0]
    assert e.watermark_min == 50
    assert e.watermark_max == 55
    assert e.source_sequence_min == 800
    assert e.source_sequence_max == 810


@needs_parquet
async def test_mixed_snapshot_id_valueerror(tmp_path: Path) -> None:
    w = LakeWriter(lake_root=tmp_path)
    rows = [
        _tick_md(reference_snapshot_id="REF-A"),
        _tick_md(reference_snapshot_id="REF-B"),
    ]
    with pytest.raises(ValueError):
        await w.write_batch(rows)


async def test_order_book_kind_valueerror(tmp_path: Path) -> None:
    # Freeze: ValueError, never silent skip
    w = LakeWriter(lake_root=tmp_path)
    with pytest.raises(ValueError):
        await w.write_batch([_order_book_md()])
    assert not (tmp_path / "market-data").exists()


@needs_parquet
async def test_candle_write_uses_interval_subdir(tmp_path: Path) -> None:
    w = LakeWriter(lake_root=tmp_path)
    entries = await w.write_batch([_candle_md("1m")])
    e = entries[0]
    assert e.data_type == "candles"
    assert e.interval == "1m"
    assert "interval=1m" in e.relative_path
    assert e.relative_path.endswith("candles.parquet")


@needs_parquet
async def test_atomic_tmp_cleaned_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Fail during final replace — no half-written final .parquet; catalog not updated
    w = LakeWriter(lake_root=tmp_path)
    import modules.m2_market_data.storage as storage_mod

    def boom(src: object, dst: object) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(storage_mod.os, "replace", boom)
    with pytest.raises(OSError):
        await w.write_batch([_tick_md()])
    # no final parquet left for that partition (tmp cleaned)
    finals = list((tmp_path / "market-data").rglob("*.parquet")) if (tmp_path / "market-data").exists() else []
    assert finals == []
    cat = tmp_path / "catalog" / "catalog.jsonl"
    assert not cat.exists() or cat.read_text(encoding="utf-8").strip() == ""


async def test_read_missing_partition_file_not_found(tmp_path: Path) -> None:
    r = LakeReader()
    with pytest.raises(FileNotFoundError):
        await r.read_partition(
            lake_root=tmp_path,
            provider="deriv",
            instrument_id="FX.EURUSD",
            date="2026-09-23",
            data_type="ticks",
            interval=None,
        )


@needs_parquet
async def test_roundtrip_marketdata_model_dump_equal(tmp_path: Path) -> None:
    w = LakeWriter(lake_root=tmp_path)
    r = LakeReader()
    rows = [_tick_md(watermark=300, price=1.08430)]
    e = (await w.write_batch(rows))[0]
    back = await r.read_partition(
        lake_root=tmp_path,
        provider=e.provider,
        instrument_id=e.instrument_id,
        date=e.date,
        data_type=e.data_type,
        interval=e.interval,
    )
    assert len(back) == 1
    assert back[0].model_dump_json() == rows[0].model_dump_json()


async def test_mixed_version_valueerror(tmp_path: Path) -> None:
    # Freeze: all rows same MarketData.version string or ValueError
    w = LakeWriter(lake_root=tmp_path)
    a = _tick_md()
    b = _tick_md(watermark=101).model_copy(update={"version": "9.9"})
    with pytest.raises(ValueError):
        await w.write_batch([a, b])


@needs_parquet
async def test_schema_version_equals_uniform_marketdata_version(tmp_path: Path) -> None:
    w = LakeWriter(lake_root=tmp_path)
    md = _tick_md()
    e = (await w.write_batch([md]))[0]
    assert e.schema_version == md.version
    assert e.reference_snapshot_id == "REF-A"
