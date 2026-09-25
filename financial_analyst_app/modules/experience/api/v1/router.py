"""M10.2 account-gate API v1 — thin routes: validate -> service -> JSON.

WHY here (not app/): bible §3 puts every M10 HTTP surface under `modules/experience/api/v1/`;
`app/main.py` is only the composition root (CORS + singleton wiring + uvicorn entry).
Spec: docs/specs/m1.3_account_mode_two_gate.spec.md (LGTM 2026-09-23) — behavior UNCHANGED,
extracted 2026-09-25 per human directive (group M10 backend logic for clean deferral).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from shared.core.account_mode import (
    AccountGateService,
    InvalidAccountModeError,
    InvalidAutoTradeError,
    LiveNotArmedError,
)
from shared.core.permissions import can

ModeIn = Literal["demo", "live"]
AutoIn = Literal["OFF", "ON"]


class ModeBody(BaseModel):
    mode: ModeIn = Field(..., description="demo or live")


class AutoBody(BaseModel):
    auto: AutoIn = Field(..., description="OFF or ON")


def _require_role(x_role: str | None, action: str) -> None:
    # Spec: missing/empty/unknown/viewer -> 403. Header default "" not "trader".
    role = (x_role or "").strip().lower()
    if not can(role, action):
        raise HTTPException(status_code=403, detail=f"role cannot {action}")


def _gate_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, LiveNotArmedError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (InvalidAccountModeError, InvalidAutoTradeError)):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, ValueError):
        # Defensive — public contract is typed errors above.
        return HTTPException(status_code=422, detail=str(exc))
    raise exc


def build_account_router(svc: AccountGateService) -> APIRouter:
    """Account-gate routes bound to THIS app's service instance (create_app owns the singleton)."""
    router = APIRouter(prefix="/account", tags=["account"])

    @router.get("/status")
    def get_status() -> dict[str, object]:
        return svc.snapshot()

    @router.get("/mode")
    def get_mode() -> dict[str, object]:
        # Spec allows status or mode path; both return full gate snapshot.
        return svc.snapshot()

    @router.post("/mode")
    def post_mode(
        body: ModeBody,
        x_role: str = Header(default="", alias="X-Role"),
    ) -> dict[str, object]:
        _require_role(x_role, "account.switch")
        try:
            svc.set_mode(body.mode)
        except (LiveNotArmedError, InvalidAccountModeError, InvalidAutoTradeError) as exc:
            raise _gate_errors(exc) from exc
        return svc.snapshot()

    @router.post("/auto-trade")
    def post_auto(
        body: AutoBody,
        x_role: str = Header(default="", alias="X-Role"),
    ) -> dict[str, object]:
        _require_role(x_role, "trading.enable")
        try:
            svc.set_auto(body.auto)
        except (LiveNotArmedError, InvalidAutoTradeError) as exc:
            raise _gate_errors(exc) from exc
        return svc.snapshot()

    return router
