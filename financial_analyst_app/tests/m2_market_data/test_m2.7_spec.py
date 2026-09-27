"""
P13 / M2.7 Recovery — watermark gap detect + backfill — Spec tests REV2.1.
WHY: AGENTS.md §2 — A21 revoked (disjoint to_interval); A22 LGTM on REV2.1
(§9 three-run + run-boundary, §7 multi-interval chunking). Spec REV2.1.

Contracts: in-module RecoveryResult; no clock; M2.2 owns watermark allocation (frozen).
pytest-asyncio mode=auto.
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

# Direct impl import — recovery.py must expose to_intervals after REV2 fix.
from modules.m2_market_data.recovery import (
    ChunkOrchestrator,
    GapAnalyzer,
    RecoveryEngine,
    RecoveryResult,
)

# --- GapAnalyzer ---

def test_first_watermark_no_gap() -> None:
    # Spec Correct 1 / cold start: no invented 1..N
    g = GapAnalyzer()
    assert g.observe("FX.EURUSD", "deriv", 1001) == []


def test_contiguous_next_no_gap() -> None:
    g = GapAnalyzer()
    g.observe("FX.EURUSD", "deriv", 1)
    assert g.observe("FX.EURUSD", "deriv", 2) == []


def test_single_hole_missing_list() -> None:
    g = GapAnalyzer()
    g.observe("FX.EURUSD", "deriv", 1)
    assert g.observe("FX.EURUSD", "deriv", 3) == [2]


def test_multi_hole_missing_range() -> None:
    g = GapAnalyzer()
    g.observe("FX.EURUSD", "deriv", 1)
    assert g.observe("FX.EURUSD", "deriv", 5) == [2, 3, 4]


def test_independent_keys_no_cross_talk() -> None:
    g = GapAnalyzer()
    g.observe("FX.EURUSD", "deriv", 1)
    g.observe("FX.EURUSD", "mt5", 1)
    assert g.observe("FX.EURUSD", "deriv", 3) == [2]
    assert g.observe("FX.EURUSD", "mt5", 3) == [2]
    assert g.observe("FX.EURUSD", "deriv", 10) == list(range(4, 10))


def test_stale_or_duplicate_watermark_ignored_last_held() -> None:
    g = GapAnalyzer()
    g.observe("FX.EURUSD", "deriv", 5)
    assert g.observe("FX.EURUSD", "deriv", 5) == []
    assert g.observe("FX.EURUSD", "deriv", 3) == []
    assert g.observe("FX.EURUSD", "deriv", 7) == [6]


def test_to_intervals_empty_returns_empty_list() -> None:
    # Spec REV2: empty → [] (not None)
    g = GapAnalyzer()
    assert g.to_intervals([]) == []


def test_to_intervals_contiguous_single_run() -> None:
    g = GapAnalyzer()
    assert g.to_intervals([2, 3, 4]) == [(2, 4)]
    assert g.to_intervals([7]) == [(7, 7)]
    assert g.to_intervals([4, 2, 3]) == [(2, 4)]  # order-insensitive
    assert g.to_intervals([3, 2, 3, 5]) == [(2, 3), (5, 5)]  # dedup + sort (review)


def test_to_intervals_disjoint_multiple_runs() -> None:
    # Spec REV2 blocker: [5,7] → two runs — 6 is NOT part of recovery
    g = GapAnalyzer()
    assert g.to_intervals([5, 7]) == [(5, 5), (7, 7)]


def test_to_intervals_disjoint_no_invented_middle() -> None:
    # Spec REV2: [5,6,10,11,12] → [(5,6),(10,12)] — 7,8,9 never in any run
    g = GapAnalyzer()
    runs = g.to_intervals([5, 6, 10, 11, 12])
    assert runs == [(5, 6), (10, 12)]
    covered = set()
    for a, b in runs:
        covered.update(range(a, b + 1))
    assert covered == {5, 6, 10, 11, 12}
    assert 7 not in covered and 8 not in covered and 9 not in covered


def test_to_intervals_three_runs_including_trailing_singleton() -> None:
    # Spec §9 REV2.1: [5,6,10,11,12,20] → three runs — not one min–max span
    g = GapAnalyzer()
    assert g.to_intervals([5, 6, 10, 11, 12, 20]) == [(5, 6), (10, 12), (20, 20)]


def test_to_intervals_run_boundary_five_seven_eight() -> None:
    # Spec §9 REV2.1: [5,7,8] → [(5,5),(7,8)] — off-by-one / run-builder boundary
    g = GapAnalyzer()
    assert g.to_intervals([5, 7, 8]) == [(5, 5), (7, 8)]


def test_adversarial_watermark_zero_or_negative_valueerror() -> None:
    g = GapAnalyzer()
    with pytest.raises(ValueError):
        g.observe("FX.EURUSD", "deriv", 0)
    with pytest.raises(ValueError):
        g.observe("FX.EURUSD", "deriv", -1)


def test_adversarial_watermark_bool_false_or_str_float_valueerror() -> None:
    # Spec Adversarial REV2: True/False/"3"/1.0 all ValueError
    g = GapAnalyzer()
    with pytest.raises(ValueError):
        g.observe("FX.EURUSD", "deriv", True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        g.observe("FX.EURUSD", "deriv", False)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        g.observe("FX.EURUSD", "deriv", "3")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        g.observe("FX.EURUSD", "deriv", 1.0)  # type: ignore[arg-type]


# --- ChunkOrchestrator ---

def test_split_even_span() -> None:
    c = ChunkOrchestrator(chunk_size=2)
    assert c.split((1, 4)) == [(1, 2), (3, 4)]


def test_split_remainder_last_chunk_shorter() -> None:
    c = ChunkOrchestrator(chunk_size=2)
    assert c.split((2, 4)) == [(2, 3), (4, 4)]


def test_split_size_three_remainder() -> None:
    # Spec Correct 13: (5,14) size 3 → (5,7)(8,10)(11,13)(14,14)
    c = ChunkOrchestrator(chunk_size=3)
    assert c.split((5, 14)) == [(5, 7), (8, 10), (11, 13), (14, 14)]


def test_split_chunk_size_gt_span_single() -> None:
    c = ChunkOrchestrator(chunk_size=100)
    assert c.split((2, 4)) == [(2, 4)]


def test_adversarial_chunk_size_zero_valueerror() -> None:
    with pytest.raises(ValueError):
        ChunkOrchestrator(chunk_size=0)
    with pytest.raises(ValueError):
        ChunkOrchestrator(chunk_size=-5)


# --- RecoveryEngine.on_gap ---

def _engine_logs(chunk_size: int = 2):
    choose_calls: list[tuple[str, str]] = []
    fetch_calls: list[tuple[tuple[int, int], str]] = []
    write_calls: list[tuple[Sequence, str, str, tuple[int, int]]] = []

    def choose(instrument_id: str, broker: str) -> str:
        choose_calls.append((instrument_id, broker))
        return "rest_candles"

    async def fetch(chunk: tuple[int, int], source: str) -> list:
        fetch_calls.append((chunk, source))
        return [f"row_{chunk[0]}_{chunk[1]}"]

    async def write(
        data: Sequence, instrument_id: str, broker: str, chunk: tuple[int, int]
    ) -> None:
        write_calls.append((data, instrument_id, broker, chunk))

    eng = RecoveryEngine(choose_source=choose, fetch=fetch, write_backfill=write, chunk_size=chunk_size)
    return eng, fetch_calls, write_calls, choose_calls


def test_happy_path_ok_all_chunks() -> None:
    # Contiguous [2,3,4] → one run (2,4) size 2 → [(2,3),(4,4)]
    eng, fetch_calls, write_calls, choose_calls = _engine_logs()
    result = asyncio.run(eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=[2, 3, 4]))
    assert isinstance(result, RecoveryResult)
    assert result.status == "ok"
    assert result.missing_count == 3
    assert result.chunk_count == 2
    assert result.chunks_done == 2
    assert result.source == "rest_candles"
    assert choose_calls == [("FX.EURUSD", "deriv")]
    assert [c for c, _ in fetch_calls] == [(2, 3), (4, 4)]
    assert write_calls[0][0] == ["row_2_3"]
    assert write_calls[0][1:3] == ("FX.EURUSD", "deriv")
    assert write_calls[1][0] == ["row_4_4"]


def test_disjoint_missing_fetches_only_listed_points() -> None:
    # Spec REV2 blocker: [5,7] must fetch (5,5) and (7,7) only — never (5,7) span
    eng, fetch_calls, _write_calls, choose_calls = _engine_logs(chunk_size=10)
    result = asyncio.run(eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=[5, 7]))
    assert result.status == "ok"
    assert result.missing_count == 2  # unique requested, not span length
    assert result.chunk_count == 2
    assert result.chunks_done == 2
    assert [c for c, _ in fetch_calls] == [(5, 5), (7, 7)]
    assert (5, 7) not in [c for c, _ in fetch_calls]
    # covered watermarks exactly {5,7}
    covered = set()
    for c, _ in fetch_calls:
        covered.update(range(c[0], c[1] + 1))
    assert covered == {5, 7}
    assert 6 not in covered
    assert len(choose_calls) == 1


def test_three_run_disjoint_chunk_count_three() -> None:
    # Spec §9 REV2.1: [5,6,10,11,12,20] size 100 → 3 runs → 3 chunks, choose once
    eng, fetch_calls, _w, choose_calls = _engine_logs(chunk_size=100)
    result = asyncio.run(
        eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=[5, 6, 10, 11, 12, 20])
    )
    assert result.status == "ok"
    assert result.missing_count == 6
    assert result.chunk_count == 3
    assert result.chunks_done == 3
    assert [c for c, _ in fetch_calls] == [(5, 6), (10, 12), (20, 20)]
    covered: set[int] = set()
    for c, _ in fetch_calls:
        covered.update(range(c[0], c[1] + 1))
    assert covered == {5, 6, 10, 11, 12, 20}
    assert 7 not in covered and 13 not in covered and 19 not in covered
    assert len(choose_calls) == 1


def test_multi_interval_chunking_within_runs() -> None:
    # Spec §7 REV2.1: missing 1,2,3,10..14 size 3 → (1,3)(10,12)(13,14); choose once
    eng, fetch_calls, _w, choose_calls = _engine_logs(chunk_size=3)
    missing = [1, 2, 3, 10, 11, 12, 13, 14]
    result = asyncio.run(eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=missing))
    assert result.status == "ok"
    assert result.missing_count == 8
    assert result.chunk_count == 3
    assert result.chunks_done == 3
    assert [c for c, _ in fetch_calls] == [(1, 3), (10, 12), (13, 14)]
    covered: set[int] = set()
    for c, _ in fetch_calls:
        covered.update(range(c[0], c[1] + 1))
    assert covered == set(missing)
    assert len(choose_calls) == 1


def test_three_run_split_size_two_fetch_args() -> None:
    # Review-required: [5,6,10,11,12] size 2 → fetch (5,6)(10,11)(12,12) — actual args
    eng, fetch_calls, _w, choose_calls = _engine_logs(chunk_size=2)
    result = asyncio.run(
        eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=[5, 6, 10, 11, 12])
    )
    assert result.status == "ok"
    assert result.chunk_count == 3
    assert [c for c, _ in fetch_calls] == [(5, 6), (10, 11), (12, 12)]
    covered: set[int] = set()
    for c, _ in fetch_calls:
        covered.update(range(c[0], c[1] + 1))
    assert covered == {5, 6, 10, 11, 12}
    assert 7 not in covered and 9 not in covered
    assert len(choose_calls) == 1


def test_disjoint_two_plus_two_fetch_args() -> None:
    # Review-required: missing [2,3,6,7] size 2 → fetch (2,3)(6,7) — never invent 4,5
    eng, fetch_calls, _w, choose_calls = _engine_logs(chunk_size=2)
    result = asyncio.run(eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=[2, 3, 6, 7]))
    assert result.status == "ok"
    assert result.missing_count == 4
    assert result.chunk_count == 2
    assert [c for c, _ in fetch_calls] == [(2, 3), (6, 7)]
    assert (4, 5) not in [c for c, _ in fetch_calls]
    covered: set[int] = set()
    for c, _ in fetch_calls:
        covered.update(range(c[0], c[1] + 1))
    assert covered == {2, 3, 6, 7}
    assert 4 not in covered and 5 not in covered
    assert len(choose_calls) == 1


def test_fail_third_of_four_chunks_partial_stops() -> None:
    # Spec Correct 14: (5,14) size 3 → 4 chunks; fail #3 → done=2, #4 never
    fetch_n = {"n": 0}
    fetch_log: list[tuple[int, int]] = []

    def choose(instrument_id: str, broker: str) -> str:
        return "rest"

    async def fetch(chunk: tuple[int, int], source: str) -> list:
        fetch_n["n"] += 1
        fetch_log.append(chunk)
        if fetch_n["n"] >= 3:
            raise RuntimeError("chunk3 down")
        return ["x"]

    async def write(data, instrument_id, broker, chunk) -> None:
        pass

    eng = RecoveryEngine(choose_source=choose, fetch=fetch, write_backfill=write, chunk_size=3)
    result = asyncio.run(eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=list(range(5, 15))))
    assert result.status == "partial"
    assert result.chunks_done == 2
    assert result.chunk_count == 4
    assert len(fetch_log) == 3  # 4th never attempted
    assert fetch_log == [(5, 7), (8, 10), (11, 13)]


def test_fetch_fail_second_chunk_partial() -> None:
    def choose(instrument_id: str, broker: str) -> str:
        return "rest"

    state = {"n": 0}

    async def fetch(chunk, source):
        state["n"] += 1
        if state["n"] >= 2:
            raise RuntimeError("fetch boom")
        return ["ok1"]

    async def write(data, instrument_id, broker, chunk) -> None:
        pass

    eng = RecoveryEngine(choose_source=choose, fetch=fetch, write_backfill=write, chunk_size=2)
    result = asyncio.run(eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=[2, 3, 4]))
    assert result.status == "partial"
    assert result.chunks_done == 1
    assert result.chunk_count == 2
    assert "fetch boom" in result.detail


def test_fetch_fail_first_chunk_failed() -> None:
    def choose(instrument_id: str, broker: str) -> str:
        return "rest"

    async def fetch(chunk, source):
        raise RuntimeError("down")

    async def write(data, instrument_id, broker, chunk) -> None:
        raise AssertionError("write must not run")

    eng = RecoveryEngine(choose_source=choose, fetch=fetch, write_backfill=write, chunk_size=10)
    result = asyncio.run(eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=[2, 3, 4]))
    assert result.status == "failed"
    assert result.chunks_done == 0


def test_empty_missing_valueerror_before_io() -> None:
    def choose(instrument_id: str, broker: str) -> str:
        raise AssertionError("choose must not run")

    async def fetch(chunk, source):
        raise AssertionError

    async def write(data, instrument_id, broker, chunk):
        raise AssertionError

    eng = RecoveryEngine(choose_source=choose, fetch=fetch, write_backfill=write)
    with pytest.raises(ValueError):
        asyncio.run(eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=[]))


def test_source_chosen_once_fetch_order_ascending() -> None:
    eng, fetch_calls, _w, choose_calls = _engine_logs()
    asyncio.run(eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=[10, 11, 12, 13, 14]))
    assert len(choose_calls) == 1
    starts = [c[0] for c, _ in fetch_calls]
    assert starts == sorted(starts)
    assert len(fetch_calls) == 3  # 10-14 size 2 → 3


# --- DEFECT HUNT 2026-09-27 (human: "any error is prohibited", worst case) ---
# Both tests below were observed FAILING against the REV2.1 implementation before the fix.

async def test_empty_fetch_is_NOT_reported_as_recovered() -> None:
    """DEFECT 1 (silent data loss): a chunk whose fetch returns ZERO rows was counted as
    done and the whole run reported status="ok". The data was gone forever and nobody was
    ever told. A recovery that cannot fail is worse than no recovery, because it converts
    data loss into silence."""

    async def fetch_nothing(chunk, source):
        return []

    async def write(data, instrument_id, broker, chunk):
        return None

    eng = RecoveryEngine(
        choose_source=lambda i, b: "lake",
        fetch=fetch_nothing,
        write_backfill=write,
        chunk_size=10,
    )
    result = await eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=[1, 2, 3])
    assert result.status != "ok", "an empty backfill must never report ok"
    assert result.chunks_done == 0, "a chunk that returned no rows is not done"
    assert result.status == "failed"
    # The one contiguous run [1,2,3] is a single chunk (1,3) at chunk_size=10.
    assert "1-3" in result.detail, (
        f"the detail must name the unrecovered range, got {result.detail!r}"
    )
    assert "permanently missing" in result.detail


async def test_partly_empty_fetch_reports_partial_not_ok() -> None:
    """DEFECT 1, partial case: one chunk returns rows, the next returns none."""

    async def fetch_sometimes(chunk, source):
        return [1, 2, 3] if chunk[0] == 1 else []

    async def write(data, instrument_id, broker, chunk):
        return None

    eng = RecoveryEngine(
        choose_source=lambda i, b: "lake",
        fetch=fetch_sometimes,
        write_backfill=write,
        chunk_size=1,
    )
    result = await eng.on_gap("FX.EURUSD", "deriv", missing_watermarks=[1, 2, 3])
    assert result.status == "partial", f"got {result.status}"
    assert result.chunks_done == 1


def test_absurd_watermark_jump_does_not_exhaust_memory() -> None:
    """DEFECT 2 (well-known DoS on the hot path): observe() did
    list(range(last + 1, watermark)), materialising one int per missing tick. A corrupted
    or spoofed watermark - or simply a long outage - built a 50-million-element list
    (~1.8 GB) in about a second, on the ingest path, blowing the <50ms tick->parquet
    budget and risking an OOM kill mid-session. A gap is a RANGE, so it must be carried as
    a range and bounded, never expanded into a list."""
    g = GapAnalyzer()
    g.observe("FX.EURUSD", "deriv", 1)
    with pytest.raises(ValueError, match="exceed"):
        g.observe("FX.EURUSD", "deriv", 50_000_000)


def test_large_but_bounded_watermark_jump_is_reported_as_a_range() -> None:
    """A big-but-acceptable hole must still be detected, and cheaply."""
    g = GapAnalyzer()
    g.observe("FX.EURUSD", "deriv", 1)
    missing = g.observe("FX.EURUSD", "deriv", 1001)
    assert len(missing) == 999
    assert missing[0] == 2 and missing[-1] == 1000
