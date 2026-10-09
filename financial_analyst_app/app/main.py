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

import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from modules.experience.api.v1.router import build_account_router
from shared.core.account_mode import AccountGateService, install_account_mode_service
from shared.core.config import AccountsConfig

log = logging.getLogger("quantrader.startup")

# WHY A STARTUP GAP CHECK. The scheduled monitor bounds staleness to its interval, but there
# is a window the schedule cannot cover: a machine that was OFF or ASLEEP has been
# accumulating a hole, and the first thing that runs after it wakes is the application. If
# the app can start against a store that is hours behind the venue, a strategy will happily
# compute on stale candles and nothing will say so. Checking at startup turns that silent
# window into a bounded one.
#
# Design constraints, all of which matter more than the feature:
#   * NON-BLOCKING. It runs as a background task. Startup must not wait on the network -
#     an unreachable venue would otherwise mean an app that never boots.
#   * FAIL-OPEN FOR THE APP, FAIL-CLOSED FOR TRUST. The app always starts. A failure is
#     logged loudly and never allowed to crash the process, because a monitor that takes
#     down the thing it monitors is worse than no monitor.
#   * OPT-IN BY ENV. Tests and CI must not fire venue traffic on every app construction.
async def _startup_gap_check() -> None:
    """Kick the proven monitor runner once, detached, and log the verdict."""
    root = Path(__file__).resolve().parents[1]
    runner = root / "scripts" / "run_data_monitor.ps1"
    if sys.platform.startswith("win") and runner.exists():
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(runner)]
    else:
        # Non-Windows (container/CI): drive the same steps through the supervisor's
        # single-pass mode so there is one code path, not two implementations.
        cmd = [sys.executable or "python", str(root / "scripts" / "run_monitor_loop.py"),
               "--once"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(root),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
    except Exception as exc:  # noqa: BLE001 - never let this break app startup
        log.error("startup gap check did not complete: %s: %s", type(exc).__name__, exc)
        return
    text = (out or b"").decode("utf-8", "replace")
    verdict = next((ln for ln in reversed(text.splitlines()) if "RESULT" in ln), "no verdict")
    if proc.returncode == 0:
        log.info("startup gap check: %s", verdict.strip())
    else:
        log.error("startup gap check exit=%s: %s", proc.returncode, verdict.strip())


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    task: asyncio.Task[None] | None = None
    if os.getenv("QUANTRADER_STARTUP_GAP_CHECK", "1") not in ("0", "false", "False"):
        task = asyncio.create_task(_startup_gap_check())
    # M10.2: the store pool is opened HERE and only here. `market_reader.TimescaleCandleReader`
    # is constructed with it, which is what makes `AGENTS.md` 6a structurally true of the
    # gateway - the handler holds a port, not a connection, so it cannot acquire a store
    # handle by accident and route around the PIT read path.
    #
    # WHY NOT ON IMPORT: a module-scope pool opens a socket at import time, which makes
    # `python -c "import app.main"` fail on any machine without the database, and makes test
    # collection depend on infrastructure.
    pool: Any = None
    if os.getenv("QUANTRADER_MARKET_READER", "1") not in ("0", "false", "False"):
        try:
            import asyncpg

            from modules.experience.api.v1.market_reader import TimescaleCandleReader
            from scripts.verify_universe_data import dsn

            pool = await asyncpg.create_pool(dsn(), timeout=20)
            app.state.pool = pool
            app.state.market_reader = TimescaleCandleReader(
                pool, instruments=_universe()
            )
        except Exception as exc:  # noqa: BLE001 - the app must still boot without market data
            # FAIL-OPEN FOR THE APP. A missing market reader means /market/* answers with a
            # typed error; it must never mean the whole API refuses to start, because that
            # would take down the account surface too over a database that is merely down.
            log.error("market reader unavailable: %s: %s", type(exc).__name__, exc)
            app.state.pool = None
            app.state.market_reader = None
    # M4.9: the approval listing is pure config (registry + params), so it composes
    # even with no pool; the STORE rides the same pool (approvals table lives in the
    # quantrader DB). Both are fail-open for boot / fail-closed for the surface: a
    # composition failure leaves the state unset, and the lazy proxies answer 503.
    try:
        app.state.approval_entries = _compose_approval_entries()
    except Exception as exc:  # noqa: BLE001 - config typo must not refuse app boot
        log.error("approval listing unavailable: %s: %s", type(exc).__name__, exc)
        app.state.approval_entries = None
    if pool is not None:
        entries = getattr(app.state, "approval_entries", None)
        if entries is None:
            # Listing failed to compose: leave the store unwired so writes answer the
            # honest 503 too - an empty known-set would misreport every POST as 404.
            app.state.approval_store = None
        else:
            try:
                from shared.core.strategy_approval_store import StrategyApprovalStore

                # known = display names AND strategy_ids (verify_m4.8 precedent): the
                # registry gate accepts either identity, everything else 404s pre-SQL.
                app.state.approval_store = StrategyApprovalStore(
                    pool,
                    known_strategies={n for n, _, _ in entries}
                    | {sid for _, sid, _ in entries},
                )
            except Exception as exc:  # noqa: BLE001 - the app must still boot
                log.error("approval store unavailable: %s: %s", type(exc).__name__, exc)
                app.state.approval_store = None
    try:
        yield
    finally:
        if pool is not None:
            await pool.close()
        if task is not None and not task.done():
            # Bounded, not cancelled outright: a monitor that is mid-write should be
            # allowed to finish its pass rather than be killed between the bar write and the
            # cursor advance. 30s is a backstop, not the expected wait.
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout=30)


class _LazyMarketReader:
    """Defers to `app.state.market_reader`, which the lifespan creates once the pool is open.

    WHY THIS EXISTS. The router is registered in `create_app`, but the pool is opened in the
    lifespan - so at registration time there is no reader yet. Registering conditionally on
    the lifespan's success was the obvious fix and it is wrong: it makes /market/* 404 when the
    database is down, and a 404 is indistinguishable from a wrong URL. A client cannot tell
    "this endpoint does not exist" from "the store is down", so it cannot decide whether to
    retry. This proxy keeps the route registered and answers a typed, RETRYABLE 503 instead.
    """

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def _reader(self) -> Any:
        reader = getattr(self._app.state, "market_reader", None)
        if reader is None:
            from modules.experience.api.v1.market_reader import MarketReaderUnavailable

            raise MarketReaderUnavailable(
                "market store pool was not available at startup"
            )
        return reader

    async def ping(self) -> None:
        self._reader()

    async def instruments(self) -> list[Any]:
        return await self._reader().instruments()

    async def read(self, **kw: Any) -> list[Any]:
        return await self._reader().read(**kw)


def _compose_approval_entries() -> list[tuple[str, str, str]]:
    """M4.9 approval listing: (display name, strategy_id, version) per registry strategy.

    Pure config - no pool - so it composes even when the database is down; the store
    then fails separately as a typed 503. The derivation (registry + ParameterRegistry)
    is the SAME one `scripts/verify_m4.8_strategy_approval.py` uses, so the API listing
    and the verify script cannot drift into different strategy sets.
    """
    from modules.m4_alpha.strategy.parameter_registry import ParameterRegistry
    from modules.m4_alpha.strategy.strategy_registry import StrategyRegistry

    root = Path(__file__).resolve().parents[1]
    registry = StrategyRegistry(package="modules.m3_analytics.strategies")
    registry.load()
    names = registry.names()
    params = ParameterRegistry(root / "config" / "strategies")
    params.load(names)
    return [(n, params.strategy_id(n), params.version(n)) for n in names]


class _LazyApprovalStore:
    """M4.9 port over `app.state.approval_store` (composed in the lifespan).

    WHY a proxy and not direct wiring: the pool opens in the lifespan, the router
    registers in create_app - same reason as `_LazyMarketReader`. An unwired store
    raises `ApprovalStoreError`, which the router maps to the typed retryable 503
    (fail-closed, spec E2); a route registered only when the store exists would 404
    instead, and 404 is indistinguishable from a wrong URL.
    """

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def _store(self) -> Any:
        store = getattr(self._app.state, "approval_store", None)
        if store is None:
            from shared.core.strategy_approval_store import ApprovalStoreError

            raise ApprovalStoreError("approval store was not available at startup")
        return store

    async def get(self, strategy_id: str, version: str) -> Any:
        return await self._store().get(strategy_id, version)

    async def set_state(
        self,
        strategy_id: str,
        version: str,
        new_state: Any,
        *,
        changed_by: str,
        note: str | None = None,
    ) -> Any:
        return await self._store().set_state(
            strategy_id, version, new_state, changed_by=changed_by, note=note
        )


def _lazy_approval_entries(app: FastAPI) -> Any:
    """The `entries` callable the router guards: unset/failed composition raises,
    and the router turns ANY entries failure into the fail-closed 503 (never a
    partial listing). Reads app.state at call time so tests can preset it."""

    def _entries() -> Any:
        listing = getattr(app.state, "approval_entries", None)
        if listing is None:
            raise RuntimeError("approval listing was not composed at startup")
        return listing

    return _entries


def _universe() -> list[dict[str, Any]]:
    """The canonical universe, read from the one screen that defines it.

    `config/reference/universe_screen.json` is the single selection (the monitor, the loader
    and the repair tool all read it), so the asset selector cannot drift from the instruments
    actually being collected.
    """
    import json

    path = Path(__file__).resolve().parents[1] / "config" / "reference" / "universe_screen.json"
    try:
        selected = json.loads(path.read_text(encoding="utf-8"))["selected"]
    except Exception:  # noqa: BLE001 - a missing screen must not stop the app booting
        return []
    out: list[dict[str, Any]] = []
    for row in selected:
        try:
            from modules.reference.reference import to_canonical

            instrument = to_canonical(row["native"], "deriv")
        except Exception as exc:  # noqa: BLE001 - one bad row must not hide the other 24
            # Logged, not swallowed: a silently dropped instrument is an asset selector that
            # is quietly missing something, which is the same class of invisible defect as the
            # 28.8h staleness this whole response shape exists to prevent.
            log.warning("universe row %r not resolvable: %s", row.get("native"), exc)
            continue
        out.append({"instrument": instrument, "asset_class": row.get("asset_class")})
    return out


def create_app(accounts: AccountsConfig | None = None) -> FastAPI:
    app = FastAPI(title="QUANTRADER2 account gates", version="0.2.0",
                  lifespan=_lifespan)
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
    # M10.2 market read. ALWAYS registered, via a lazy proxy - see `_LazyMarketReader` for why
    # a missing store must degrade to a retryable 503 and never to a 404.
    from modules.experience.api.v1.market_router import build_market_router

    app.include_router(
        build_market_router(_LazyMarketReader(app), now=lambda: datetime.now(UTC)),
        prefix="/api/v1",
    )
    # M4.9 admin approval. ALWAYS registered via lazy proxies (see `_LazyApprovalStore`):
    # unwired => typed retryable 503, never 404.
    from modules.experience.api.v1.approval_router import build_approval_router

    app.include_router(
        build_approval_router(
            _LazyApprovalStore(app), entries=_lazy_approval_entries(app)
        ),
        prefix="/api/v1",
    )
    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
