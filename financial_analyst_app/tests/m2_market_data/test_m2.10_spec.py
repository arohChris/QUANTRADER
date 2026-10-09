"""
M2.10 WARM — TimescaleDB finalized-1m-candle store + FIFO flush — Spec tests (WRITTEN BEFORE CODE).

WHY: durable-path law differs from M2.9 HOT — WARM is NEVER fail-open: connection loss
     surfaces as typed WarmError/FlushError (never fake-empty reads, never silent drops),
     flush-before-evict (ack before cursor save before ring evict), cursor monotonic
     (A33 discipline), ON CONFLICT DO NOTHING idempotency, float64-exact price roundtrip,
     DDL never destroys, and A42 compression/conversion clauses — all locked by tests
     BEFORE modules/m2_market_data/warm.py exists (AGENTS §2 Step 3,
     spec docs/specs/m2.10_warm.spec.md LGTM'd A45).

RED until Step 5: ModuleNotFoundError: modules.m2_market_data.warm — honest collection
RED, never skipped (same pattern as M2.9/M3.0).
Hermetic: no `asyncpg` import anywhere (lazy in impl connect()); connection injected —
test_module_is_free_of_module_level_asyncpg proves it.
Money hand-calc N/A per spec §1: float64 market data (not settlement) — Decimal law N/A;
prices asserted via float.hex bitwise equality instead.
"""
from __future__ import annotations

import random
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from modules.m2_market_data import warm  # RED until Step 5 (impl)
from shared.contracts.market_data import Candle

_DSN = "postgresql://quantrader:SEKRET-99@127.0.0.1:5432/quantrader"
_T0 = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)


# ---- fixtures (contract constructors only — no impl) -----------------------------

def _bar(minute: int, *, close: float = 1.08510) -> Candle:
    start = _T0 + timedelta(minutes=minute)
    return Candle(
        open=1.08500,
        high=1.08550,
        low=1.08450,
        close=close,
        interval="1m",
        interval_start=start,
        interval_end=start + timedelta(minutes=1),
    )


def _row(c: Candle) -> dict[str, object]:
    return {
        "provider": "deriv",
        "instrument_id": "CRYPTO.BTCUSD",
        "interval_start": c.interval_start,
        "interval_end": c.interval_end,
        "open": c.open,
        "high": c.high,
        "low": c.low,
        "close": c.close,
        "average_spread": c.average_spread,
        "min_spread": c.min_spread,
        "max_spread": c.max_spread,
        "expected_duration_ms": c.expected_duration_ms,
        "quality_status": c.quality_status,
        "aggregation_version": c.aggregation_version,
        "reference_snapshot_id": "REF-2026-09-25-001",
        "created_at": "2026-01-02T11:00:00+00:00",
    }


class FakeConn:
    """In-memory stand-in for asyncpg Connection — ONLY the subset warm.py may use.

    Responses are programmed per call order (fetchval/fetch/execute queues);
    `fail` names raise OSError("simulated outage") — mirrors m2.9 FakeRedis style.
    """

    def __init__(
        self,
        *,
        fetchval_results: list[Any] | None = None,
        fetch_results: list[list[Any]] | None = None,
        execute_status: list[str] | None = None,
        fail: set[str] | None = None,
    ) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.fail: set[str] = set(fail or ())
        self._fetchval: list[Any] = list(fetchval_results or [])
        self._fetch: list[list[Any]] = list(fetch_results or [])
        self._execute: list[str] = list(execute_status or [])

    async def execute(self, sql: str, *args: object) -> str:
        self.calls.append((sql, args))
        if "execute" in self.fail:
            raise OSError("simulated outage")
        return self._execute.pop(0) if self._execute else "INSERT 0 1"

    async def fetchval(self, sql: str, *args: object, **kw: object) -> Any:
        self.calls.append((sql, args))
        if "fetchval" in self.fail:
            raise OSError("simulated outage")
        return self._fetchval.pop(0) if self._fetchval else None

    async def fetch(self, sql: str, *args: object, **kw: object) -> list[Any]:
        self.calls.append((sql, args))
        if "fetch" in self.fail:
            raise OSError("simulated outage")
        return self._fetch.pop(0) if self._fetch else []

    async def terminate(self) -> None:
        self.calls.append(("TERMINATE", ()))


def _cfg(**over: object) -> warm.WarmConfig:
    over.setdefault("dsn", _DSN)
    return warm.WarmConfig(**over)  # type: ignore[arg-type]


def _store(
    conn: FakeConn | None = None,
    **cfg_over: object,
) -> tuple[Any, FakeConn]:
    conn = conn if conn is not None else FakeConn()
    store = warm.Candles1mStore(_cfg(**cfg_over), pool=conn)  # type: ignore[arg-type]
    return store, conn


def _executed(conn: FakeConn, needle: str) -> list[str]:
    return [sql for sql, _ in conn.calls if needle in sql]


# ---- config bounds (§3.13) + redaction (§3.7) ------------------------------------

def test_config_defaults_construct_valid() -> None:
    cfg = warm.WarmConfig(dsn=_DSN)
    assert cfg.flush_batch_size == 240  # D2: 4h window of 1m bars
    assert cfg.window_hours == 4        # D2: ring = 4h
    assert cfg.pool_min == 1 and cfg.pool_max >= cfg.pool_min


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"pool_min": 0}, "pool_min"),
        ({"pool_min": 1, "pool_max": 0}, "pool_max"),
        ({"pool_min": 1, "pool_max": 65}, "pool_max"),
        ({"connect_timeout_s": 0.0}, "connect_timeout"),
        ({"connect_timeout_s": 61.0}, "connect_timeout"),
        ({"statement_timeout_s": 0.0}, "statement_timeout"),
        ({"statement_timeout_s": 301.0}, "statement_timeout"),
        ({"flush_batch_size": 0}, "flush_batch_size"),
        ({"flush_batch_size": 241}, "flush_batch_size"),
        ({"window_hours": 0}, "window_hours"),
        ({"window_hours": 25}, "window_hours"),
        ({"backlog_bound": 0}, "backlog_bound"),
    ],
)
def test_config_absurd_value_refuses_startup(overrides: dict[str, object], needle: str) -> None:
    # §3.13: refuse absurd config at startup, never run with it; message names the field
    with pytest.raises(ValueError, match=needle):
        warm.WarmConfig(dsn=_DSN, **overrides)  # type: ignore[arg-type]


def test_config_repr_str_never_contains_password() -> None:
    cfg = _cfg()
    assert "SEKRET-99" not in repr(cfg)
    assert "SEKRET-99" not in str(cfg)
    assert "127.0.0.1" not in repr(cfg)  # dsn wholly excluded (field repr=False)
    assert repr(cfg).startswith("WarmConfig(")


def test_redact_dsn_masks_password_keeps_host() -> None:
    red = warm.redact_dsn(_DSN)
    assert "SEKRET-99" not in red
    assert "quantrader:***@" in red
    assert "127.0.0.1:5432" in red  # host stays debuggable
    assert warm.redact_dsn("postgresql://h:5432/db") == "postgresql://h:5432/db"


# ---- DDL: idempotent, A42 compression/conversion, never destroys ------------------

def _ddl() -> str:
    return "\n".join(warm.build_ddl_statements())


def test_ddl_candles_1m_create_idempotent_with_full_columns() -> None:
    ddl = _ddl()
    create = next(s for s in warm.build_ddl_statements() if "candles_1m" in s and "CREATE TABLE" in s)
    assert "CREATE TABLE IF NOT EXISTS candles_1m" in create
    # §1 column list, verbatim
    for col in (
        "provider", "instrument_id", "interval_start", "interval_end", "open", "high",
        "low", "close", "average_spread", "min_spread", "max_spread",
        "expected_duration_ms", "quality_status", "aggregation_version",
        "reference_snapshot_id", "created_at",
    ):
        assert re.search(rf"\b{col}\b", create), f"missing column {col}"
    assert "double precision" in create  # float8 exact roundtrip (spec §1 prices)
    # Candle 2.0.0 (human §5 2026-09-29): a FRESH database must not be born with the column.
    assert not re.search(r"\btick_count\b", create), (
        "tick_count must be absent from the CREATE TABLE - the venue never sent it"
    )
    # ...and an EXISTING database must be demoted, never destroyed. DROP COLUMN is
    # irreversible (reverting the commit cannot bring the values back, AGENTS.md 12 Q6),
    # so the migration only relaxes NOT NULL - and only if the column is there at all.
    migration = [s for s in warm.build_ddl_statements() if "DROP NOT NULL" in s]
    assert len(migration) == 1, "exactly one reversible demotion statement"
    assert "DROP COLUMN" not in migration[0]
    assert "information_schema.columns" in migration[0], (
        "must be conditional: a fresh DB never had the column and a bare ALTER aborts startup"
    )
    assert "CREATE TABLE IF NOT EXISTS candles_1m" in ddl


def test_ddl_unique_key_on_provider_instrument_interval_start() -> None:
    assert re.search(
        r"UNIQUE\s*\(\s*provider,\s*instrument_id,\s*interval_start\s*\)", _ddl()
    ), "writes are ON CONFLICT DO NOTHING on this key (spec §1)"


def test_ddl_hypertable_one_day_chunks_if_not_exists() -> None:
    ddl = _ddl()
    assert re.search(r"create_hypertable\(\s*'candles_1m',\s*'interval_start'", ddl, re.IGNORECASE)
    assert re.search(r"if_not_exists\s*=>\s*TRUE", ddl, re.IGNORECASE)
    assert re.search(r"chunk_time_interval\s*=>\s*INTERVAL\s*'1 day'", ddl, re.IGNORECASE)


def test_ddl_index_supports_range_scan_desc() -> None:
    ddl = _ddl()
    assert re.search(r"CREATE INDEX IF NOT EXISTS", ddl)
    assert re.search(
        r"\(\s*provider,\s*instrument_id,\s*interval_start\s+DESC\s*\)", ddl
    ), "read_1m range scan law (spec §1)"


def test_ddl_compression_policy_present_a42() -> None:
    # A42: compression ON — segmentby identity, orderby time DESC, 7d policy idempotent
    ddl = _ddl()
    assert re.search(r"timescaledb\.compress\b", ddl)
    assert re.search(r"compress_segmentby\s*=\s*'provider,\s*instrument_id'", ddl)
    assert re.search(r"compress_orderby\s*=\s*'interval_start DESC'", ddl)
    assert re.search(
        r"add_compression_policy\(\s*'candles_1m',\s*INTERVAL\s*'7 days',\s*if_not_exists\s*=>\s*TRUE",
        ddl,
        re.IGNORECASE,
    )


def test_ddl_ticks_live_conversion_table_a42() -> None:
    # A42 (c): one-time lake→Timescale conversion target, idempotent identity key
    ddl = _ddl()
    assert "CREATE TABLE IF NOT EXISTS ticks_live" in ddl
    assert re.search(r"UNIQUE\s*\(\s*provider,\s*instrument_id,\s*epoch\s*\)", ddl)


def test_ddl_flush_cursors_table_persists_watermark() -> None:
    ddl = _ddl()
    assert "CREATE TABLE IF NOT EXISTS flush_cursors" in ddl
    assert re.search(r"\bwatermark\b", ddl)


def test_ddl_never_destroys_data() -> None:
    """Adversarial: the DDL path must be structurally incapable of data loss.

    REFINED 2026-09-29, not relaxed. The original test banned the word `DROP` outright,
    which the Candle 2.0.0 migration tripped: PostgreSQL has no "make nullable" spelling
    other than `ALTER COLUMN ... DROP NOT NULL`. Rather than delete a data-safety guard to
    let a migration through, the guard now names the DESTRUCTIVE forms explicitly and then
    requires the single permitted `DROP` to be exactly a NOT NULL relaxation. That is
    STRICTER than a blanket ban, because it no longer passes if anyone adds an unrelated
    `DROP <something>` later - it fails unless every DROP in the DDL is the safe one.
    """
    ddl = _ddl()
    for destructive in (r"\bDROP\s+TABLE\b", r"\bDROP\s+COLUMN\b", r"\bTRUNCATE\b",
                        r"\bDELETE\b", r"\bUPDATE\b"):
        assert not re.search(destructive, ddl, re.IGNORECASE), (
            f"destructive DDL present: {destructive}  # finalized rows are immutable"
        )
    every_drop = re.findall(r"\bDROP\b[^;]*", ddl, re.IGNORECASE)
    assert len(every_drop) == 1, f"exactly one DROP allowed, found {len(every_drop)}: {every_drop}"
    assert re.fullmatch(r"DROP\s+NOT\s+NULL", every_drop[0].strip(), re.IGNORECASE), (
        f"the one permitted DROP must be a NOT NULL relaxation, got: {every_drop[0]!r}"
    )


async def test_apply_ddl_twice_every_statement_guarded() -> None:
    # Spec edge: DDL applied twice → no error, no duplicate objects
    conn = FakeConn()
    n1 = await warm.apply_ddl(conn)
    n2 = await warm.apply_ddl(conn)
    stmts = warm.build_ddl_statements()
    assert n1 == n2 == len(stmts)
    assert len(conn.calls) == 2 * n1
    assert [sql for sql, _ in conn.calls[:n1]] == list(stmts)
    for sql in stmts:
        if re.search(r"CREATE TABLE|CREATE INDEX", sql, re.IGNORECASE):
            assert "IF NOT EXISTS" in sql, f"unguarded: {sql[:60]}"
        if re.search(r"create_hypertable|add_compression_policy", sql, re.IGNORECASE):
            assert re.search(r"if_not_exists\s*=>\s*TRUE", sql, re.IGNORECASE), f"unguarded: {sql[:60]}"


# ---- validation: store never persists garbage (adversarial) ----------------------

@pytest.mark.parametrize("bad_count", [-1, -100])
def test_contract_now_refuses_tick_count_entirely(bad_count: int) -> None:
    """Candle 2.0.0 removed the field, so the adversarial question changed shape.

    It used to ask "is a negative tick_count rejected?" Now there is no tick_count to be
    negative: `extra="forbid"` refuses the key outright, whatever its value. Asserting the
    refusal is stronger than the old bound check, because a writer that still tries to pass
    the field now FAILS LOUDLY instead of quietly storing a fabricated number.
    """
    # WHY `Candle(**...)` and not `model_copy(update=...)`: pydantic v2's model_copy SKIPS
    # validation, so model_copy would happily attach a field extra="forbid" is meant to
    # reject, and this test would pass without anything being refused.
    payload = _bar(0).model_dump()
    payload["tick_count"] = bad_count
    with pytest.raises(ValidationError) as excinfo:
        Candle(**payload)  # type: ignore[arg-type]
    assert excinfo.value.errors()[0]["type"] == "extra_forbidden"
    assert "tick_count" not in Candle.model_fields
    assert Candle.model_fields["version"].default == "2.0.0", (
        "2.0.0 also ADDED the version field the contract never had (AGENTS.md 5)"
    )


def test_encode_rejects_interval_start_after_interval_end() -> None:
    bad = _bar(0).model_copy(
        update={
            "interval_start": _T0 + timedelta(minutes=5),
            "interval_end": _T0,
        }
    )
    with pytest.raises(warm.StoreValidationError):
        warm.encode_candle_row("deriv", "CRYPTO.BTCUSD", bad, "REF-1")


@pytest.mark.parametrize("bad_quality", ["BOGUS", "", "complete", "GAP"])
def test_encode_rejects_unknown_quality_status(bad_quality: str) -> None:
    bad = _bar(0).model_copy(update={"quality_status": bad_quality})
    with pytest.raises(warm.StoreValidationError):
        warm.encode_candle_row("deriv", "CRYPTO.BTCUSD", bad, "REF-1")


@pytest.mark.parametrize("bad_interval", ["5m", "1h", "1D", ""])
def test_encode_rejects_non_1m_interval(bad_interval: str) -> None:
    # Adversarial not in spec edges: WARM stores 1m ONLY (D1 binding) — refuse anything else
    bad = _bar(0).model_copy(update={"interval": bad_interval})
    with pytest.raises(warm.StoreValidationError):
        warm.encode_candle_row("deriv", "CRYPTO.BTCUSD", bad, "REF-1")


@pytest.mark.parametrize(
    ("provider", "instrument_id"),
    [("", "CRYPTO.BTCUSD"), ("deriv", ""), ("deriv", "a b")],
)
def test_encode_rejects_empty_or_injected_identity(provider: str, instrument_id: str) -> None:
    # Adversarial: identity parts feed SQL params — never accept empty/whitespace
    with pytest.raises(warm.StoreValidationError):
        warm.encode_candle_row(provider, instrument_id, _bar(0), "REF-1")


@pytest.mark.parametrize("quality", ["COMPLETE", "GAP_AFFECTED"])
def test_encode_accepts_both_m24_quality_statuses(quality: str) -> None:
    c = _bar(0).model_copy(update={"quality_status": quality})
    row = warm.encode_candle_row("deriv", "CRYPTO.BTCUSD", c, "REF-1")
    assert row["quality_status"] == quality
    assert warm.decode_candle_row(row) == c


# ---- encode/decode: column golden + float64 bitwise ------------------------------

def test_encode_row_column_keys_golden() -> None:
    row = warm.encode_candle_row("deriv", "CRYPTO.BTCUSD", _bar(0), "REF-2026-09-25-001")
    assert set(row.keys()) == {
        "provider", "instrument_id", "interval_start", "interval_end",
        "open", "high", "low", "close", "average_spread",
        "min_spread", "max_spread", "expected_duration_ms", "quality_status",
        "aggregation_version", "reference_snapshot_id",
    }  # created_at is DB-default (spec §1); observed_duration_ms not persisted (not a column)
    assert row["provider"] == "deriv" and row["instrument_id"] == "CRYPTO.BTCUSD"


def test_float64_price_roundtrip_is_bitwise_exact() -> None:
    # Spec edge: float8 roundtrips exactly — 1.08538 must come back as the SAME bits
    c = _bar(0, close=1.08538).model_copy(update={"open": 1.08538, "high": 1.08538, "low": 1.08538})
    out = warm.decode_candle_row(warm.encode_candle_row("deriv", "CRYPTO.BTCUSD", c, "REF-1"))
    for name in ("open", "high", "low", "close"):
        assert float.hex(getattr(out, name)) == float.hex(getattr(c, name))
    assert out == c


def test_reference_snapshot_id_carried_through_encode() -> None:
    row = warm.encode_candle_row("deriv", "CRYPTO.BTCUSD", _bar(0), "REF-2026-09-25-001")
    assert row["reference_snapshot_id"] == "REF-2026-09-25-001"
    # decode ignores lineage columns — read contract is list[Candle] (spec §5)
    assert warm.decode_candle_row(row) == _bar(0)


def test_decode_missing_column_rejected() -> None:
    # Store never corrupts: truncated row → typed rejection, not KeyError/None-candle
    row = _row(_bar(0))
    del row["close"]
    with pytest.raises(warm.StoreValidationError):
        warm.decode_candle_row(row)


def test_decode_junk_value_rejected() -> None:
    row = _row(_bar(0))
    row["close"] = "not-a-float"
    with pytest.raises(warm.StoreValidationError):
        warm.decode_candle_row(row)


# ---- FIFO plan + cursor (pure logic) ---------------------------------------------

def test_plan_flush_sorts_ascending_fifo() -> None:
    shuffled = [_bar(2), _bar(0), _bar(1)]
    plan = warm.plan_flush(shuffled, batch_size=240)
    assert [c.interval_start for c in plan] == [
        _bar(0).interval_start, _bar(1).interval_start, _bar(2).interval_start,
    ]


def test_plan_flush_respects_batch_size_cap() -> None:
    bars = [_bar(m) for m in range(10)]
    plan = warm.plan_flush(bars, batch_size=4)
    assert len(plan) == 4
    assert plan[0].interval_start == _bar(0).interval_start  # FIFO head first


def test_plan_flush_empty_input_returns_empty() -> None:
    assert warm.plan_flush([], batch_size=240) == []


def test_cursor_regression_rejected() -> None:
    cur = warm.FlushCursor("deriv", "CRYPTO.BTCUSD", watermark=_bar(5).interval_start)
    with pytest.raises(warm.CursorRegressionError):
        cur.advance(_bar(3).interval_start)


def test_cursor_equal_watermark_is_idempotent_noop() -> None:
    cur = warm.FlushCursor("deriv", "CRYPTO.BTCUSD", watermark=_bar(5).interval_start)
    got = cur.advance(_bar(5).interval_start)
    assert got.watermark == _bar(5).interval_start


def test_cursor_initial_state_accepts_first_advance() -> None:
    cur = warm.FlushCursor("deriv", "CRYPTO.BTCUSD")
    assert cur.watermark is None
    assert cur.advance(_bar(0).interval_start).watermark == _bar(0).interval_start


def test_max_contiguous_stops_at_gap() -> None:
    # out-of-order: cursor must not leap over the missing minute (spec §4)
    wm = _bar(0).interval_start
    starts = [_bar(1).interval_start, _bar(2).interval_start, _bar(4).interval_start]
    assert warm.max_contiguous(wm, starts) == _bar(2).interval_start


def test_max_contiguous_gap_fill_resumes() -> None:
    wm = warm.max_contiguous(
        _bar(0).interval_start,
        [_bar(1).interval_start, _bar(2).interval_start, _bar(4).interval_start],
    )
    assert wm == _bar(2).interval_start
    # next flush supplies the gap minute AND the stale one — stale ignored, chain extends
    got = warm.max_contiguous(wm, [_bar(3).interval_start, _bar(4).interval_start, _bar(1).interval_start])
    assert got == _bar(4).interval_start


def test_max_contiguous_no_progress_keeps_watermark() -> None:
    wm = _bar(3).interval_start
    assert warm.max_contiguous(wm, [_bar(1).interval_start]) == wm  # only stale entries
    assert warm.max_contiguous(wm, []) == wm


def test_max_contiguous_fresh_cursor_from_earliest() -> None:
    # watermark=None: chain starts at earliest STORED bar; gap stops it (never leap ahead)
    starts = [_bar(1).interval_start, _bar(2).interval_start]
    assert warm.max_contiguous(None, starts) == _bar(2).interval_start
    assert warm.max_contiguous(None, [_bar(1).interval_start, _bar(3).interval_start]) == _bar(1).interval_start
    assert warm.max_contiguous(None, []) is None


# ---- store reads: inclusive bounds, order, never fail-open -----------------------

async def test_read_1m_empty_returns_empty_list_not_error() -> None:
    store, conn = _store(FakeConn(fetch_results=[[]]))
    got = await store.read_1m("deriv", "CRYPTO.BTCUSD", _bar(0).interval_start, _bar(5).interval_end)
    assert got == []  # empty instrument → [] (spec edge), NOT None, NOT error
    assert any("ORDER BY interval_start ASC" in sql for sql, _ in conn.calls)


async def test_read_1m_inclusive_bounds_params_and_order() -> None:
    store, conn = _store(FakeConn(fetch_results=[[]]))
    start, end = _bar(0).interval_start, _bar(5).interval_end
    await store.read_1m("deriv", "CRYPTO.BTCUSD", start, end)
    sql, args = conn.calls[0]
    assert "interval_start >= " in sql and "interval_start <= " in sql  # inclusive both ends
    assert "ORDER BY interval_start ASC" in sql
    assert "provider = " in sql and "instrument_id = " in sql
    assert args == ("deriv", "CRYPTO.BTCUSD", start, end)


async def test_read_1m_decodes_rows_to_candles_in_order() -> None:
    bars = [_bar(0), _bar(1), _bar(2)]
    store, _ = _store(FakeConn(fetch_results=[[_row(b) for b in bars]]))
    got = await store.read_1m("deriv", "CRYPTO.BTCUSD", bars[0].interval_start, bars[2].interval_end)
    assert got == bars


async def test_read_1m_uses_exact_requested_window() -> None:
    # A46 caller-controlled depth: window args reach SQL verbatim — no planner inside WARM
    store, conn = _store(FakeConn(fetch_results=[[]]))
    start, end = _bar(2).interval_start, _bar(9).interval_end
    await store.read_1m("deriv", "CRYPTO.BTCUSD", start, end)
    sql, args = conn.calls[0]
    assert args == ("deriv", "CRYPTO.BTCUSD", start, end)  # unshifted, unpadded, verbatim
    assert "interval_start >= $3" in sql and "interval_start <= $4" in sql


async def test_read_1m_does_not_expand_window() -> None:
    # A46: nothing outside the request is auto-added — UI "load more" lives ABOVE WARM
    inside = _bar(4)
    store, _ = _store(FakeConn(fetch_results=[[_row(inside)]]))
    got = await store.read_1m("deriv", "CRYPTO.BTCUSD", _bar(3).interval_start, _bar(5).interval_end)
    assert got == [inside]  # exactly the requested window's content — no synthetic padding rows


async def test_read_1m_does_not_return_rows_outside_requested_window() -> None:
    # A46 invariant: requested_start <= interval_start <= requested_end — defensive filter,
    # so even a wrong SQL result or a look-ahead-leaking caller habit cannot widen the range
    before, inside, after = _bar(1), _bar(3), _bar(7)
    store, _ = _store(FakeConn(fetch_results=[[_row(before), _row(inside), _row(after)]]))
    got = await store.read_1m("deriv", "CRYPTO.BTCUSD", _bar(2).interval_start, _bar(5).interval_end)
    assert got == [inside]


async def test_read_1m_without_connection_raises_not_fake_empty() -> None:
    # Durable path NEVER fail-open (contrast M2.9): [] would lie "no bars exist"
    store = warm.Candles1mStore(_cfg())  # pool=None
    with pytest.raises(warm.WarmError, match="connect"):
        await store.read_1m("deriv", "CRYPTO.BTCUSD", _T0, _T0)


async def test_flush_without_connection_raises_not_silent_drop() -> None:
    store = warm.Candles1mStore(_cfg())
    with pytest.raises(warm.WarmError, match="connect"):
        await store.flush_batch("deriv", "CRYPTO.BTCUSD", [_bar(0)])


# ---- flush: ack → cursor → evict ordering, idempotency, dead endpoint ------------

async def test_flush_success_acks_then_saves_cursor_then_reports_evict() -> None:
    bars = [_bar(2), _bar(0), _bar(1)]  # deliberately unsorted input
    conn = FakeConn(
        fetchval_results=[None],                     # load cursor (fresh)
        execute_status=["INSERT 0 3"],               # upsert ack
        fetch_results=[[_bar(2).interval_start, _bar(0).interval_start, _bar(1).interval_start]],
    )
    store, _ = _store(conn)
    rep = await store.flush_batch("deriv", "CRYPTO.BTCUSD", bars)

    assert rep.acked is True and rep.inserted == 3
    assert rep.cursor.watermark == _bar(2).interval_start
    # flush-before-evict: ring evicts only what this ack covers, FIFO ascending
    assert rep.evicted == (_bar(0), _bar(1), _bar(2))
    # ack BEFORE cursor save — crash between the two re-offers the batch (at-least-once)
    insert_at = next(i for i, (sql, _) in enumerate(conn.calls) if "INSERT INTO candles_1m" in sql)
    save_at = next(i for i, (sql, _) in enumerate(conn.calls) if "EXCLUDED.watermark" in sql)
    assert insert_at < save_at
    save_sql = conn.calls[save_at][0]
    assert re.search(r"watermark\s*<\s*EXCLUDED\.watermark", save_sql), "monotonic DB guard"


async def test_flush_duplicate_reflush_zero_new_cursor_unchanged() -> None:
    # Spec edge: duplicate re-flush → 0 new rows (idempotent), cursor unchanged
    wm = _bar(2).interval_start
    conn = FakeConn(
        fetchval_results=[wm],
        execute_status=["INSERT 0 0"],  # every row conflicts → zero new
        fetch_results=[[]],             # nothing stored beyond cursor
    )
    store, _ = _store(conn)
    rep = await store.flush_batch("deriv", "CRYPTO.BTCUSD", [_bar(0), _bar(1), _bar(2)])
    assert rep.inserted == 0
    assert rep.acked is True
    assert rep.cursor.watermark == wm
    assert "EXCLUDED.watermark" not in "".join(sql for sql, _ in conn.calls)  # no save


async def test_flush_dead_on_load_typed_error_no_write() -> None:
    conn = FakeConn(fail={"fetchval"})
    store, _ = _store(conn)
    with pytest.raises(warm.FlushError):
        await store.flush_batch("deriv", "CRYPTO.BTCUSD", [_bar(0)])
    assert _executed(conn, "INSERT INTO candles_1m") == []   # nothing written
    assert "EXCLUDED.watermark" not in "".join(sql for sql, _ in conn.calls)


async def test_flush_dead_on_write_typed_error_cursor_not_saved() -> None:
    # dead Timescale mid-flush: typed error, batch retained (no report → ring not evicted)
    conn = FakeConn(fetchval_results=[None], fail={"execute"})
    store, _ = _store(conn)
    with pytest.raises(warm.FlushError):
        await store.flush_batch("deriv", "CRYPTO.BTCUSD", [_bar(0), _bar(1)])
    assert "EXCLUDED.watermark" not in "".join(sql for sql, _ in conn.calls)  # cursor untouched


async def test_flush_out_of_order_batches_final_set_identical() -> None:
    # Spec edge: out-of-order + interleaved → final stored set identical; cursor = max contiguous.
    # t0 delayed: batch1 offers t1,t2 first. Upsert-by-key still stores everything later.
    t0, t1, t2 = _bar(0), _bar(1), _bar(2)
    conn1 = FakeConn(
        fetchval_results=[None],
        execute_status=["INSERT 0 2"],
        fetch_results=[[t2.interval_start, t1.interval_start]],  # reversed on purpose
    )
    store1, _ = _store(conn1)
    rep1 = await store1.flush_batch("deriv", "CRYPTO.BTCUSD", [t1, t2])
    assert rep1.cursor.watermark == t2.interval_start  # contiguous t1→t2
    assert rep1.evicted == (t1, t2)                    # FIFO even from unsorted input

    # batch2 delivers the delayed t0 — at-least-once: NO cursor filter drops it
    conn2 = FakeConn(
        fetchval_results=[rep1.cursor.watermark],
        execute_status=["INSERT 0 1"],   # t0 upserted (would be missing under a cursor filter)
        fetch_results=[[]],              # nothing newer than t2 stored yet
    )
    store2, _ = _store(conn2)
    rep2 = await store2.flush_batch("deriv", "CRYPTO.BTCUSD", [t0])
    assert _executed(conn2, "INSERT INTO candles_1m"), "t0 must reach the store (no silent skip)"
    assert rep2.inserted == 1
    assert rep2.cursor.watermark == t2.interval_start  # max contiguous — did not leap/regress
    assert "EXCLUDED.watermark" not in "".join(sql for sql, _ in conn2.calls)  # no progress → no save


async def test_flush_junk_rejected_before_any_query() -> None:
    # validation happens before first I/O — encode-all-first, garbage never touches the store
    conn = FakeConn(fetchval_results=[None])
    store, _ = _store(conn)
    good, junk = _bar(0), _bar(1).model_copy(
        update={"interval_start": _T0 + timedelta(minutes=5), "interval_end": _T0}
    )
    with pytest.raises(warm.StoreValidationError):
        await store.flush_batch("deriv", "CRYPTO.BTCUSD", [good, junk])
    assert _executed(conn, "INSERT INTO candles_1m") == []


async def test_flush_empty_batch_returns_ack_zero_untouched_cursor() -> None:
    wm = _bar(4).interval_start
    conn = FakeConn(fetchval_results=[wm])
    store, _ = _store(conn)
    rep = await store.flush_batch("deriv", "CRYPTO.BTCUSD", [])
    assert rep.acked is True and rep.inserted == 0 and rep.evicted == ()
    assert rep.cursor.watermark == wm
    assert _executed(conn, "INSERT INTO candles_1m") == []


async def test_flush_restart_new_instance_resumes_persisted_cursor() -> None:
    # Spec edge: restart mid-flush → resume from persisted cursor; no bar duplicated
    # (dup protection = ON CONFLICT DO NOTHING, at-least-once re-offer is safe)
    wm = _bar(2).interval_start
    conn = FakeConn(
        fetchval_results=[wm],            # persisted watermark comes back
        execute_status=["INSERT 0 0"],    # re-offer conflicts everywhere
        fetch_results=[[]],
    )
    store, _ = _store(conn)
    rep = await store.flush_batch("deriv", "CRYPTO.BTCUSD", [_bar(0), _bar(1), _bar(2)])
    loads = [args for sql, args in conn.calls if "flush_cursors" in sql and "SELECT" in sql.upper()]
    assert loads == [("deriv", "CRYPTO.BTCUSD")]          # resumed exactly from cursor
    assert rep.cursor.watermark == wm and rep.inserted == 0  # no duplicate, no regression


# ---- connect: redaction, lazy asyncpg, close, backoff ----------------------------

async def test_connect_failure_typed_error_redacts_password() -> None:
    async def boom(dsn: str, **kw: object) -> Any:
        raise OSError(f"cannot reach {dsn}")

    store = warm.Candles1mStore(_cfg(), pool_factory=boom)  # type: ignore[arg-type]
    with pytest.raises(warm.WarmError) as excinfo:
        await store.connect()
    msg = str(excinfo.value)
    assert "SEKRET-99" not in msg
    assert "SEKRET-99" not in repr(excinfo.value)
    assert "127.0.0.1" in msg  # host stays for debuggability (spec §2)


def test_module_is_free_of_module_level_asyncpg() -> None:
    # hermetic: asyncpg imported lazily inside connect() only (m2.9 lazy-redis pattern)
    assert not hasattr(warm, "asyncpg"), "asyncpg must never be a module-level import"


async def test_close_idempotent_returns_none() -> None:
    store, conn = _store()
    assert await store.close() is None
    assert await store.close() is None
    assert sum(1 for sql, _ in conn.calls if sql == "TERMINATE") <= 1


def test_backoff_bounded_grows_no_retry_storm() -> None:
    # §2 transient-error law: exponential + jitter, hard-capped (no retry storms)
    rng = random.Random(42)
    ds = [warm.backoff_seconds(a, rng=rng) for a in range(11)]
    assert all(0.0 < d <= 8.0 for d in ds)
    assert ds[0] < ds[3] < ds[6]  # exponential growth dominates jitter
