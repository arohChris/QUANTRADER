"""M2.1-live capture helpers — WHY this module exists.

Pure, socket-free unit behind `scripts/verify_m2.1_live_capture.py` (spec
`docs/specs/m2.1_live_capture.spec.md`): frame building, arg validation, response
classification, JSONL roundtrip, connect retry (local DNS is flaky), and the
read-back asserts that close the G0 "data layer has never held real data" bug.
Network lives ONLY in the verify script (Step 7); pytest imports this file safely.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Any

# WHY re-export from M2.2: single HALF_UP rounding source (spec Correctness) —
# never re-implement round_to_tick here or the two paths can drift.
from modules.m2_market_data.normalize import _round_to_tick as round_to_tick

__all__ = [
    "CaptureError",
    "CaptureMismatchError",
    "ReadbackReport",
    "assert_readback",
    "build_capture_frame",
    "classify",
    "connect_with_retry",
    "filter_run_rows",
    "from_jsonl_line",
    "round_to_tick",
    "to_jsonl_line",
    "validate_args",
]


class CaptureError(Exception):
    """Capture-path failure (bad line, connect exhausted, ...)."""


class CaptureMismatchError(CaptureError):
    """Read-back assert failed; `.check` names the invariant, `.detail` the numbers."""

    def __init__(self, check: str, detail: str) -> None:
        super().__init__(f"{check}: {detail}")
        self.check = check
        self.detail = detail


@dataclass(frozen=True)
class ReadbackReport:
    captured: int
    accepted: int
    dropped: int
    first_price: Any
    last_price: Any


def build_capture_frame(symbol: str) -> str:
    # WHY no app_id in body: the A41-signed endpoint rejects body app_id
    # (InputValidationFailed, proven 2026-09-25); app_id travels in the URL query.
    # Deviation from frozen stream.build_tick_subscribe is spec-recorded.
    return json.dumps({"ticks": symbol, "subscribe": 1})


def validate_args(symbol: str, n_ticks: int) -> tuple[str, int]:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError(f"symbol must be a non-blank string, got {symbol!r}")
    if isinstance(n_ticks, bool) or not isinstance(n_ticks, int) or n_ticks < 1:
        raise ValueError(f"n_ticks must be an int >= 1, got {n_ticks!r}")
    return symbol, n_ticks


def classify(msg: dict) -> str:
    """Map an inbound frame to tick|closed|error|other (spec E2 classification)."""
    if not isinstance(msg, dict):
        return "other"
    err = msg.get("error")
    if not isinstance(err, dict):
        tick = msg.get("tick")
        if isinstance(tick, dict):
            err = tick.get("error")
    if isinstance(err, dict):
        # WHY check code first: MarketIsClosed arrives with msg_type "tick" on this feed.
        return "closed" if err.get("code") == "MarketIsClosed" else "error"
    tick = msg.get("tick")
    if msg.get("msg_type") == "tick" and isinstance(tick, dict) and "quote" in tick:
        return "tick"
    return "other"


def to_jsonl_line(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def from_jsonl_line(line: str) -> dict:
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise CaptureError(f"invalid JSONL line: {exc}") from exc
    if not isinstance(obj, dict):
        raise CaptureError(f"JSONL line must be a JSON object, got {type(obj).__name__}")
    return obj


def connect_with_retry(
    connect: Callable[[], Any], *, attempts: int, sleep: Callable[[float], None]
) -> Any:
    """Retry ONLY the connection phase (local DNS flakiness); first success wins."""
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError(f"attempts must be int >= 1, got {attempts!r}")
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return connect()
        except Exception as exc:  # noqa: BLE001 — any connect-phase failure is retryable
            last = exc
            if attempt < attempts:
                sleep(1.5)
    raise CaptureError(
        f"connect failed after {attempts} attempts: {type(last).__name__}: {last}"
    ) from last


def _frame_epoch(obj: dict) -> int | None:
    tick = obj.get("tick")
    if isinstance(tick, dict):
        ep = tick.get("epoch")
        if isinstance(ep, int) and not isinstance(ep, bool):
            return ep
    return None


def filter_run_rows(rows: list[dict], epochs: set[int]) -> list[dict]:
    # WHY: daily partitions may hold prior runs — asserts must see THIS run only (E5).
    return [r for r in rows if r.get("epoch") in epochs]


def assert_readback(
    *,
    captured: list[dict],
    rows: list[dict],
    drops: list[dict],
    tick_size: Decimal | str | float,
) -> ReadbackReport:
    if not captured:
        raise CaptureMismatchError("empty_capture", "0 ticks captured — empty run never passes")
    if len(captured) != len(rows) + len(drops):
        raise CaptureMismatchError(
            "reconcile",
            f"captured={len(captured)} rows={len(rows)} drops={len(drops)}",
        )
    for row in rows:
        ep = row.get("epoch")
        if not isinstance(ep, int) or isinstance(ep, bool):
            raise CaptureMismatchError("chronology", f"row epoch not an int: {ep!r}")
    for prev, cur in pairwise(rows):
        if cur["epoch"] < prev["epoch"]:
            raise CaptureMismatchError(
                "chronology", f"epoch {cur['epoch']} < previous {prev['epoch']}"
            )
    for row in rows:
        snap = row.get("reference_snapshot_id")
        if not isinstance(snap, str) or not snap:
            raise CaptureMismatchError(
                "lineage", f"epoch={row.get('epoch')!r} reference_snapshot_id={snap!r}"
            )
    size = Decimal(str(tick_size))
    drop_epochs = {d.get("epoch") for d in drops if isinstance(d.get("epoch"), int)}
    candidates = [
        c for c in captured if (ep := _frame_epoch(c)) is None or ep not in drop_epochs
    ]
    used = [False] * len(candidates)
    for row in rows:
        idx = next(
            (
                i
                for i, c in enumerate(candidates)
                if not used[i] and _frame_epoch(c) == row["epoch"]
            ),
            None,
        )
        if idx is None:
            raise CaptureMismatchError(
                "reconcile", f"no captured frame for stored row epoch={row['epoch']}"
            )
        used[idx] = True
        quote = candidates[idx].get("tick", {}).get("quote")
        try:
            expected = round_to_tick(Decimal(str(quote)), size)
        except Exception as exc:
            raise CaptureMismatchError(
                "price", f"epoch={row['epoch']} unroundable quote {quote!r}: {exc}"
            ) from exc
        actual = Decimal(str(row["price"]))
        if actual != expected:
            raise CaptureMismatchError(
                "price", f"epoch={row['epoch']} expected={expected} actual={actual}"
            )
    if drops and drop_epochs and len(drop_epochs) == len(drops) and not all(used):
        raise CaptureMismatchError(
            "reconcile", "frame neither dropped nor stored (leftover captured frame)"
        )
    return ReadbackReport(
        captured=len(captured),
        accepted=len(rows),
        dropped=len(drops),
        first_price=Decimal(str(rows[0]["price"])),
        last_price=Decimal(str(rows[-1]["price"])),
    )
