"""M2.10 WARM — TimescaleDB finalized-1m candle store + D2 FIFO flush.

WHY this module exists:
  The durable leg of the HOT/WARM split (human storage law A42): M2.9 HOT holds the
  trailing 4h ring (cache, never the truth); WARM owns the canonical persisted
  finalized-1m dataset in TimescaleDB and the flush that moves minutes out of the
  ring. Durable path is NEVER fail-open (contrast M2.9): connection loss surfaces
  as typed WarmError/FlushError — reads never return a fake-empty list, writes
  never silently drop a finalized candle.

WHY not the alternatives:
  - No as_of / no history-depth heuristics here (A46 responsibility boundary):
    read_1m is a pure range reader; PIT availability_time <= T and window planning
    belong to the Historical/PIT service ABOVE this layer. window_hours/flush
    batch size are flush-ops params, not a platform depth limit.
  - ON CONFLICT DO NOTHING (never UPDATE): finalized candles are immutable;
    at-least-once re-flush + idempotent upsert = effectively once (spec §3).
  - float8 storage, not Decimal: Candle prices are float64 — pg double precision
    roundtrips the exact bits (market data, not settlement; money-law N/A).

# Consumers: M2.11 pipeline (flush orchestration), Analysis warmup reads (via
# caller-controlled range), verify_m2.10_warm.py (DDL/seed/A42 conversion checks).
"""
from __future__ import annotations

import inspect
import random
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from shared.contracts.market_data import Candle

__all__ = [
    "ALLOWED_QUALITY_STATUS",
    "Candles1mStore",
    "CursorRegressionError",
    "FlushCursor",
    "FlushError",
    "FlushReport",
    "StoreValidationError",
    "WarmConfig",
    "WarmError",
    "apply_ddl",
    "backoff_seconds",
    "build_ddl_statements",
    "decode_candle_row",
    "encode_candle_row",
    "max_contiguous",
    "plan_flush",
    "redact_dsn",
]

# M2.4 emits only these two (candles.py:160) — anything else is junk at the door
ALLOWED_QUALITY_STATUS = frozenset({"COMPLETE", "GAP_AFFECTED"})
_INTERVAL_1M = "1m"
_BAR = timedelta(minutes=1)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class WarmError(Exception):
    """Typed durable-path error — WARM never fails open, never fakes results."""


class FlushError(WarmError):
    """Flush to Timescale failed — batch retained by the caller (ring NOT evicted)."""


class CursorRegressionError(WarmError):
    """Watermark moved backwards — monotonic discipline (A33 mirror)."""


class StoreValidationError(WarmError):
    """Junk row/candle refused — the store never persists garbage (adversarial)."""


# ---- config (§3.13: refuse absurd values at startup) ------------------------------

@dataclass(frozen=True)
class WarmConfig:
    """Sane-bound flush/read config. `dsn` carries secrets -> excluded from repr."""

    dsn: str = field(repr=False)
    pool_min: int = 1
    pool_max: int = 10
    connect_timeout_s: float = 5.0
    statement_timeout_s: float = 30.0
    flush_batch_size: int = 240  # D2: one 4h window of 1m bars per batch
    window_hours: int = 4        # D2 ring horizon (ops param, NOT platform depth)
    backlog_bound: int = 1000    # beyond bound = CRITICAL (spec §3)

    def __post_init__(self) -> None:
        _bound("pool_min", self.pool_min, 1, 32)
        if not self.pool_min <= self.pool_max <= 64:
            raise ValueError(f"pool_max must be in [{self.pool_min}, 64], got {self.pool_max}")
        _bound("connect_timeout_s", self.connect_timeout_s, 0, 60, exclusive_low=True)
        _bound("statement_timeout_s", self.statement_timeout_s, 0, 300, exclusive_low=True)
        _bound("flush_batch_size", self.flush_batch_size, 1, 240)
        _bound("window_hours", self.window_hours, 1, 24)
        _bound("backlog_bound", self.backlog_bound, 1, 100_000)


def _bound(name: str, value: float, lo: float, hi: float, *, exclusive_low: bool = False) -> None:
    low_ok = value > lo if exclusive_low else value >= lo
    if not (low_ok and value <= hi):
        op = "(" if exclusive_low else "["
        raise ValueError(f"{name} must be in {op}{lo}, {hi}], got {value}")


_DSN_PASSWORD = re.compile(r"(://[^:/@\s]+):[^@\s]*@")


def redact_dsn(text: str) -> str:
    """WHY: secrets never reach logs/exceptions/reprs (§3.7) — mask userinfo only,
    keep host:port so connection failures stay debuggable."""
    return _DSN_PASSWORD.sub(r"\1:***@", text)


# ---- DDL (idempotent; applied only by verify/setup, never on import) --------------

_DDL: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS candles_1m (
        provider text NOT NULL,
        instrument_id text NOT NULL,
        interval_start timestamptz NOT NULL,
        interval_end timestamptz NOT NULL,
        open double precision NOT NULL,
        high double precision NOT NULL,
        low double precision NOT NULL,
        close double precision NOT NULL,
        tick_count bigint NOT NULL,
        average_spread double precision,
        min_spread double precision,
        max_spread double precision,
        expected_duration_ms bigint NOT NULL,
        quality_status text NOT NULL,
        aggregation_version text NOT NULL,
        reference_snapshot_id text,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (provider, instrument_id, interval_start)
    )""",
    (
        "SELECT create_hypertable('candles_1m', 'interval_start', if_not_exists => TRUE, "
        "chunk_time_interval => INTERVAL '1 day')"
    ),
    (
        "CREATE INDEX IF NOT EXISTS candles_1m_pf_start_idx "
        "ON candles_1m (provider, instrument_id, interval_start DESC)"
    ),
    # A42: compression ON — segmentby identity, orderby time, policy idempotent
    (
        "ALTER TABLE candles_1m SET (timescaledb.compress, "
        "timescaledb.compress_segmentby = 'provider, instrument_id', "
        "timescaledb.compress_orderby = 'interval_start DESC')"
    ),
    "SELECT add_compression_policy('candles_1m', INTERVAL '7 days', if_not_exists => TRUE)",
    """CREATE TABLE IF NOT EXISTS flush_cursors (
        provider text NOT NULL,
        instrument_id text NOT NULL,
        watermark timestamptz NOT NULL,
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (provider, instrument_id)
    )""",
    """CREATE TABLE IF NOT EXISTS ticks_live (
        provider text NOT NULL,
        instrument_id text NOT NULL,
        epoch timestamptz NOT NULL,
        price double precision,
        bid double precision,
        ask double precision,
        reference_snapshot_id text,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (provider, instrument_id, epoch)
    )""",
)


def build_ddl_statements() -> tuple[str, ...]:
    """WHY a getter, not module side effects: DDL runs only from explicit callers
    (verify/setup) — importing warm.py never touches the database."""
    return _DDL


async def apply_ddl(conn: Any) -> int:
    """Apply idempotent DDL; returns statement count. Safe to run twice (IF NOT EXISTS)."""
    stmts = build_ddl_statements()
    for sql in stmts:
        await conn.execute(sql)
    return len(stmts)


# ---- encode/decode (validation = store never persists garbage) --------------------

_REQUIRED_COLUMNS = (
    "interval_start", "interval_end", "open", "high", "low", "close",
    "tick_count", "expected_duration_ms", "quality_status", "aggregation_version",
)


def encode_candle_row(
    provider: str,
    instrument_id: str,
    candle: Candle,
    reference_snapshot_id: str | None,
) -> dict[str, object]:
    """Candle -> candles_1m row dict (created_at is DB-default, never caller-set)."""
    _validate_identity(provider, instrument_id)
    _validate_candle(candle)
    return {
        "provider": provider,
        "instrument_id": instrument_id,
        "interval_start": candle.interval_start,
        "interval_end": candle.interval_end,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "tick_count": candle.tick_count,
        "average_spread": candle.average_spread,
        "min_spread": candle.min_spread,
        "max_spread": candle.max_spread,
        "expected_duration_ms": candle.expected_duration_ms,
        "quality_status": candle.quality_status,
        "aggregation_version": candle.aggregation_version,
        "reference_snapshot_id": reference_snapshot_id,
    }


def decode_candle_row(row: Mapping[str, object]) -> Candle:
    """Row -> Candle. Interval is implicit (table is 1m-only, D1); junk -> typed rejection."""
    try:
        missing = [k for k in _REQUIRED_COLUMNS if k not in row]
        if missing:
            raise StoreValidationError(f"row missing columns: {missing}")
        return Candle(
            open=float(row["open"]),  # type: ignore[arg-type]
            high=float(row["high"]),  # type: ignore[arg-type]
            low=float(row["low"]),  # type: ignore[arg-type]
            close=float(row["close"]),  # type: ignore[arg-type]
            tick_count=int(row["tick_count"]),  # type: ignore[arg-type]
            average_spread=row.get("average_spread"),  # type: ignore[arg-type]
            min_spread=row.get("min_spread"),  # type: ignore[arg-type]
            max_spread=row.get("max_spread"),  # type: ignore[arg-type]
            interval=_INTERVAL_1M,
            interval_start=row["interval_start"],  # type: ignore[arg-type]
            interval_end=row["interval_end"],  # type: ignore[arg-type]
            quality_status=str(row["quality_status"]),
            aggregation_version=str(row["aggregation_version"]),
            expected_duration_ms=int(row["expected_duration_ms"]),  # type: ignore[arg-type]
        )
    except StoreValidationError:
        raise
    except Exception as exc:
        raise StoreValidationError(f"junk candle row: {exc}") from exc


def _validate_identity(provider: str, instrument_id: str) -> None:
    if not provider or not instrument_id:
        raise StoreValidationError("provider and instrument_id must be non-empty")
    if any(ch.isspace() for ch in provider + instrument_id):
        raise StoreValidationError("provider/instrument_id must not contain whitespace")


def _validate_candle(candle: Candle) -> None:
    if candle.tick_count < 0:
        raise StoreValidationError(f"tick_count must be >= 0, got {candle.tick_count}")
    if candle.interval_start > candle.interval_end:
        raise StoreValidationError("interval_start must be <= interval_end")
    if candle.quality_status not in ALLOWED_QUALITY_STATUS:
        raise StoreValidationError(
            f"quality_status {candle.quality_status!r} not in {sorted(ALLOWED_QUALITY_STATUS)}"
        )
    if candle.interval != _INTERVAL_1M:
        raise StoreValidationError(f"interval must be '1m' (D1), got {candle.interval!r}")


# ---- FIFO plan + cursor (pure logic, spec §4) -------------------------------------

def plan_flush(candles: Sequence[Candle], *, batch_size: int) -> list[Candle]:
    """WHY sort here: ring may hand minutes in any order; flush must go ascending
    (FIFO) so a crash mid-flush leaves a contiguous durable prefix. Batch-capped
    so one flush can never exceed the configured D2 window."""
    if batch_size < 1:
        raise ValueError(f"flush_batch_size must be >= 1, got {batch_size}")
    return sorted(candles, key=lambda c: c.interval_start)[:batch_size]


@dataclass(frozen=True)
class FlushCursor:
    """Watermark per (provider, instrument) — persisted in Timescale, monotonic."""

    provider: str
    instrument_id: str
    watermark: datetime | None = None

    def advance(self, new: datetime) -> FlushCursor:
        if self.watermark is not None and new < self.watermark:
            raise CursorRegressionError(
                f"cursor regression rejected: {new.isoformat()} < {self.watermark.isoformat()}"
            )
        return FlushCursor(self.provider, self.instrument_id, new)


@dataclass(frozen=True)
class FlushReport:
    """Ack result: ring evicts `evicted` ONLY when this report exists (flush-before-evict)."""

    acked: bool
    inserted: int
    cursor: FlushCursor
    evicted: tuple[Candle, ...]


def max_contiguous(
    watermark: datetime | None,
    starts: Iterable[datetime],
) -> datetime | None:
    """Largest watermark reachable by a gap-free 1m chain from the current cursor
    (or from the earliest stored bar when watermark is None). WHY: cursor may never
    leap over a missing minute — out-of-order batches are accepted (upsert by key)
    but progress is proven, not assumed (spec §4 / A33)."""
    pool = set(starts)
    if watermark is None:
        if not pool:
            return None
        cur = min(pool)
    else:
        cur = watermark
    while (nxt := cur + _BAR) in pool:
        cur = nxt
    return cur


def backoff_seconds(
    attempt: int,
    *,
    base: float = 0.1,
    cap: float = 8.0,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff with jitter, hard-capped — no retry storms (§3.8)."""
    if attempt < 0:
        raise ValueError(f"attempt must be >= 0, got {attempt}")
    if not 0 < base <= cap:
        raise ValueError(f"base must be in (0, {cap}], got {base}")
    r: Any = rng if rng is not None else random
    exp = min(cap, base * (2**attempt))
    jittered = exp * (1 + r.uniform(-0.1, 0.1))
    return max(0.0, min(cap, jittered))


# ---- store (async I/O; durable path never fail-open) ------------------------------

class Candles1mStore:
    """Async finalized-1m store. Connection injected or built lazily via asyncpg
    (hermetic tests never import asyncpg — m2.9 lazy-import pattern)."""

    def __init__(
        self,
        config: WarmConfig,
        *,
        pool: Any = None,
        pool_factory: Any = None,
    ) -> None:
        self._cfg = config
        self._pool = pool
        self._factory = pool_factory

    async def connect(self) -> None:
        if self._pool is not None:
            return
        try:
            if self._factory is not None:
                factory = self._factory
            else:
                import asyncpg  # lazy import: tests inject fakes, never need the driver

                factory = asyncpg.create_pool
            self._pool = await factory(
                self._cfg.dsn,
                min_size=self._cfg.pool_min,
                max_size=self._cfg.pool_max,
                timeout=self._cfg.connect_timeout_s,
            )
        except Exception as exc:
            raise WarmError(f"timescale connect failed: {redact_dsn(str(exc))}") from exc

    async def close(self) -> None:
        pool = self._pool
        if pool is None:
            return
        self._pool = None
        try:
            if hasattr(pool, "terminate"):
                res = pool.terminate()
            elif hasattr(pool, "aclose"):
                res = pool.aclose()
            else:
                res = None
            if inspect.isawaitable(res):
                await res
        except Exception:  # noqa: BLE001 - close is best-effort and idempotent
            return
        return

    def _require_pool(self) -> Any:
        if self._pool is None:
            # match="connect" in tests: a missing pool must READ as an error, never []
            raise WarmError("not connected: call connect() first (durable path never fail-open)")
        return self._pool

    async def read_1m(
        self,
        provider: str,
        instrument_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[Candle]:
        """Range read, ascending. A46: window args verbatim, never auto-expanded;
        rows outside [window_start, window_end] are filtered even if returned."""
        pool = self._require_pool()
        sql = (
            "SELECT provider, instrument_id, interval_start, interval_end, open, high, low, close, "
            "tick_count, average_spread, min_spread, max_spread, expected_duration_ms, "
            "quality_status, aggregation_version, reference_snapshot_id "
            "FROM candles_1m "
            "WHERE provider = $1 AND instrument_id = $2 "
            "AND interval_start >= $3 AND interval_start <= $4 "
            "ORDER BY interval_start ASC"
        )
        try:
            rows = await pool.fetch(sql, provider, instrument_id, window_start, window_end)
        except Exception as exc:
            raise WarmError(f"read_1m failed: {redact_dsn(str(exc))}") from exc
        out: list[Candle] = []
        for raw in rows:
            candle = decode_candle_row(raw)
            if window_start <= candle.interval_start <= window_end:
                out.append(candle)
        return out

    async def flush_batch(
        self,
        provider: str,
        instrument_id: str,
        candles: Sequence[Candle],
        reference_snapshot_id: str | None = None,
    ) -> FlushReport:
        """D2 flush: load cursor -> plan FIFO -> upsert (ack) -> recompute max
        contiguous -> save cursor. Crash between ack and save only re-flushes
        (ON CONFLICT DO NOTHING -> effectively once). On any I/O failure the typed
        FlushError propagates BEFORE the cursor moves: caller retains the batch and
        the ring evicts nothing (flush-before-evict, spec §3)."""
        pool = self._require_pool()
        cursor = await self._load_cursor(pool, provider, instrument_id)
        plan = plan_flush(candles, batch_size=self._cfg.flush_batch_size)
        if not plan:
            return FlushReport(acked=True, inserted=0, cursor=cursor, evicted=())
        # encode ALL rows first: junk anywhere -> StoreValidationError before any write
        rows = [
            encode_candle_row(provider, instrument_id, c, reference_snapshot_id) for c in plan
        ]
        inserted = await self._upsert(pool, rows)
        starts = await self._stored_starts(pool, provider, instrument_id, cursor.watermark)
        new_wm = max_contiguous(cursor.watermark, starts)
        if new_wm is not None and new_wm != cursor.watermark:
            cursor = cursor.advance(new_wm)
            await self._save_cursor(pool, cursor)
        return FlushReport(acked=True, inserted=inserted, cursor=cursor, evicted=tuple(plan))

    async def _load_cursor(self, pool: Any, provider: str, instrument_id: str) -> FlushCursor:
        sql = "SELECT watermark FROM flush_cursors WHERE provider = $1 AND instrument_id = $2"
        try:
            wm = await pool.fetchval(sql, provider, instrument_id)
        except Exception as exc:
            raise FlushError(f"cursor load failed: {redact_dsn(str(exc))}") from exc
        return FlushCursor(provider, instrument_id, wm)

    async def _upsert(self, pool: Any, rows: list[dict[str, object]]) -> int:
        cols = list(rows[0].keys())
        n = len(cols)
        col_sql = ", ".join(cols)
        values_sql = ", ".join(
            "(" + ", ".join(f"${i * n + j + 1}" for j in range(n)) + ")"
            for i in range(len(rows))
        )
        # finalized = immutable: ON CONFLICT DO NOTHING, never UPDATE (spec §1)
        sql = f"INSERT INTO candles_1m ({col_sql}) VALUES {values_sql} ON CONFLICT DO NOTHING"
        flat = tuple(v for r in rows for v in r.values())
        try:
            status = await pool.execute(sql, *flat)
        except Exception as exc:
            raise FlushError(f"candle upsert failed: {redact_dsn(str(exc))}") from exc
        parts = str(status).split()
        try:
            return int(parts[-1])
        except (IndexError, ValueError) as exc:
            raise FlushError(f"unexpected insert status from store: {status!r}") from exc

    async def _stored_starts(
        self,
        pool: Any,
        provider: str,
        instrument_id: str,
        watermark: datetime | None,
    ) -> list[datetime]:
        sql = (
            "SELECT interval_start FROM candles_1m "
            "WHERE provider = $1 AND instrument_id = $2 AND interval_start > $3 "
            "ORDER BY interval_start ASC"
        )
        bound = watermark if watermark is not None else _EPOCH
        try:
            rows = await pool.fetch(sql, provider, instrument_id, bound)
        except Exception as exc:
            raise FlushError(f"contiguity scan failed: {redact_dsn(str(exc))}") from exc
        out: list[datetime] = []
        for r in rows:
            out.append(r if isinstance(r, datetime) else r["interval_start"])  # type: ignore[arg-type]
        return out

    async def _save_cursor(self, pool: Any, cursor: FlushCursor) -> None:
        sql = (
            "INSERT INTO flush_cursors (provider, instrument_id, watermark) VALUES ($1, $2, $3) "
            "ON CONFLICT (provider, instrument_id) DO UPDATE "
            "SET watermark = EXCLUDED.watermark, updated_at = now() "
            "WHERE flush_cursors.watermark < EXCLUDED.watermark"  # DB-level monotonic guard
        )
        try:
            await pool.execute(sql, cursor.provider, cursor.instrument_id, cursor.watermark)
        except Exception as exc:
            raise FlushError(f"cursor save failed: {redact_dsn(str(exc))}") from exc
