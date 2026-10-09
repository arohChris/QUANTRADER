"""M4.9 - the admin approval control loop: two thin routes.

    GET  /api/v1/strategy-approvals        list every registry strategy + effective state
    POST /api/v1/strategy-approvals/state  one legal transition, store read-back proof

WHY here: bible 3 puts every M10 HTTP surface under `modules/experience/api/v1/`;
`app/main.py` stays the composition root. This is the read/write face of the frozen
m4.8 store for the Admin workspace - nothing more.

THIN BY LAW (`market_router.py` precedent, restated for this leaf): no arithmetic,
no state derivation, no invented error codes. The store's typed errors ARE the error
map, and every reply is the frozen envelope so the frontend branches on `error.code`
only. `X-Role` is normalised exactly like `build_account_router._require_role`
(strip + lower) - it remains the documented demo-auth path until OIDC (main.py).

Spec: docs/specs/m4.9_approval_ui.spec.md (LGTM @you 2026-10-07, verbatim
"just do a pro job here"; Q1 = changed_by default "admin:ui", Q2 = reuse
`unknown_symbol` for the registry-gate 404, both recorded in the spec).
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from modules.experience.api.v1.envelope import (
    envelope_error,
    envelope_success,
    http_status_for,
)
from shared.contracts.errors import ErrorCode
from shared.contracts.strategy_approval import StrategyApproval, StrategyState
from shared.core.permissions import can
from shared.core.strategy_approval_store import (
    ApprovalStoreError,
    ApprovalTransitionError,
    UnknownStrategyApprovalError,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
#: Q1 (spec 9): the demo-auth era has no real principal to attribute to. A default
#: keeps the audit field honest (it says where the write came from: the UI) instead
#: of forcing the client to invent a name. OIDC replaces this with the real subject.
DEFAULT_CHANGED_BY = "admin:ui"

StrategyEntry = tuple[str, str, str]  # (display name, strategy_id, version)


class ApprovalStorePort(Protocol):
    """Structural seam (market_router CandleReader pattern): the real
    `StrategyApprovalStore` satisfies it; tests inject the store over a fake
    connection. Declaring the port here is what keeps this module from growing a
    direct SQL dependency later."""

    async def get(
        self, strategy_id: str, version: str
    ) -> StrategyApproval | None: ...

    async def set_state(
        self,
        strategy_id: str,
        version: str,
        new_state: StrategyState,
        *,
        changed_by: str,
        note: str | None = None,
    ) -> StrategyApproval: ...


class StateBody(BaseModel):
    """POST body. `extra="forbid"` so a client-supplied `changed_at` (or anything
    else undeclared) is REJECTED, never silently dropped - the store is the only
    clock (spec 6). Validation failures never reach the store: zero SQL."""

    model_config = ConfigDict(extra="forbid")

    strategy_id: str
    version: str
    new_state: StrategyState
    note: str | None = Field(default=None, max_length=200)
    changed_by: str | None = None

    @field_validator("changed_by")
    @classmethod
    def _non_blank(cls, value: str | None) -> str | None:
        # Whitespace-only handles die HERE, before any SQL (spec E7); the frozen
        # contract would also reject them - belt and braces at the boundary.
        if value is not None and not value.strip():
            raise ValueError("changed_by must be non-empty after strip")
        return value


def _authorised(x_role: str | None) -> bool:
    # Exact account-router normalisation: unknown/empty/None -> no permission.
    return can((x_role or "").strip().lower(), "strategy.approve")


def _cid(x_correlation_id: str | None) -> str:
    # A valid client id is echoed (trace continuity); a malformed or oversized one
    # falls back to a minted id so a bad header can never 500 the envelope
    # (envelope._check_cid raises on empty/over-long).
    value = (x_correlation_id or "").strip()
    return value if 0 < len(value) <= 128 else uuid.uuid4().hex


def _err(
    code: ErrorCode, message: str, *, cid: str, as_of: datetime
) -> JSONResponse:
    """A typed failure, as the envelope, with the status the frozen code maps to -
    never a hand-picked status (market_router `_err` law)."""
    return JSONResponse(
        status_code=http_status_for(code),
        content=envelope_error(
            code=code, message=message, correlation_id=cid, as_of=as_of
        ),
    )


def _ok(data: Any, *, cid: str, as_of: datetime) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content=envelope_success(
            data,
            correlation_id=cid,
            as_of=as_of,
            schema_version=SCHEMA_VERSION,
        ),
    )


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "invalid request body"
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ()))
    return f"{loc}: {first.get('msg', 'invalid')}" if loc else str(
        first.get("msg", "invalid")
    )


def build_approval_router(
    store: ApprovalStorePort,
    *,
    entries: Callable[[], Sequence[StrategyEntry]],
) -> APIRouter:
    """Routes bound to THIS app's store instance (create_app owns the wiring).

    `entries` is the composition root's registry view (registry + ParameterRegistry).
    It is a callable so a failure to compose the listing fails CLOSED as a 503
    (the UI shows nothing new) instead of serving half a truth.
    """
    router = APIRouter(prefix="/strategy-approvals", tags=["approvals"])

    @router.get("")
    async def list_approvals(
        x_role: str | None = Header(default=None),
        x_correlation_id: str | None = Header(default=None),
    ) -> JSONResponse:
        cid = _cid(x_correlation_id)
        as_of = datetime.now(UTC)
        if not _authorised(x_role):
            return _err(
                ErrorCode.permission_denied,
                "role cannot strategy.approve",
                cid=cid,
                as_of=as_of,
            )
        try:
            listing = list(entries())
        except RuntimeError as exc:  # composition failure = fail-closed display
            # Narrow on purpose (market_router posture): the lazy proxy fails with
            # RuntimeError exactly; anything else re-raises to a FastAPI 500 per the
            # spec error map's "anything else" row rather than wearing a 503 face.
            logger.error("approval listing unavailable: %s", exc)
            return _err(
                ErrorCode.unknown_state,
                "approval listing unavailable",
                cid=cid,
                as_of=as_of,
            )
        strategies: list[dict[str, Any]] = []
        for name, strategy_id, version in listing:
            try:
                row = await store.get(strategy_id, version)
            except ApprovalStoreError as exc:
                # Fail-closed (3.3): an unreadable store serves NO states.
                logger.error(
                    "approval store unreadable strategy=%s version=%s err=%s",
                    strategy_id, version, exc,
                )
                return _err(
                    ErrorCode.unknown_state,
                    "approval store unavailable",
                    cid=cid,
                    as_of=as_of,
                )
            strategies.append(
                {
                    "name": name,
                    "strategy_id": strategy_id,
                    "version": version,
                    # Absent row = implicit draft (m4.8): metadata is null in the API
                    # output - the store's display-only sentinel never leaks (E3).
                    "state": row.state if row else "draft",
                    "changed_by": row.changed_by if row else None,
                    "changed_at": row.changed_at if row else None,
                    "note": row.note if row else None,
                }
            )
        return _ok({"strategies": strategies}, cid=cid, as_of=as_of)

    @router.post("/state")
    async def set_state(
        request: Request,
        x_role: str | None = Header(default=None),
        x_correlation_id: str | None = Header(default=None),
    ) -> JSONResponse:
        cid = _cid(x_correlation_id)
        as_of = datetime.now(UTC)
        if not _authorised(x_role):
            return _err(
                ErrorCode.permission_denied,
                "role cannot strategy.approve",
                cid=cid,
                as_of=as_of,
            )
        # The handler owns body parsing end to end so EVERY reply is the envelope -
        # FastAPI's own `detail` dialect would give the frontend a second error shape.
        try:
            payload = await request.json()
        except ValueError:
            # json.JSONDecodeError and UnicodeDecodeError both derive from ValueError;
            # everything else re-raises (spec error map: "anything else" -> 500).
            return _err(
                ErrorCode.capability_not_supported,
                "body is not valid JSON",
                cid=cid,
                as_of=as_of,
            )
        try:
            body = StateBody.model_validate(payload)
        except ValidationError as exc:
            return _err(
                ErrorCode.capability_not_supported,
                _validation_message(exc),
                cid=cid,
                as_of=as_of,
            )
        try:
            approval = await store.set_state(
                body.strategy_id,
                body.version,
                body.new_state,
                changed_by=body.changed_by or DEFAULT_CHANGED_BY,
                note=body.note,
            )
        except UnknownStrategyApprovalError as exc:
            # Spec Q2: reuse the frozen 404 (no enum expansion for one endpoint).
            return _err(ErrorCode.unknown_symbol, str(exc), cid=cid, as_of=as_of)
        except ApprovalTransitionError as exc:
            return _err(ErrorCode.illegal_transition, str(exc), cid=cid, as_of=as_of)
        except ValidationError as exc:
            # The frozen contract validating inside set_state (blank handle etc.).
            return _err(
                ErrorCode.capability_not_supported,
                _validation_message(exc),
                cid=cid,
                as_of=as_of,
            )
        except ApprovalStoreError as exc:
            # Unwired / DB failure: state undeterminable => fail-closed 503 (E2).
            logger.error("approval write failed strategy=%s err=%s",
                         body.strategy_id, exc)
            return _err(ErrorCode.unknown_state, str(exc), cid=cid, as_of=as_of)
        return _ok(approval.model_dump(), cid=cid, as_of=as_of)

    return router
