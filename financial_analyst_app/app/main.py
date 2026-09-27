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
        # WHY localhost-only and no "null" (corrected 2026-09-27): the "null" origin existed
        # solely for the file:// vanilla Day-1 panel, which has been deleted. Leaving it
        # admitted requests from ANY file:// page, which is a real origin-spoofing surface.
        allow_origins=[
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ],
        allow_methods=["GET", "POST"],
        # WHY Authorization: the OIDC path sends a bearer token, and X-Correlation-Id /
        # Idempotency-Key are mandated by the API contract. Without these in the allow-list
        # the preflight fails and the whole gateway is unusable in a browser.
        # X-Role is STILL LISTED because it remains the only working auth path until OIDC
        # lands - removing it here would break the running demo. It is a client-supplied
        # header and is not authentication; it must be dropped from this list in the same
        # change that enables OIDC validation.
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-Correlation-Id",
            "Idempotency-Key",
            "X-Role",
        ],
        # WHY credentials on: required for any cookie/session-based auth. Safe here ONLY
        # because allow_origins is an explicit localhost list, never "*" — with wildcard
        # origins and credentials the browser rejects the response anyway.
        allow_credentials=True,
    )

    app.include_router(build_account_router(svc), prefix="/api/v1")
    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
