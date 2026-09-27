"""M10.2 Envelope — the one shape every HTTP reply takes.

WHY this exists: without a single shape, the frontend must understand three different
error formats (FastAPI `detail`, the exception's own `to_dict`, and the envelope). The
frontend therefore branches on one thing only: `error.code`.

Spec: docs/specs/m2_envelope.spec.md -> docs/specs/m10.2_envelope.spec.md (approved ACTION 70).
Tests: tests/m10_2/test_m10.2_envelope_spec.py (35, written BEFORE this file).

Consumers: every M10.2 router (leaves 2-9). Once leaf 2 imports this, it is frozen.
"""
from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from shared.contracts.errors import ErrorCode

# WHY caps: 1 KiB is far beyond any legitimate correlation id or cursor, and a bound is
# the only thing that stops a client making the process allocate arbitrary memory.
MAX_ID_LEN = 128
MAX_CURSOR_LEN = 1024
DEFAULT_LIMIT = 50
MAX_LIMIT = 500

# WHY this table is TOTAL: every frozen ErrorCode has a deliberate status. A code that
# silently falls through to 500 lies to monitoring and to client retry logic — a caller
# error reported as a server fault gets retried forever. `config_error` is the only
# member allowed to be a genuine 500, because it means the server is misconfigured.
_STATUS: dict[ErrorCode, int] = {
    # --- 401/403: who is asking ---
    ErrorCode.permission_denied: 403,
    ErrorCode.isolation_violation: 403,
    # --- 404: asked for something that does not exist ---
    ErrorCode.unknown_symbol: 404,
    # --- 409: state conflict; the same request may succeed later ---
    ErrorCode.duplicate_signal: 409,
    ErrorCode.duplicate_order: 409,
    ErrorCode.illegal_transition: 409,
    ErrorCode.stale_signal: 409,
    ErrorCode.stale_alpha: 409,
    ErrorCode.data_quality_invalid: 409,
    ErrorCode.trade_plan_expired: 409,
    ErrorCode.gap_detected: 409,
    # --- 422: the request itself is wrong or the rules refuse it ---
    ErrorCode.capability_not_supported: 422,
    ErrorCode.insufficient_margin: 422,
    ErrorCode.exposure_limit: 422,
    ErrorCode.leverage_limit: 422,
    ErrorCode.concentration_limit: 422,
    ErrorCode.spread_too_wide: 422,
    ErrorCode.liquidity_proxy: 422,
    ErrorCode.rr_too_low: 422,
    ErrorCode.missing_sl_tp: 422,
    ErrorCode.qty_step: 422,
    ErrorCode.insufficient_qty: 422,
    ErrorCode.negative_price: 422,
    ErrorCode.inverted_quote: 422,
    ErrorCode.future_timestamp: 422,
    # --- 503: the other side is down, or our own state is unresolvable ---
    ErrorCode.broker_unavailable: 503,
    ErrorCode.unknown_state: 503,
    # --- 500: genuinely the server's fault ---
    ErrorCode.config_error: 500,
}

# WHY: only these are worth retrying. Retrying insufficient_margin just burns a request.
_RETRYABLE = frozenset({ErrorCode.broker_unavailable, ErrorCode.unknown_state})


def _utc_z(value: datetime) -> str:
    """Render an aware datetime as RFC-3339 with an explicit Z.

    WHY normalize-then-render: a +02:00 wall clock of 12:00 IS 10:00Z. Relabelling the
    wall clock instead of converting it would move the instant, which is the exact bug the
    PIT layer exists to prevent.
    """
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_cid(correlation_id: str) -> str:
    if not isinstance(correlation_id, str) or not correlation_id:
        raise ValueError("correlation_id must be a non-empty string")
    if len(correlation_id) > MAX_ID_LEN:
        raise ValueError(f"correlation_id exceeds {MAX_ID_LEN} characters")
    return correlation_id


def _check_as_of(as_of: datetime) -> datetime:
    """Reject naive datetimes. WHY: AGENTS.md 3.8 — a naive time has no meaning; whichever
    machine reads it invents a timezone and silently shifts the data by hours."""
    if not isinstance(as_of, datetime):
        # WHY ValueError not TypeError: spec-frozen at E5. Every leaf treats this as one
        # bad-input class, and the gateway maps ValueError -> 422 in a single place.
        raise ValueError("as_of must be a datetime")  # noqa: TRY004
    if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
        raise ValueError("as_of must be tz-aware (UTC); naive datetimes are rejected")
    return as_of


def _serialise(value: Any) -> Any:
    """Make a payload JSON-safe.

    WHY Decimal -> str, not float: money must never round-trip through binary floating
    point. Decimal("12.00") becomes the string "12.00" so the frontend can render it
    exactly and a reader can tell 2dp from 5dp. AGENTS.md 3.1.

    WHY float -> rejected: this is the LAST gate before the browser, so it is the only
    place that can guarantee no float reaches the money path. Silently stringifying a
    float would hide a producer bug; rejecting it forces the fix at the source, where it
    is one `Decimal(...)` call. int is still allowed, because counts, sequences and
    quantities are exact in binary and carry no precision risk.
    """
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"non-finite Decimal is not serialisable: {value}")
        return format(value, "f")
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        raise TypeError(
            f"float is not permitted in an API payload ({value!r}); "
            "use Decimal for exact numerics so money keeps its precision"
        )
    if isinstance(value, datetime):
        return _utc_z(_check_as_of(value))
    if isinstance(value, dict):
        return {str(k): _serialise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialise(v) for v in value]
    if isinstance(value, UUID):
        return str(value)
    return value


def envelope_success(
    data: Any,
    *,
    correlation_id: str,
    as_of: datetime,
    schema_version: str,
) -> dict[str, Any]:
    """Wrap a successful result. Returns a plain dict, ready for JSONResponse."""
    return {
        "data": _serialise(data),
        "meta": {
            "correlation_id": _check_cid(correlation_id),
            "as_of": _utc_z(_check_as_of(as_of)),
            "schema_version": schema_version,
            "next_cursor": None,
            "has_more": False,
        },
        "error": None,
    }


def envelope_error(
    code: ErrorCode,
    message: str,
    *,
    correlation_id: str,
    as_of: datetime,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap a failure. The code MUST be an ErrorCode member.

    WHY a member and not a string: a bare string is the only way a typo like
    "INSUFFICENT_MARGIN" reaches the wire and the frontend silently stops matching it.
    """
    if not isinstance(code, ErrorCode):
        raise TypeError(
            f"code must be an ErrorCode member, got {type(code).__name__}: {code!r}"
        )
    return {
        "data": None,
        "meta": {
            "correlation_id": _check_cid(correlation_id),
            "as_of": _utc_z(_check_as_of(as_of)),
        },
        "error": {
            "code": code.value,
            "message": message,
            "retryable": code in _RETRYABLE,
            "details": _serialise(details or {}),
        },
    }


def http_status_for(code: ErrorCode) -> int:
    """Map a frozen error code to its HTTP status. Unmapped -> 500, never a guess."""
    return _STATUS.get(code, 500)


def validate_limit(raw: Any) -> int:
    """Validate a page size.

    WHY reject rather than clamp: a client asking for 10,000 rows is a bug or an attack.
    Silently returning 500 hides it from them; an explicit 422 tells them.
    """
    if raw is None:
        return DEFAULT_LIMIT
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"limit must be an integer, got {raw!r}")  # noqa: TRY004
    if raw < 1 or raw > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}, got {raw}")
    return raw


@dataclass(frozen=True, slots=True)
class Page:
    """One page of results plus how to get the next. Frozen: a handler cannot mutate a
    page after it is built, so what the frontend receives is what was paginated."""

    rows: list[Any]
    has_more: bool
    next_cursor: str | None


def _mint_cursor(offset: int) -> str:
    """Mint an opaque page token.

    WHY opaque: the frontend must not be able to read, construct, or depend on a cursor.
    Earlier revision handed out the literal text {"o":50}, which leaked the offset and let
    a client forge its own page. Base64url hides the payload from casual inspection.

    WHY still not a security boundary: a determined client can decode base64. Real
    unforgeability belongs to the store-side cursor in the data leaves (candles, tick WS),
    where the token is an opaque key the store issued. This leaf only guarantees the
    offset is not advertised.
    """
    raw = json.dumps({"o": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _read_cursor(cursor: str) -> int:
    """Decode a page token back to its offset, rejecting anything we did not mint."""
    if not isinstance(cursor, str) or not cursor:
        raise ValueError("cursor must be a non-empty string")
    if len(cursor) > MAX_CURSOR_LEN:
        raise ValueError(f"cursor exceeds {MAX_CURSOR_LEN} characters")
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, ValueError, TypeError) as exc:
        raise ValueError(f"cursor is not a valid page token: {cursor[:24]!r}") from exc
    if not isinstance(payload, dict):
        raise ValueError("cursor is not a valid page token")  # noqa: TRY004
    offset = payload.get("o")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("cursor does not carry a valid offset")
    return offset


def paginate(rows: list[Any], *, limit: Any, cursor: str | None) -> Page:
    """Slice an already-fetched, deterministically ordered list into one page.

    WHY offset-based: a stable ordering is mandatory so a page boundary can neither skip
    nor repeat a row. Earlier revision validated the cursor but then ignored its offset,
    so every page returned the first page — pagination silently never advanced.

    WHY the offset is range-checked: an offset past the end would hand back a silently
    empty page, which a client cannot distinguish from "no more data".
    """
    size = validate_limit(limit)
    offset = 0 if cursor is None else _read_cursor(cursor)
    if offset > len(rows):
        raise ValueError(f"cursor offset {offset} is past the end of {len(rows)} rows")
    window = rows[offset : offset + size]
    has_more = offset + size < len(rows)
    return Page(
        rows=window,
        has_more=has_more,
        next_cursor=_mint_cursor(offset + size) if has_more else None,
    )


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "Page",
    "envelope_error",
    "envelope_success",
    "http_status_for",
    "paginate",
    "validate_limit",
]
