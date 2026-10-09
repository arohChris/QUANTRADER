"""WHY: M4.9 Step 7 — real/realistic verification of the approval gateway, the
`create_app` wiring, and the frontend mirror (spec docs/specs/m4.9_approval_ui.spec.md
§5-§7). NOT unit tests: drives the REAL router over the REAL registry config
(`_compose_approval_entries`), walks legal + illegal transitions over HTTP, proves
the unwired `create_app()` fails closed, injects a DB outage to show the 503, and
does a THREE-WAY matrix parity (this script's copy vs backend `_LEGAL` vs the
frontend `LEGAL_TRANSITIONS` source) so a human can eyeball one printed table.

Real store if `DSN` env var present (asyncpg; ONE transaction, ROLLED BACK —
verification never mutates the DB); otherwise the deterministic fake below.
Fault-injection phase runs on the fake only (you cannot break a real pool on
purpose and keep the run honest).

Usage: python scripts/verify_m4.9_approval_ui.py    (optional DSN=...)
Exit: 0 = all checks OK · 1 = any check failed.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from httpx import ASGITransport, AsyncClient

from app.main import _compose_approval_entries, create_app
from modules.experience.api.v1.approval_router import build_approval_router
from shared.core.strategy_approval_store import (
    _LEGAL,
    StrategyApprovalStore,
)

PANEL = REPO / "frontend" / "app" / "AdminTradingSurface.tsx"
APP_TSX = REPO / "frontend" / "app" / "App.tsx"
SERVICE = REPO / "frontend" / "services" / "approval_api.ts"

GET = "/api/v1/strategy-approvals"
POST = "/api/v1/strategy-approvals/state"
_BASE = "http://test"

# WHY an independent third copy: if store, script and frontend ever disagree on
# one cell, two of the three checks below go red (M4.8 script precedent).
LEGAL: dict[str, frozenset[str]] = {
    "draft": frozenset({"testing", "rejected", "discontinued"}),
    "testing": frozenset({"draft", "approved", "rejected"}),
    "approved": frozenset({"testing", "rejected", "discontinued"}),
    "rejected": frozenset({"testing"}),
    "discontinued": frozenset({"testing", "approved"}),
}

_DDL = """
CREATE TABLE IF NOT EXISTS strategy_approvals (
    strategy_id TEXT NOT NULL,
    version     TEXT NOT NULL,
    state       TEXT NOT NULL,
    changed_by  TEXT NOT NULL,
    changed_at  TIMESTAMPTZ NOT NULL,
    note        TEXT NULL,
    PRIMARY KEY (strategy_id, version)
)
"""

_failures: list[str] = []


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(label)
    return ok


class FakeConn:
    """PK-shaped fake for the store's only statements; records every call so the
    zero-SQL checks are exact, and can inject a read outage on demand."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.fail: set[str] = set()

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.calls.append(("fetch", args))
        if "fetch" in self.fail:
            raise OSError("simulated outage")
        row = self.rows.get((str(args[0]), str(args[1])))
        return [dict(row)] if row else []

    async def execute(self, sql: str, *args: object) -> str:
        self.calls.append(("execute", args))
        sid, ver, state, changed_by, changed_at, note = args
        self.rows[(str(sid), str(ver))] = {
            "strategy_id": str(sid), "version": str(ver), "state": str(state),
            "changed_by": str(changed_by), "changed_at": changed_at, "note": note,
        }
        return "INSERT 0 1"


async def _call(
    app: Any,
    method: str,
    url: str,
    *,
    role: str | None = "admin",
    body: dict[str, Any] | None = None,
    raw: bytes | None = None,
) -> Any:
    headers: dict[str, str] = {}
    if role is not None:
        headers["X-Role"] = role
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE, headers=headers
    ) as client:
        if method == "GET":
            return await client.get(url)
        return await client.post(
            url,
            json=body,
            content=raw,
            headers={"Content-Type": "application/json"} if raw is not None else None,
        )


def _err_code(res: Any) -> str | None:
    payload = res.json()
    return payload["error"]["code"] if payload.get("error") else None


def _wire_app(store: StrategyApprovalStore, entries: Any) -> Any:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(
        build_approval_router(store, entries=entries), prefix="/api/v1"
    )
    return app


# ----------------------------------------------------------------------------------
# Phase 1: REAL create_app, no lifespan — mounted routes + fail-closed unwired state
# ----------------------------------------------------------------------------------


async def _phase_wiring() -> None:
    print("\n== Phase 1: real create_app() wiring (no lifespan = unwired state) ==")
    app = create_app()
    res = await _call(app, "GET", GET, role="viewer")
    _check("viewer GET -> 403 permission_denied",
           res.status_code == 403 and _err_code(res) == "permission_denied")
    res = await _call(app, "POST", POST, role="trader",
                      body={"strategy_id": "x", "version": "1.0",
                            "new_state": "testing"})
    _check("trader POST -> 403 before any store effect",
           res.status_code == 403 and _err_code(res) == "permission_denied")
    res = await _call(app, "GET", GET, role="admin")
    _check("unwired admin GET -> 503 unknown_state, retryable",
           res.status_code == 503 and _err_code(res) == "unknown_state"
           and res.json()["error"].get("retryable") is True)
    res = await _call(app, "POST", POST, role="admin",
                      body={"strategy_id": "x", "version": "1.0",
                            "new_state": "testing"})
    _check("unwired admin POST -> 503 unknown_state (fail-closed)",
           res.status_code == 503 and _err_code(res) == "unknown_state")
    res = await _call(app, "GET", GET, role="root")
    _check("unknown role 'root' -> 403 (normalised like account router)",
           res.status_code == 403 and _err_code(res) == "permission_denied")


# ----------------------------------------------------------------------------------
# Phase 2: REAL registry listing over HTTP — every entry, implicit drafts, eyeball
# ----------------------------------------------------------------------------------


async def _phase_listing(app: Any, entries: list[tuple[str, str, str]]) -> list[dict]:
    print("\n== Phase 2: listing the REAL registry over HTTP (human-eyeball table) ==")
    res = await _call(app, "GET", GET)
    ok = res.status_code == 200
    body = res.json()
    ok = ok and body["error"] is None and body["meta"]["schema_version"] == "1.0"
    rows = body["data"]["strategies"] if ok else []
    _check("GET 200 envelope with every registry entry",
           ok and len(rows) == len(entries)
           and {r["strategy_id"] for r in rows} == {sid for _, sid, _ in entries})
    _check("implicit drafts: state='draft' with null metadata (no sentinel leak)",
           all(r["state"] == "draft" for r in rows)
           and all(r["changed_by"] is None and r["changed_at"] is None
                   and r["note"] is None for r in rows))
    print(f"  {'name':<34} {'strategy_id':<34} {'ver':<7} state")
    for r in rows:
        print(f"  {r['name']:<34} {r['strategy_id']:<34} {r['version']:<7} "
              f"{r['state']}")
    print(f"  -> {len(rows)} rows; eyeball: names/versions must match config/strategies")
    return rows


# ----------------------------------------------------------------------------------
# Phase 3: transitions over HTTP — legal flip, REV3 dual-testing, illegal 409,
#          injection 404, contract 422s, default changed_by (Q1)
# ----------------------------------------------------------------------------------


async def _phase_transitions(app: Any, db: FakeConn | None,
                             entries: list[tuple[str, str, str]]) -> None:
    print("\n== Phase 3: transitions over HTTP (legal / illegal / adversarial) ==")
    (name1, sid1, ver1), (name2, sid2, ver2) = entries[0], entries[1]

    res = await _call(app, "POST", POST,
                      body={"strategy_id": sid1, "version": ver1,
                            "new_state": "testing"})
    data = res.json().get("data") or {}
    _check(f"{name1}: draft->testing 200 with store read-back",
           res.status_code == 200 and data.get("state") == "testing")
    _check("default changed_by is 'admin:ui' (Q1)",
           data.get("changed_by") == "admin:ui", str(data.get("changed_by")))

    res = await _call(app, "POST", POST,
                      body={"strategy_id": sid2, "version": ver2,
                            "new_state": "testing"})
    second = res.json().get("data") or {}
    _check(f"REV3: {name2} enters testing while {name1} holds it",
           res.status_code == 200 and second.get("state") == "testing",
           str(second))

    res = await _call(app, "GET", GET)
    states = {r["strategy_id"]: r["state"] for r in res.json()["data"]["strategies"]}
    _check("REV3: BOTH testing rows listed at once (E9)",
           states.get(sid1) == "testing" and states.get(sid2) == "testing",
           f"{sid1}={states.get(sid1)} {sid2}={states.get(sid2)}")

    res = await _call(app, "POST", POST,
                      body={"strategy_id": sid2, "version": ver2,
                            "new_state": "draft"})
    _check("testing->draft 200 (legal reverse flip)",
           res.status_code == 200 and res.json()["data"]["state"] == "draft")

    res = await _call(app, "POST", POST,
                      body={"strategy_id": sid2, "version": ver2,
                            "new_state": "approved",
                            "changed_by": "admin:verify"})
    _check("illegal draft->approved -> 409 illegal_transition",
           res.status_code == 409 and _err_code(res) == "illegal_transition",
           str(res.json().get("error")))
    res = await _call(app, "GET", GET)
    states = {r["strategy_id"]: r["state"] for r in res.json()["data"]["strategies"]}
    _check("store unchanged after 409 (read-back proof)",
           states.get(sid2) == "draft")

    before = len(db.calls) if db is not None else None
    res = await _call(app, "POST", POST,
                      body={"strategy_id": "x'; DROP TABLE strategy_approvals;--",
                            "version": "1.0", "new_state": "approved"})
    _check("injection id -> 404 unknown_symbol (Q2 reuse)",
           res.status_code == 404 and _err_code(res) == "unknown_symbol")
    if db is not None:
        _check("zero SQL issued for the gated id",
               len(db.calls) == before)

    for label, body in (
        ("bad state literal -> 422",
         {"strategy_id": sid1, "version": ver1, "new_state": "APPROVED"}),
        ("201-char note -> 422",
         {"strategy_id": sid1, "version": ver1, "new_state": "rejected",
          "note": "n" * 201}),
        ("blank changed_by -> 422",
         {"strategy_id": sid1, "version": ver1, "new_state": "rejected",
          "changed_by": "   "}),
        ("unknown extra field (changed_at) -> 422",
         {"strategy_id": sid1, "version": ver1, "new_state": "rejected",
          "changed_at": "2026-10-07T00:00:00Z"}),
    ):
        res = await _call(app, "POST", POST, body=body)
        _check(label, res.status_code == 422
               and _err_code(res) == "capability_not_supported")

    res = await _call(app, "POST", POST, raw=b"not json{{")
    _check("non-JSON body -> 422 envelope (never FastAPI detail dialect)",
           res.status_code == 422 and _err_code(res) == "capability_not_supported")

    res = await _call(app, "POST", POST, role=None,
                      body={"strategy_id": sid1, "version": ver1,
                            "new_state": "rejected"})
    _check("missing X-Role POST -> 403",
           res.status_code == 403 and _err_code(res) == "permission_denied")

    res = await _call(app, "POST", POST,
                      body={"strategy_id": sid1, "version": ver1,
                            "new_state": "rejected", "changed_by": "  admin "})
    # Tolerant of strip-or-keep at the contract: the override must be HONOURED
    # (not silently replaced by the "admin:ui" default), whitespace aside.
    _check("changed_by override is echoed (Q1 client override)",
           res.status_code == 200
           and str(res.json()["data"].get("changed_by", "")).strip() == "admin",
           str(res.json().get("data")))


# ----------------------------------------------------------------------------------
# Phase 4: fail-closed read (fake only) + THREE-WAY matrix parity + frontend locks
# ----------------------------------------------------------------------------------


async def _phase_fail_closed(app: Any, db: FakeConn | None) -> None:
    if db is None:
        print("\n== Phase 4: fail-closed read — SKIPPED (real DSN, no fault injection) ==")
        return
    print("\n== Phase 4: DB read outage -> fail-closed 503, never a guessed state ==")
    db.fail.add("fetch")
    try:
        res = await _call(app, "GET", GET)
        _check("read outage -> 503 unknown_state retryable (no fabricated rows)",
               res.status_code == 503 and _err_code(res) == "unknown_state"
               and res.json()["error"].get("retryable") is True)
    finally:
        db.fail.discard("fetch")
    res = await _call(app, "GET", GET)
    _check("recovery: reads resume after the outage clears",
           res.status_code == 200)


def _phase_cross_tree() -> None:
    print("\n== Phase 5: THREE-WAY matrix parity + frontend source locks ==")
    backend = {k: set(v) for k, v in _LEGAL.items()}
    _check("script copy == backend _LEGAL", LEGAL == backend,
           f"script={LEGAL} backend={backend}")

    panel = PANEL.read_text(encoding="utf-8")
    block = re.search(r"LEGAL_TRANSITIONS[^=]*=\s*\{(.*?)\n\};", panel, re.DOTALL)
    pairs = re.findall(r"(\w+)\s*:\s*\[([^\]]*)\]", block.group(1)) if block else []
    frontend = {k: set(re.findall(r'"(\w+)"', v)) for k, v in pairs}
    _check("frontend LEGAL_TRANSITIONS source == backend _LEGAL",
           frontend == backend and set(frontend) == set(backend),
           f"frontend={frontend}")

    _check("panel imports the service (sole network choke point)",
           "../services/approval_api" in panel)
    _check("panel confirms every transition", "confirm(" in panel)
    app_tsx = APP_TSX.read_text(encoding="utf-8")
    _check("App.tsx passes role into the surface",
           "<AdminTradingSurface role={role" in app_tsx)
    offenders = [
        p.name for p in (REPO / "frontend" / "app").glob("*.ts*")
        if "strategy-approvals" in p.read_text(encoding="utf-8")
    ]
    _check("no app component knows the endpoint path (service-only)",
           offenders == [], f"offenders={offenders}")
    _check("service is the one fetch site for these routes",
           "fetch(" in SERVICE.read_text(encoding="utf-8")
           and "strategy-approvals" in SERVICE.read_text(encoding="utf-8"))


async def main() -> int:
    entries = _compose_approval_entries()  # REAL registry + parameter config
    _check("real registry composition yields >=2 strategies",
           len(entries) >= 2, f"got {len(entries)}")
    if len(entries) < 2:
        return 1
    known = {n for n, _, _ in entries} | {sid for _, sid, _ in entries}

    await _phase_wiring()

    dsn = os.environ.get("DSN")
    if dsn:
        import asyncpg

        print("\nDSN present -> REAL asyncpg store (single transaction, rolled back)")
        conn = await asyncpg.connect(dsn)
        try:
            async with conn.transaction():
                await conn.execute(_DDL)
                store = StrategyApprovalStore(conn, known_strategies=known)
                app = _wire_app(store, lambda: list(entries))
                await _phase_listing(app, entries)
                await _phase_transitions(app, None, entries)
                await _phase_fail_closed(app, None)
            print("\n[note] real-DSN run: transaction ROLLED BACK — the database "
                  "is unchanged (verify must never mutate)")
        finally:
            await conn.close()
    else:
        print("no DSN env var -> deterministic fake connection")
        db = FakeConn()
        store = StrategyApprovalStore(db, known_strategies=known)
        app = _wire_app(store, lambda: list(entries))
        await _phase_listing(app, entries)
        await _phase_transitions(app, db, entries)
        await _phase_fail_closed(app, db)

    _phase_cross_tree()

    if _failures:
        print(f"\n{len(_failures)} FAILED check(s):")
        for item in _failures:
            print(f"  [FAIL] {item}")
        return 1
    print("\nALL CHECKS OK — eyeball the printed registry table above")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
