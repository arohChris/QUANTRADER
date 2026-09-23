"""
M10.2 thin API — 2-gate account MODE + AUTO-TRADE (JSON only).
WHY thin: no business logic in routers — validate → AccountGateService → JSON.
Spec: docs/specs/m1.3_account_mode_two_gate.spec.md
Decoupled from M10.4 frontend: never HTML/static; CORS local origins only.
X-Role on POST: missing/empty/unknown/viewer → 403 (no default-to-trader).
GET requires no switch/enable permission.
"""
from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from shared.core.account_mode import (
    AccountGateService,
    InvalidAccountModeError,
    InvalidAutoTradeError,
    LiveNotArmedError,
    install_account_mode_service,
)
from shared.core.config import AccountsConfig
from shared.core.permissions import can

ModeIn = Literal["demo", "live"]
AutoIn = Literal["OFF", "ON"]


class ModeBody(BaseModel):
    mode: ModeIn = Field(..., description="demo or live")


class AutoBody(BaseModel):
    auto: AutoIn = Field(..., description="OFF or ON")


def _require_role(x_role: str | None, action: str) -> None:
    # Spec: missing/empty/unknown/viewer → 403. Header default "" not "trader".
    role = (x_role or "").strip().lower()
    if not can(role, action):
        raise HTTPException(status_code=403, detail=f"role cannot {action}")


def create_app(accounts: AccountsConfig | None = None) -> FastAPI:
    app = FastAPI(title="QUANTRADER2 account gates", version="0.2.0")
    # WHY install: each create_app owns the gates this API mutates; bot reads same singleton.
    svc = install_account_mode_service(AccountGateService(accounts=accounts))
    app.state.account_mode = svc
    app.state.account_gate = svc

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8000",
            "http://localhost:8000",
            "null",  # file:// pages send Origin: null
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Role"],
    )

    def _gate_errors(exc: Exception) -> HTTPException:
        if isinstance(exc, LiveNotArmedError):
            return HTTPException(status_code=409, detail=str(exc))
        if isinstance(exc, (InvalidAccountModeError, InvalidAutoTradeError)):
            return HTTPException(status_code=422, detail=str(exc))
        if isinstance(exc, ValueError):
            # Defensive — public contract is typed errors above.
            return HTTPException(status_code=422, detail=str(exc))
        raise exc

    @app.get("/api/v1/account/status")
    def get_status() -> dict[str, object]:
        return svc.snapshot()

    @app.get("/api/v1/account/mode")
    def get_mode() -> dict[str, object]:
        # Spec allows status or mode path; both return full gate snapshot.
        return svc.snapshot()

    @app.post("/api/v1/account/mode")
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

    @app.post("/api/v1/account/auto-trade")
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

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
