"""
M10.2 thin API — 2-gate account MODE + AUTO-TRADE (JSON only).
WHY thin: composition root only — routes live in modules/experience/api/v1/router.py
(M10 home per bible §3, extracted 2026-09-25); this file wires CORS + service
singleton + uvicorn entry. Behavior unchanged (spec-m1.3_two_gate LGTM 2026-09-23).
Decoupled from M10.4 frontend: never HTML/static; CORS local origins only.
X-Role on POST: missing/empty/unknown/viewer → 403 (no default-to-trader).
GET requires no switch/enable permission.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from modules.experience.api.v1.router import build_account_router
from shared.core.account_mode import AccountGateService, install_account_mode_service
from shared.core.config import AccountsConfig


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

    app.include_router(build_account_router(svc), prefix="/api/v1")
    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
