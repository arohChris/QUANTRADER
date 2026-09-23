"""
M2.8 Storage — global market-data parquet lake + catalog (P14).
WHY: persist canonical MarketData once per provider+instrument+date (no user_id);
catalog row must match parquet (rowcount + deterministic checksum) for replay/M8.

Spec: docs/specs/m2.8_storage.spec.md REV1.1 (A23 freezes: partition_key 5-tuple,
no parquet+catalog 2PC, rows_in_write_order, order_book ValueError, JSONL one entry/key).
Consumes: shared.contracts.market_data only — no private/tenant paths in this slice.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, ConfigDict

from shared.contracts.market_data import Candle, MarketData

_DEFAULT_LAKE_ROOT = Path("data/lake")
_TICK_KINDS = frozenset({"tick", "quote", "trade"})


class CatalogEntry(BaseModel):
    """In-module frozen catalog row — one per partition_key (not shared/contracts yet)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    instrument_id: str
    date: str  # YYYY-MM-DD
    data_type: str  # "ticks" | "candles"
    interval: str | None  # None for ticks; Candle.interval as-is for candles
    relative_path: str  # POSIX path under lake_root
    rowcount: int
    watermark_min: int
    watermark_max: int
    source_sequence_min: int | None
    source_sequence_max: int | None
    reference_snapshot_id: str | None
    checksum: str  # sha256 hex — rows_in_write_order
    schema_version: str  # uniform MarketData.version


def _check_segment(name: str, value: str) -> None:
    # Spec frozen: segment equals . or .. OR contains / \ = OR contains ".." substring
    if not value:
        raise ValueError(f"{name} must be non-empty")
    if value in (".", "..") or "/" in value or "\\" in value or "=" in value or ".." in value:
        raise ValueError(f"invalid path segment {name}={value!r}")


def _provider_of(md: MarketData) -> str:
    provider = md.provider if md.provider else md.broker
    if not provider:
        raise ValueError("provider and broker both empty")
    return provider


def _date_str(exchange: datetime | None) -> str:
    if exchange is None:
        raise ValueError("exchange_timestamp is required for partition date (no fallback clock)")
    return exchange.date().isoformat()


def _data_type_interval(md: MarketData) -> tuple[str, str | None]:
    if md.kind == "order_book":
        # M2.5 deferred — never silent skip
        raise ValueError("kind=order_book not supported in M2.8 Day-1")
    if md.kind == "candle":
        if not isinstance(md.payload, Candle) or not md.payload.interval:
            raise ValueError("candle requires Candle.interval (canonical string, no convert)")
        return "candles", md.payload.interval
    if md.kind in _TICK_KINDS:
        return "ticks", None
    raise ValueError(f"unsupported kind={md.kind!r}")


def _rel_from_parts(
    *,
    lake_root: Path | str,
    provider: str,
    instrument_id: str,
    day: str,
    data_type: str,
    interval: str | None,
) -> Path:
    _check_segment("provider", provider)
    _check_segment("instrument_id", instrument_id)
    rel = (
        f"market-data/provider={provider}"
        f"/instrument={instrument_id}"
        f"/date={day}"
        f"/data_type={data_type}"
    )
    if interval is not None:
        _check_segment("interval", interval)
        rel += f"/interval={interval}"
    rel += f"/{data_type}.parquet"
    return Path(lake_root) / rel


def partition_path(md: MarketData, *, lake_root: Path | str = _DEFAULT_LAKE_ROOT) -> Path:
    """Frozen path formula — under lake_root; never includes user_id."""
    if not md.instrument_id:
        raise ValueError("instrument_id is required")
    provider = _provider_of(md)
    day = _date_str(md.exchange_timestamp)
    data_type, interval = _data_type_interval(md)
    return _rel_from_parts(
        lake_root=lake_root,
        provider=provider,
        instrument_id=md.instrument_id,
        day=day,
        data_type=data_type,
        interval=interval,
    )


def _rel_posix(md: MarketData) -> str:
    # Catalog stores POSIX relative path (no lake_root prefix)
    full = partition_path(md, lake_root=Path("."))
    return full.as_posix()[2:] if full.as_posix().startswith("./") else full.as_posix()


def _partition_key(md: MarketData) -> tuple[str, str, str, str, str | None]:
    if not md.instrument_id:
        raise ValueError("instrument_id is required")
    provider = _provider_of(md)
    day = _date_str(md.exchange_timestamp)
    data_type, interval = _data_type_interval(md)
    return (provider, md.instrument_id, day, data_type, interval)


def _checksum(rows: Sequence[MarketData]) -> str:
    # Spec freeze: sha256("\n".join(md.model_dump_json() for md in rows_in_write_order))
    joined = "\n".join(md.model_dump_json() for md in rows)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _entry_from_rows(
    key: tuple[str, str, str, str, str | None],
    rows: list[MarketData],
    rel: str,
) -> CatalogEntry:
    provider, instrument_id, day, data_type, interval = key

    snapshots = {md.reference_snapshot_id for md in rows if md.reference_snapshot_id is not None}
    if len(snapshots) > 1:
        raise ValueError(f"mixed reference_snapshot_id in partition: {sorted(snapshots)}")
    snapshot = next(iter(snapshots)) if snapshots else None

    versions = {md.version for md in rows}
    if len(versions) != 1:
        raise ValueError(f"mixed MarketData.version in partition: {sorted(versions)}")
    schema_version = next(iter(versions))

    watermarks = list(md.watermark for md in rows)
    # bool is int subclass in Python — reject True/False as watermark
    if any(isinstance(w, bool) or not isinstance(w, int) or w < 1 for w in watermarks):
        raise ValueError(f"watermark must be int >= 1, got {watermarks}")

    seqs = [md.source_sequence for md in rows if md.source_sequence is not None]

    return CatalogEntry(
        provider=provider,
        instrument_id=instrument_id,
        date=day,
        data_type=data_type,
        interval=interval,
        relative_path=rel,
        rowcount=len(rows),
        watermark_min=min(watermarks),
        watermark_max=max(watermarks),
        source_sequence_min=min(seqs) if seqs else None,
        source_sequence_max=max(seqs) if seqs else None,
        reference_snapshot_id=snapshot,
        checksum=_checksum(rows),
        schema_version=schema_version,
    )


class LakeWriter:
    """Async parquet writer — atomic tmp→os.replace per partition; catalog after replace."""

    def __init__(self, *, lake_root: Path | str = _DEFAULT_LAKE_ROOT) -> None:
        self._lake_root = Path(lake_root)

    async def write_batch(self, mds: Sequence[MarketData]) -> list[CatalogEntry]:
        if not mds:
            return []

        # Phase 1: group + validate ALL partitions before any filesystem I/O
        groups: dict[tuple[str, str, str, str, str | None], list[MarketData]] = {}
        for md in mds:
            key = _partition_key(md)
            groups.setdefault(key, []).append(md)

        prepared: list[tuple[list[MarketData], CatalogEntry]] = []
        for key, rows in groups.items():
            rel = _rel_posix(rows[0])
            prepared.append((rows, _entry_from_rows(key, rows, rel)))

        # Phase 2: write (still no cross-partition 2PC — earlier may remain if later fails)
        results: list[CatalogEntry] = []
        for rows, entry in prepared:
            await self._write_partition_atomic(rows, entry)
            results.append(entry)
        return results

    async def _write_partition_atomic(self, rows: list[MarketData], entry: CatalogEntry) -> None:
        final_path = self._lake_root / entry.relative_path
        tmp_path = final_path.with_name(final_path.name + ".tmp")
        final_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await asyncio.to_thread(_write_parquet_sync, rows, tmp_path)
            # Spec freeze: os.replace (not Path.replace) — tests monkeypatch storage.os.replace
            await asyncio.to_thread(os.replace, tmp_path, final_path)
        except BaseException:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        # Catalog AFTER successful parquet replace — no 2PC (spec freeze)
        await asyncio.to_thread(self._upsert_catalog_sync, entry)

    def _upsert_catalog_sync(self, entry: CatalogEntry) -> None:
        catalog_file = self._lake_root / "catalog" / "catalog.jsonl"
        catalog_file.parent.mkdir(parents=True, exist_ok=True)
        key = (entry.provider, entry.instrument_id, entry.date, entry.data_type, entry.interval)
        kept: list[dict] = []
        if catalog_file.is_file():
            for line in catalog_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                row_key = (
                    row["provider"],
                    row["instrument_id"],
                    row["date"],
                    row["data_type"],
                    row["interval"],
                )
                # Spec freeze: exactly one physical line per partition_key (last wins)
                if row_key != key:
                    kept.append(row)
        kept.append(json.loads(entry.model_dump_json()))
        payload = "\n".join(json.dumps(r, sort_keys=True) for r in kept)
        if payload:
            payload += "\n"
        # Catalog rewrite is atomic (tmp→os.replace) — still NO parquet+catalog 2PC
        tmp_catalog = catalog_file.with_name(catalog_file.name + ".tmp")
        try:
            tmp_catalog.write_text(payload, encoding="utf-8")
            os.replace(tmp_catalog, catalog_file)
        except BaseException:
            try:
                tmp_catalog.unlink(missing_ok=True)
            except OSError:
                pass
            raise


def _write_parquet_sync(rows: list[MarketData], path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    # Full JSON per row — round-trip preserves model_dump_json + write order
    payloads = [md.model_dump_json() for md in rows]
    table = pa.table({"json": pa.array(payloads, type=pa.string())})
    pq.write_table(table, path)


def _read_parquet_sync(path: Path) -> list[MarketData]:
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    col = table.column("json").to_pylist()
    return [MarketData.model_validate_json(s) for s in col]


class LakeReader:
    """Explicit named-partition read — missing file → FileNotFoundError (no silent [])."""

    async def read_partition(
        self,
        *,
        lake_root: Path | str,
        provider: str,
        instrument_id: str,
        date: str | date_cls,
        data_type: str,
        interval: str | None = None,
    ) -> list[MarketData]:
        day = date if isinstance(date, str) else date.isoformat()
        path = _rel_from_parts(
            lake_root=lake_root,
            provider=provider,
            instrument_id=instrument_id,
            day=day,
            data_type=data_type,
            interval=interval,
        )
        if not path.is_file():
            raise FileNotFoundError(f"partition not found: {path}")
        return await asyncio.to_thread(_read_parquet_sync, path)
