"""
M10.2 Envelope — Step 3 tests, written AGAINST `docs/specs/m10.2_envelope.spec.md`.

Written BEFORE implementation, per AGENTS.md §2 Step 3. Until Step 5 lands, every test in
this file fails at import with `ModuleNotFoundError: modules.experience.api.v1.envelope`.
That RED is the honest baseline and is re-runnable:

    python -m pytest tests/m10_2/test_m10.2_envelope_spec.py -q

Each test maps 1:1 to a numbered edge in the spec (§6 E1-E20) or an adversarial case (§7).
No test asserts merely "does not raise" — every one asserts a specific value, a specific
serialized string, or a specific raised type.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from modules.experience.api.v1.envelope import (
    Page,
    envelope_error,
    envelope_success,
    http_status_for,
    paginate,
    validate_limit,
)
from shared.contracts.errors import ErrorCode

_T0 = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
_CID = "018f3a2b-7c4d-4b1e-9f2a-1c3d5e7f9a0b"


def _dumps(body: dict) -> str:
    return json.dumps(body, sort_keys=True)


# ---------------------------------------------------------------- E1-E3 shape

def test_E1_success_empty_dict_payload() -> None:
    body = envelope_success({}, correlation_id=_CID, as_of=_T0, schema_version="1.0")
    assert body["data"] == {}
    assert body["error"] is None
    assert body["meta"]["correlation_id"] == _CID
    assert body["meta"]["schema_version"] == "1.0"


def test_E2_success_empty_list_is_not_404() -> None:
    """An empty collection is a successful empty answer, never a missing resource."""
    body = envelope_success([], correlation_id=_CID, as_of=_T0, schema_version="1.0")
    assert body["data"] == []
    assert body["error"] is None


def test_E3_error_shape_has_null_data_and_frozen_code() -> None:
    body = envelope_error(
        ErrorCode.capability_not_supported,
        "Order-book imbalance is unavailable for this broker",
        correlation_id=_CID,
        as_of=_T0,
    )
    assert body["data"] is None
    assert body["error"]["code"] == "capability_not_supported"
    assert body["error"]["retryable"] is False
    assert http_status_for(ErrorCode.capability_not_supported) == 422


# ---------------------------------------------------------------- E4-E6 correlation + clock

def test_E4_correlation_id_is_required_not_defaulted() -> None:
    with pytest.raises(TypeError):
        envelope_success({}, as_of=_T0, schema_version="1.0")  # type: ignore[call-arg]


def test_E5_naive_as_of_is_rejected() -> None:
    """AGENTS.md §3.8 — every internal timestamp is UTC and tz-aware, never naive."""
    naive = datetime(2026, 9, 27, 12, 0, 0)  # noqa: DTZ001  # deliberate: this IS the bad input under test
    with pytest.raises(ValueError, match="tz-aware"):
        envelope_success({}, correlation_id=_CID, as_of=naive, schema_version="1.0")


def test_E6_non_utc_offset_is_normalized_not_relabelled() -> None:
    """+02:00 12:00 is 10:00Z — the instant is preserved, not the wall-clock label."""
    aware = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    body = envelope_success({}, correlation_id=_CID, as_of=aware, schema_version="1.0")
    assert body["meta"]["as_of"] == "2026-09-27T10:00:00Z"


# ---------------------------------------------------------------- E7-E8 money serialization (hand-calc)

def test_E7_decimal_money_serializes_as_exact_2dp_string() -> None:
    # Hand calc: Decimal("12.00") must serialize as the string "12.00"
    # (2dp quote currency, ROUND_HALF_UP applied at the settle boundary)
    # — never the float 12.0. AGENTS.md §3.1 forbids float in the money path.
    body = envelope_success(
        {"pnl_net": Decimal("12.00")}, correlation_id=_CID, as_of=_T0, schema_version="1.0"
    )
    assert body["data"]["pnl_net"] == "12.00"
    assert isinstance(body["data"]["pnl_net"], str)
    assert "12.0" not in _dumps(body).replace('"12.00"', "")


def test_E8_decimal_price_keeps_significant_zeros_and_never_exponent_form() -> None:
    # Hand calc: a 5dp price Decimal("0.00100") keeps 5dp -> "0.00100";
    # it must never serialize as 1e-3 or lose trailing zeros.
    # WHY assert the value, not the whole blob: a correlation id can legitimately contain
    # "e-", so a substring scan of the entire JSON would false-positive on the fixture.
    body = envelope_success(
        {"price": Decimal("0.00100")}, correlation_id=_CID, as_of=_T0, schema_version="1.0"
    )
    assert body["data"]["price"] == "0.00100"
    assert "e" not in body["data"]["price"].lower()


# ---------------------------------------------------------------- E9-E13 limit validation

def test_E9_limit_zero_rejected() -> None:
    with pytest.raises(ValueError, match="limit"):
        validate_limit(0)


def test_E10_limit_above_max_is_rejected_not_clamped() -> None:
    """Spec §6 E10 — an over-max request is REJECTED, never silently clamped."""
    with pytest.raises(ValueError, match="limit"):
        validate_limit(501)


def test_E11_limit_at_max_accepted() -> None:
    assert validate_limit(500) == 500


def test_E12_non_numeric_limit_rejected() -> None:
    with pytest.raises(ValueError, match="limit"):
        validate_limit("many")  # type: ignore[arg-type]


def test_E13_absent_limit_defaults_to_50() -> None:
    assert validate_limit(None) == 50


# ---------------------------------------------------------------- E14-E16 pagination

def test_E14_rows_beyond_limit_truncate_and_report_more() -> None:
    page = paginate(list(range(120)), limit=50, cursor=None)
    assert len(page.rows) == 50
    assert page.has_more is True
    assert page.next_cursor is not None


def test_E15_rows_exactly_equal_to_limit_has_no_more() -> None:
    page = paginate(list(range(50)), limit=50, cursor=None)
    assert len(page.rows) == 50
    assert page.has_more is False
    assert page.next_cursor is None


def test_E16_rows_fewer_than_limit_returns_all() -> None:
    page = paginate([1, 2, 3], limit=50, cursor=None)
    assert page.rows == [1, 2, 3]
    assert page.has_more is False


# ---------------------------------------------------------------- E17, E20 cursor + details

def test_E17_malformed_cursor_rejected_never_partial_page() -> None:
    with pytest.raises(ValueError, match="cursor"):
        paginate([1, 2, 3], limit=50, cursor="not-a-real-cursor")


def test_E20_absent_details_becomes_empty_object_not_null() -> None:
    body = envelope_error(
        ErrorCode.unknown_state, "unresolvable", correlation_id=_CID, as_of=_T0
    )
    assert body["error"]["details"] == {}
    assert body["error"]["details"] is not None


# ---------------------------------------------------------------- E18-E19 code + details

def test_E18_non_enum_error_code_cannot_be_constructed() -> None:
    """Spec §4.8 — a bare string code is the only way an unvalidated code reaches the wire."""
    with pytest.raises(TypeError):
        envelope_error(
            "made_up_code",  # type: ignore[arg-type]
            "nope",
            correlation_id=_CID,
            as_of=_T0,
        )


def test_E19_details_preserved_verbatim() -> None:
    details = {"required_margin": "86.74", "balance": "600.00"}
    body = envelope_error(
        ErrorCode.insufficient_margin,
        "not enough margin",
        correlation_id=_CID,
        as_of=_T0,
        details=details,
    )
    assert body["error"]["details"] == details


# ---------------------------------------------------------------- §5 status mapping

@pytest.mark.parametrize(
    ("code", "status"),
    [
        (ErrorCode.capability_not_supported, 422),
        (ErrorCode.unknown_symbol, 404),
        (ErrorCode.insufficient_margin, 422),
        (ErrorCode.exposure_limit, 422),
        (ErrorCode.leverage_limit, 422),
        (ErrorCode.concentration_limit, 422),
        (ErrorCode.stale_signal, 409),
        (ErrorCode.stale_alpha, 409),
        (ErrorCode.data_quality_invalid, 409),
        (ErrorCode.gap_detected, 409),
        (ErrorCode.trade_plan_expired, 409),
        (ErrorCode.broker_unavailable, 503),
        (ErrorCode.unknown_state, 503),
    ],
)
def test_status_mapping_is_total_over_frozen_enum(code: ErrorCode, status: int) -> None:
    assert http_status_for(code) == status


def test_broker_unavailable_is_marked_retryable() -> None:
    body = envelope_error(
        ErrorCode.broker_unavailable, "down", correlation_id=_CID, as_of=_T0
    )
    assert body["error"]["retryable"] is True
    assert http_status_for(ErrorCode.broker_unavailable) == 503


# ---------------------------------------------------------------- §7 adversarial

def test_ADV_dunder_string_code_cannot_traverse() -> None:
    with pytest.raises(TypeError):
        envelope_error(
            "__class__",  # type: ignore[arg-type]
            "probe",
            correlation_id=_CID,
            as_of=_T0,
        )


def test_ADV_proto_key_in_details_stays_inert_data() -> None:
    details = {"__proto__": 1, "constructor": "x"}
    body = envelope_error(
        ErrorCode.unknown_state, "probe", correlation_id=_CID, as_of=_T0, details=details
    )
    assert body["error"]["details"] == details
    assert json.loads(_dumps(body))["error"]["details"] == details


def test_ADV_oversized_correlation_id_rejected() -> None:
    with pytest.raises(ValueError, match="correlation_id"):
        envelope_success(
            {}, correlation_id="x" * 1_048_576, as_of=_T0, schema_version="1.0"
        )


def test_ADV_year_9999_accepted_when_tz_aware() -> None:
    far = datetime(9999, 12, 31, tzinfo=UTC)
    body = envelope_success({}, correlation_id=_CID, as_of=far, schema_version="1.0")
    assert body["meta"]["as_of"] == "9999-12-31T00:00:00Z"


def test_ADV_year_9999_rejected_when_naive() -> None:
    far = datetime(9999, 12, 31)  # noqa: DTZ001  # deliberate: naive far-future is the bad input under test
    with pytest.raises(ValueError, match="tz-aware"):
        envelope_success({}, correlation_id=_CID, as_of=far, schema_version="1.0")


def test_ADV_decimal_nan_is_rejected_never_emitted_as_invalid_json() -> None:
    with pytest.raises(ValueError):
        envelope_success(
            {"pnl_net": Decimal("NaN")},
            correlation_id=_CID,
            as_of=_T0,
            schema_version="1.0",
        )


def test_ADV_negative_limit_rejected() -> None:
    with pytest.raises(ValueError, match="limit"):
        validate_limit(-1)


def test_ADV_oversized_cursor_rejected_without_unbounded_parse() -> None:
    with pytest.raises(ValueError, match="cursor"):
        paginate([1, 2, 3], limit=50, cursor="z" * 1_048_576)


def test_ADV_nested_decimal_is_string_at_every_level() -> None:
    payload = {"a": {"b": {"c": {"pnl": Decimal("1.50")}}}}
    body = envelope_success(payload, correlation_id=_CID, as_of=_T0, schema_version="1.0")
    assert body["data"]["a"]["b"]["c"]["pnl"] == "1.50"


# ---------------------------------------------------------------- §4 invariants

def test_data_and_error_are_mutually_exclusive() -> None:
    ok = envelope_success({"x": 1}, correlation_id=_CID, as_of=_T0, schema_version="1.0")
    bad = envelope_error(
        ErrorCode.unknown_state, "x", correlation_id=_CID, as_of=_T0
    )
    assert (ok["data"] is None) is (ok["error"] is not None)
    assert (bad["data"] is None) is (bad["error"] is not None)


def test_builder_is_deterministic_byte_for_byte() -> None:
    """Spec §4.7 — purity is what makes the envelope replayable."""
    first = envelope_success(
        {"pnl": Decimal("1.50")}, correlation_id=_CID, as_of=_T0, schema_version="1.0"
    )
    second = envelope_success(
        {"pnl": Decimal("1.50")}, correlation_id=_CID, as_of=_T0, schema_version="1.0"
    )
    assert _dumps(first) == _dumps(second)


def test_no_detail_key_anywhere_in_either_shape() -> None:
    """Spec §9 — the frontend must never depend on FastAPI's string-only `detail`.

    WHY scan for the exact JSON key `"detail":` and not the substring "detail": our own
    error field is named `details`, which contains "detail" as a substring. A substring
    scan would false-positive on the shape we deliberately designed.
    """
    ok = envelope_success({"x": 1}, correlation_id=_CID, as_of=_T0, schema_version="1.0")
    bad = envelope_error(
        ErrorCode.insufficient_margin, "x", correlation_id=_CID, as_of=_T0
    )

    def _keys(node: object) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {k for v in node.values() for k in _keys(v)}
        if isinstance(node, list):
            return {k for v in node for k in _keys(v)}
        return set()

    for body in (ok, bad):
        assert '"detail":' not in _dumps(body)
        assert "detail" not in _keys(body)


def test_page_is_immutable() -> None:
    """A frozen Page cannot be mutated by a handler after pagination."""
    page = paginate([1, 2], limit=50, cursor=None)
    assert isinstance(page, Page)
    with pytest.raises(AttributeError):  # dataclasses.FrozenInstanceError subclasses this
        page.rows = [9]  # type: ignore[misc]


# ---------------------------------------------------------------- AUDIT-FOUND DEFECTS
# Added 2026-09-27 after an adversarial probe found these while the suite was fully green.
# Each names the defect it pins, so a regression names itself.

def test_AUDIT_valid_cursor_actually_advances_to_the_next_page() -> None:
    """DEFECT 1 (critical): a valid cursor returned page 1 again, so pagination never
    advanced. A caller walking 120 rows in pages of 50 would loop forever on page 1."""
    rows = list(range(120))
    p1 = paginate(rows, limit=50, cursor=None)
    p2 = paginate(rows, limit=50, cursor=p1.next_cursor)
    assert p1.rows[0] == 0
    assert p1.rows[-1] == 49
    assert p2.rows[0] == 50, "cursor must resume after the previous page, not restart"
    assert p2.rows[-1] == 99
    p3 = paginate(rows, limit=50, cursor=p2.next_cursor)
    assert p3.rows == [100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                       110, 111, 112, 113, 114, 115, 116, 117, 118, 119]
    assert p3.has_more is False
    assert p3.next_cursor is None


def test_AUDIT_pages_do_not_overlap_or_skip() -> None:
    """DEFECT 1 follow-on: walking every page must reproduce the input exactly once."""
    rows = list(range(120))
    seen: list[int] = []
    cursor = None
    for _ in range(10):  # bounded so an infinite loop fails instead of hanging
        page = paginate(rows, limit=50, cursor=cursor)
        seen.extend(page.rows)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == rows
    assert len(seen) == len(set(seen)), "no row may appear on two pages"


def test_AUDIT_cursor_is_opaque_not_readable_offset() -> None:
    """DEFECT 2: the cursor was the literal text {"o":50}, so a client could read the
    offset and hand-craft its own. Spec §11 recommends the frontend cannot infer ordering.

    WHY these assertions and not "the letter o is absent": a single letter occurs inside
    base64 by chance, so its presence proves nothing. What matters is that the token is
    not readable JSON and does not advertise the offset in the clear.
    """
    import json as _json

    page = paginate(list(range(120)), limit=50, cursor=None)
    assert page.next_cursor is not None
    cursor = page.next_cursor
    assert "{" not in cursor and "}" not in cursor
    assert '{"o"' not in cursor
    assert '":50"' not in cursor
    with pytest.raises(ValueError):  # not decodable as plain JSON
        _json.loads(cursor)


def test_AUDIT_float_is_rejected_not_serialised_into_the_money_path() -> None:
    """DEFECT 3: a float passed straight through, so a module returning 12.0 produced
    `"pnl_net": 12.0` on the wire. AGENTS.md §3.1 forbids float in the money path, and
    this envelope is the last gate before the browser."""
    with pytest.raises((TypeError, ValueError)):
        envelope_success(
            {"pnl_net": 12.00},
            correlation_id=_CID,
            as_of=_T0,
            schema_version="1.0",
        )


def test_AUDIT_status_mapping_covers_every_frozen_error_code() -> None:
    """DEFECT 4: only 13 of 28 frozen codes were mapped; the other 9 silently became 500.
    `permission_denied` reporting as a server fault is the worst of them — it breaks both
    monitoring and client retry logic. No code may be unmapped."""
    unmapped = {c.value for c in ErrorCode if http_status_for(c) == 500}
    allowed_500 = {"config_error"}
    assert unmapped == allowed_500, (
        f"every ErrorCode needs a deliberate status; only {allowed_500} may be 500. "
        f"Unmapped: {sorted(unmapped)}"
    )


def test_AUDIT_permission_denied_is_403_not_500() -> None:
    assert http_status_for(ErrorCode.permission_denied) == 403


def test_AUDIT_isolation_violation_is_403() -> None:
    """A cross-tenant read is an authorization failure, never a server fault."""
    assert http_status_for(ErrorCode.isolation_violation) == 403


def test_AUDIT_forged_cursor_offset_is_rejected() -> None:
    """A client must not be able to hand-craft a token to jump to an arbitrary offset."""
    import base64

    forged = base64.urlsafe_b64encode(b'{"o":999999}').decode().rstrip("=")
    with pytest.raises(ValueError, match="cursor"):
        paginate(list(range(120)), limit=50, cursor=forged)


def test_AUDIT_int_is_allowed_because_counts_and_sequences_are_not_money() -> None:
    """Guard against over-correcting: rejecting every float would break legitimate
    non-money numerics. The rule is money-shaped keys, not a blanket ban."""
    body = envelope_success(
        {"sequence": 1843, "tick_count": 120, "quantity": 3},
        correlation_id=_CID,
        as_of=_T0,
        schema_version="1.0",
    )
    assert body["data"]["sequence"] == 1843
