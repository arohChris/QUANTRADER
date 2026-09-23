"""
M2.7 — recovery — watermark gap detect + backfill orchestration.
WHY: M2.2 is the FROZEN sole watermark allocator (WatermarkRegistry.next);
this module observes holes, splits disjoint missing into contiguous runs only
(never invents seqs between runs), chunks, then injected async fetch/write.

Spec: docs/specs/m2.7_recovery.spec.md REV2.1 2026-09-23 (disjoint-gap + A22 LGTM).
Ownership freeze: see BUILD_STATUS Discrepancies — do not add next() here.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Iterable, Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

Chunk = tuple[int, int]  # inclusive (start, end)


class RecoveryResult(BaseModel):
    """In-module result — not a shared contract until M8/M12 consume it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["ok", "partial", "failed"] = Field(
        description="ok=all chunks; partial=some done then abort; failed=0 done"
    )
    missing_count: int  # unique watermarks requested (len(set(missing)))
    chunk_count: int
    chunks_done: int
    source: str
    detail: str = ""


class GapAnalyzer:
    """Per-(instrument_id, broker) last-seen watermark → missing lists (sync, no clock)."""

    def __init__(self) -> None:
        self._last: dict[tuple[str, str], int] = {}

    def observe(self, instrument_id: str, broker: str, watermark: int) -> list[int]:
        # bool is int subclass in Python — reject explicitly (spec Adversarial).
        if isinstance(watermark, bool) or not isinstance(watermark, int):
            raise ValueError(f"watermark must be int, got {type(watermark).__name__}")
        if watermark < 1:
            raise ValueError(f"watermark must be >= 1, got {watermark}")
        key = (instrument_id, broker)
        last = self._last.get(key)
        if last is None:
            self._last[key] = watermark
            return []  # cold start — do not invent 1..N
        if watermark <= last:
            return []  # stale/duplicate — hold last
        missing = list(range(last + 1, watermark))
        self._last[key] = watermark
        return missing

    @staticmethod
    def to_intervals(missing: Iterable[int]) -> list[Chunk]:
        """Contiguous inclusive runs only — never one global (min,max) for disjoint input.

        Pure/stateless: [5,7] → [(5,5),(7,7)] — 6 is not missing and must not be fetched.
        """
        vals = sorted(set(missing))
        if not vals:
            return []
        runs: list[Chunk] = []
        start = prev = vals[0]
        for v in vals[1:]:
            if v == prev + 1:
                prev = v
            else:
                runs.append((start, prev))
                start = prev = v
        runs.append((start, prev))
        return runs


class ChunkOrchestrator:
    """Split one inclusive interval into non-overlapping inclusive chunks (count-based)."""

    def __init__(self, *, chunk_size: int) -> None:
        if chunk_size < 1:
            raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
        self._size = chunk_size

    def split(self, interval: Chunk) -> list[Chunk]:
        start, end = interval
        if start > end:
            raise ValueError(f"interval start {start} > end {end}")
        chunks: list[Chunk] = []
        cur = start
        while cur <= end:
            stop = min(cur + self._size - 1, end)
            chunks.append((cur, stop))
            cur = stop + 1
        return chunks


class RecoveryEngine:
    """on_gap: choose source once → chunk every contiguous run → fetch → write."""

    def __init__(
        self,
        *,
        choose_source: Callable[[str, str], str],
        fetch: Callable[[Chunk, str], Awaitable[Sequence]],
        write_backfill: Callable[[Sequence, str, str, Chunk], Awaitable[None]],
        chunk_size: int = 100,
    ) -> None:
        self._choose = choose_source
        self._fetch = fetch
        self._write = write_backfill
        self._chunks = ChunkOrchestrator(chunk_size=chunk_size)

    async def on_gap(
        self,
        instrument_id: str,
        broker: str,
        *,
        missing_watermarks: Sequence[int],
    ) -> RecoveryResult:
        if not missing_watermarks:
            raise ValueError("missing_watermarks must be non-empty before I/O")
        missing_count = len(set(missing_watermarks))  # unique requested — not span
        source = self._choose(instrument_id, broker)  # once per recovery op
        # Disjoint law: only contiguous runs — no invented middle seqs.
        runs = GapAnalyzer.to_intervals(missing_watermarks)
        chunks: list[Chunk] = []
        for run in runs:
            chunks.extend(self._chunks.split(run))
        chunks_done = 0
        for chunk in chunks:
            try:
                data = await self._fetch(chunk, source)
                # Write fail ⇒ chunk not done; watermarks remain recoverable (spec REV2).
                await self._write(data, instrument_id, broker, chunk)
                chunks_done += 1
            except Exception as exc:  # abort remaining — retry lives outside M2.7
                detail = str(exc) or type(exc).__name__
                status: Literal["ok", "partial", "failed"] = "partial" if chunks_done else "failed"
                return RecoveryResult(
                    status=status,
                    missing_count=missing_count,
                    chunk_count=len(chunks),
                    chunks_done=chunks_done,
                    source=source,
                    detail=detail,
                )
        return RecoveryResult(
            status="ok",
            missing_count=missing_count,
            chunk_count=len(chunks),
            chunks_done=chunks_done,
            source=source,
            detail="",
        )


__all__ = ["ChunkOrchestrator", "GapAnalyzer", "RecoveryEngine", "RecoveryResult"]
