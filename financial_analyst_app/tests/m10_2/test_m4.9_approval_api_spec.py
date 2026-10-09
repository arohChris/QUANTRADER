"""M4.9 - approval API (admin control loop): GET list + POST state transition.

WHY: tests BEFORE code, written against `docs/specs/m4.9_approval_ui.spec.md`
(spec LGTM @you 2026-10-07, verbatim "just do a pro job here"). The handler module
`modules.experience.api.v1.approval_router` does not exist yet; imports of it are
LAZY (inside `_router_module`) so this file collects cleanly and fails per-test with
an honest `ModuleNotFoundError` - the m4.8 pattern (0 collection errors, no blocker
entry needed).

Frozen things this slice CONSUMES and never reinvents:
  * the envelope (`envelope_success`/`envelope_error`/`http_status_for`) - the
    frontend branches on `error.code` only;
  * the `StrategyApproval` contract + `StrategyApprovalStore` (m4.8, contracts
    pkg 2.3.0) - the store's typed errors ARE the API's error map;
  * `ErrorCode` - no invented codes (spec Q2: reuse `unknown_symbol` for the
    registry-gate 404; flagged in the spec, not silently expanded).

Money: none in this slice (spec Scope) - no hand-calc tests.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from shared.contracts.errors import ErrorCode
from shared.contracts.strategy_approval import StrategyApproval
from shared.core.strategy_approval_store import StrategyApprovalStore

_BASE = "http://test"
_NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)

# (display name, strategy_id, version) - what the composition root will derive from
# registry + ParameterRegistry. Two entries: one seeded with a row, one implicit draft.
ENTRIES: list[tuple[str, str, str]] = [
    ("trend_following", "tf::trend", "2.1"),
    ("mean_reversion", "mr::mean", "1.0"),
]


def _router_module() -> Any:
    """Lazy import: honest RED before Step 5, clean collection either way."""
    from modules.experience.api.v1 import approval_router

    return approval_router


def _row(sid: str, ver: str, state: str, *, changed_by: str = "seed:m4.9",
         note: str | None = None, changed_at: datetime = _NOW) -> dict[str, Any]:
    return {
        "strategy_id": sid, "version": ver, "state": state,
        "changed_by": changed_by, "changed_at": changed_at, "note": note,
    }


class FakeConn:
    """PK-shaped fake for the store's only statements (SELECT-by-PK, UPSERT).
    Records every call so 'zero SQL' assertions are exact, not hopeful."""

    def __init__(self, rows: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = dict(rows or {})
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.calls.append(("fetch", args))
        if "WHERE state =" in sql:  # list_visible - not exercised by this API
            wanted = args[0]
            return [dict(r) for r in self.rows.values() if r["state"] == wanted]
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


def _store(db: FakeConn | None, *, known: set[str] | None = None) -> StrategyApprovalStore:
    return StrategyApprovalStore(db, known_strategies=known)


def _app(store: StrategyApprovalStore, entries: Any = None) -> FastAPI:
    if entries is None:
        entries = lambda: list(ENTRIES)
    app = FastAPI()
    app.include_router(_router_module().build_approval_router(store, entries=entries))
    return app


async def _call(
    app: FastAPI,
    method: str,
    url: str,
    *,
    role: str | None = "admin",
    json_body: dict[str, Any] | None = None,
    raw: bytes | None = None,
    cid: str | None = None,
) -> Any:
    headers: dict[str, str] = {}
    if role is not None:
        headers["X-Role"] = role
    if cid is not None:
        headers["X-Correlation-Id"] = cid
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=_BASE, headers=headers
    ) as client:
        if method == "GET":
            return await client.get(url)
        return await client.post(
            url,
            json=json_body,
            content=raw,
            headers={"Content-Type": "application/json"} if raw is not None else None,
        )


_GET = "/strategy-approvals"
_POST = "/strategy-approvals/state"


# ---------------- GET: listing, implicit draft, empty, REV3, envelope ----------------

async def test_get_lists_every_entry_with_envelope_shape() -> None:
    db = FakeConn({("tf::trend", "2.1"): _row("tf::trend", "2.1", "approved",
                                              note="g0 ok")})
    res = await _call(_app(_store(db)), "GET", _GET)
    assert res.status_code == 200
    body = res.json()
    assert body["error"] is None
    meta = body["meta"]
    assert meta["schema_version"] == "1.0"
    assert meta["as_of"].endswith("Z")
    assert meta["next_cursor"] is None and meta["has_more"] is False
    rows = {r["name"]: r for r in body["data"]["strategies"]}
    assert set(rows) == {"trend_following", "mean_reversion"}
    approved = rows["trend_following"]
    assert approved["strategy_id"] == "tf::trend"
    assert approved["version"] == "2.1"
    assert approved["state"] == "approved"
    assert approved["changed_by"] == "seed:m4.9"
    assert approved["changed_at"].endswith("Z")
    assert approved["note"] == "g0 ok"


async def test_get_implicit_draft_is_state_draft_with_null_metadata() -> None:
    # mr::mean has NO row: absent row = draft (fail-closed), metadata must be null -
    # the API never leaks the store's display-only synthetic sentinel (spec E3).
    res = await _call(_app(_store(FakeConn())), "GET", _GET)
    assert res.status_code == 200
    mr = next(r for r in res.json()["data"]["strategies"] if r["name"] == "mean_reversion")
    assert mr["state"] == "draft"
    assert mr["changed_by"] is None
    assert mr["changed_at"] is None
    assert mr["note"] is None
    assert mr["strategy_id"] == "mr::mean"


async def test_get_empty_registry_returns_empty_list() -> None:
    app = _app(_store(FakeConn()), entries=list)
    res = await _call(app, "GET", _GET)
    assert res.status_code == 200
    assert res.json()["data"]["strategies"] == []
    assert res.json()["error"] is None


async def test_get_rev3_two_testing_rows_listed_simultaneously() -> None:
    # REV3: multiple strategies may hold 'testing' at once - both must surface.
    db = FakeConn({
        ("tf::trend", "2.1"): _row("tf::trend", "2.1", "testing"),
        ("mr::mean", "1.0"): _row("mr::mean", "1.0", "testing"),
    })
    res = await _call(_app(_store(db)), "GET", _GET)
    assert res.status_code == 200
    states = {r["name"]: r["state"] for r in res.json()["data"]["strategies"]}
    assert states["trend_following"] == "testing"
    assert states["mean_reversion"] == "testing"


# ---------------- auth matrix (spec E1) ----------------

@pytest.mark.parametrize("role", ["viewer", "trader", "root", ""])
async def test_get_non_admin_role_403_with_zero_store_calls(role: str) -> None:
    db = FakeConn()
    res = await _call(_app(_store(db)), "GET", _GET, role=role)
    assert res.status_code == 403
    body = res.json()
    assert body["error"]["code"] == ErrorCode.permission_denied.value
    assert body["data"] is None
    assert db.calls == []  # denied BEFORE any store call


async def test_get_missing_role_header_403() -> None:
    db = FakeConn()
    res = await _call(_app(_store(db)), "GET", _GET, role=None)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == ErrorCode.permission_denied.value
    assert db.calls == []


@pytest.mark.parametrize("role", ["admin", "Admin", "ADMIN"])
async def test_get_role_is_normalised_like_the_account_router(role: str) -> None:
    res = await _call(_app(_store(FakeConn())), "GET", _GET, role=role)
    assert res.status_code == 200


# ---------------- GET failure + correlation id ----------------

async def test_get_unwired_store_503_fail_closed() -> None:
    # spec E2: a store we cannot read is a store that says NO - 503 unknown_state,
    # retryable, and NO data (the UI shows nothing new, never a guess).
    res = await _call(_app(_store(None)), "GET", _GET)
    assert res.status_code == 503
    body = res.json()
    assert body["error"]["code"] == ErrorCode.unknown_state.value
    assert body["error"]["retryable"] is True
    assert body["data"] is None


async def test_get_entries_failure_503() -> None:
    def _boom() -> list[tuple[str, str, str]]:
        raise RuntimeError("registry unreadable")

    res = await _call(_app(_store(FakeConn()), entries=_boom), "GET", _GET)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == ErrorCode.unknown_state.value


async def test_get_correlation_id_from_header_is_echoed() -> None:
    res = await _call(_app(_store(FakeConn())), "GET", _GET, cid="cid-abc-123")
    assert res.status_code == 200
    assert res.json()["meta"]["correlation_id"] == "cid-abc-123"


# ---------------- POST: legal transition + read-back ----------------

async def test_post_draft_to_testing_returns_store_readback() -> None:
    db = FakeConn()
    res = await _call(_app(_store(db)), "POST", _POST, json_body={
        "strategy_id": "tf::trend", "version": "2.1", "new_state": "testing",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["error"] is None
    data = body["data"]
    assert data["state"] == "testing"
    assert data["strategy_id"] == "tf::trend"
    assert data["version"] == "2.1"
    assert data["changed_by"] == "admin:ui"  # Q1 default
    assert data["changed_at"].endswith("Z")
    # read-back proof: the response IS the stored row
    assert db.rows[("tf::trend", "2.1")]["state"] == "testing"


async def test_post_client_changed_by_and_note_pass_through() -> None:
    db = FakeConn()
    res = await _call(_app(_store(db)), "POST", _POST, json_body={
        "strategy_id": "tf::trend", "version": "2.1", "new_state": "testing",
        "changed_by": "alice", "note": "second pass after g0 review",
    })
    assert res.status_code == 200
    assert res.json()["data"]["changed_by"] == "alice"
    assert res.json()["data"]["note"] == "second pass after g0 review"
    assert db.rows[("tf::trend", "2.1")]["changed_by"] == "alice"


async def test_post_second_write_wins_sequentially() -> None:
    # spec E10: last committed write wins (M1.5 precedence), both writes 200.
    app = _app(_store(FakeConn()))
    first = await _call(app, "POST", _POST, json_body={
        "strategy_id": "tf::trend", "version": "2.1", "new_state": "testing",
        "changed_by": "alice",
    })
    second = await _call(app, "POST", _POST, json_body={
        "strategy_id": "tf::trend", "version": "2.1", "new_state": "approved",
        "changed_by": "bob",
    })
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["data"]["state"] == "approved"
    assert second.json()["data"]["changed_by"] == "bob"


async def test_post_idempotent_same_state_writes_nothing() -> None:
    db = FakeConn({("tf::trend", "2.1"): _row("tf::trend", "2.1", "testing")})
    res = await _call(_app(_store(db)), "POST", _POST, json_body={
        "strategy_id": "tf::trend", "version": "2.1", "new_state": "testing",
    })
    assert res.status_code == 200
    assert res.json()["data"]["state"] == "testing"
    assert not any(op == "execute" for op, _ in db.calls)  # no-op wrote nothing


async def test_post_correlation_id_from_header_is_echoed() -> None:
    res = await _call(_app(_store(FakeConn())), "POST", _POST, cid="cid-post-9",
                      json_body={"strategy_id": "tf::trend", "version": "2.1",
                                 "new_state": "testing"})
    assert res.status_code == 200
    assert res.json()["meta"]["correlation_id"] == "cid-post-9"


# ---------------- POST: typed failure map (spec E6/E7/E8 + adversarial) ----------------

async def test_post_illegal_transition_409_store_unchanged() -> None:
    db = FakeConn({("mr::mean", "1.0"): _row("mr::mean", "1.0", "rejected")})
    res = await _call(_app(_store(db)), "POST", _POST, json_body={
        "strategy_id": "mr::mean", "version": "1.0", "new_state": "approved",
    })
    assert res.status_code == 409
    body = res.json()
    assert body["error"]["code"] == ErrorCode.illegal_transition.value
    assert body["data"] is None
    assert body["error"]["message"]  # carries from->to for the UI
    assert db.rows[("mr::mean", "1.0")]["state"] == "rejected"  # unchanged


async def test_post_unknown_strategy_404_with_zero_sql() -> None:
    db = FakeConn()
    store = _store(db, known={"tf::trend", "mr::mean"})
    res = await _call(_app(store), "POST", _POST, json_body={
        "strategy_id": "x'; DROP TABLE strategy_approvals;--", "version": "2.1",
        "new_state": "testing",
    })
    assert res.status_code == 404
    assert res.json()["error"]["code"] == ErrorCode.unknown_symbol.value  # spec Q2
    assert db.calls == []  # registry gate fires before any statement


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({"strategy_id": "tf::trend", "version": "2.1", "new_state": "APPROVED"},
         "state literal outside the frozen Literal"),
        ({"strategy_id": "tf::trend", "version": "2.1", "new_state": "testing",
          "note": "n" * 201}, "note over 200 chars"),
        ({"strategy_id": "tf::trend", "version": "2.1", "new_state": "testing",
          "changed_by": "   "}, "whitespace-only changed_by"),
        ({"strategy_id": "tf::trend", "version": "2.1", "new_state": "testing",
          "changed_at": "2020-01-01T00:00:00Z"}, "client-supplied extra field"),
        ({"strategy_id": "tf::trend", "new_state": "testing"}, "missing version"),
    ],
    ids=["bad-state", "long-note", "blank-changed_by", "extra-field", "missing-version"],
)
async def test_post_contract_violations_422_zero_sql(
    body: dict[str, Any], why: str,
) -> None:
    db = FakeConn()
    res = await _call(_app(_store(db)), "POST", _POST, json_body=body)
    assert res.status_code == 422, why
    err = res.json()["error"]
    assert err["code"] == ErrorCode.capability_not_supported.value, why
    assert err["message"], why  # never a bare status with no reason
    assert db.calls == [], why


async def test_post_malformed_json_422_envelope() -> None:
    # The handler owns body parsing end to end: even broken JSON is an envelope reply,
    # never FastAPI's `detail` dialect (the frontend branches on error.code only).
    db = FakeConn()
    res = await _call(_app(_store(db)), "POST", _POST, raw=b"{not json")
    assert res.status_code == 422
    assert res.json()["error"]["code"] == ErrorCode.capability_not_supported.value
    assert db.calls == []


async def test_post_unwired_store_503_fail_closed() -> None:
    res = await _call(_app(_store(None)), "POST", _POST, json_body={
        "strategy_id": "tf::trend", "version": "2.1", "new_state": "testing",
    })
    assert res.status_code == 503
    body = res.json()
    assert body["error"]["code"] == ErrorCode.unknown_state.value
    assert body["error"]["retryable"] is True
    assert body["data"] is None


async def test_post_missing_role_403_with_zero_store_calls() -> None:
    db = FakeConn()
    res = await _call(_app(_store(db)), "POST", _POST, role=None, json_body={
        "strategy_id": "tf::trend", "version": "2.1", "new_state": "testing",
    })
    assert res.status_code == 403
    assert res.json()["error"]["code"] == ErrorCode.permission_denied.value
    assert db.calls == []


@pytest.mark.parametrize("role", ["viewer", "trader"])
async def test_post_non_admin_role_403(role: str) -> None:
    db = FakeConn()
    res = await _call(_app(_store(db)), "POST", _POST, role=role, json_body={
        "strategy_id": "tf::trend", "version": "2.1", "new_state": "testing",
    })
    assert res.status_code == 403
    assert db.calls == []


# ---------------- wiring sanity: the store used is the REAL store ----------------

def test_slice_reuses_the_real_store_not_a_reimplementation() -> None:
    # The router must depend on the m4.8 store (structural Protocol), not copy it.
    # A fake above is a CONNECTION, never a store - this pins that choice.
    store = _store(FakeConn())
    assert type(store) is StrategyApprovalStore
    # ... and the frozen contract still guards every write path underneath.
    with pytest.raises(ValidationError):
        StrategyApproval(
            strategy_id="s::x", version="1.0",
            state="APPROVED",  # type: ignore[arg-type]  # deliberately not a Literal
            changed_by="admin:ui", changed_at=_NOW, note=None,
        )


# ---------------- composition root (app.main): routes + lazy proxies ----------------
# WHY: the router factory tests above prove the leaf; these pin the WIRING - that
# create_app mounts both routes under /api/v1, that auth fires before any wiring,
# and that an app whose lifespan never ran answers 503 fail-closed (never 404/500).

def _mounted(app: FastAPI) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        if path:
            out[path] = set(getattr(route, "methods", None) or [])
    return out


def test_create_app_registers_the_approval_routes() -> None:
    from app.main import create_app

    by_path = _mounted(create_app())
    assert "GET" in by_path.get("/api/v1/strategy-approvals", set())
    assert "POST" in by_path.get("/api/v1/strategy-approvals/state", set())


async def test_real_app_non_admin_403_before_any_wiring() -> None:
    # Auth precedes entries/store: without this, a role check that only ran once the
    # store was reachable would 503 instead of 403 when the surface is unwired.
    from app.main import create_app

    res = await _call(create_app(), "GET", "/api/v1/strategy-approvals", role="viewer")
    assert res.status_code == 403
    assert res.json()["error"]["code"] == ErrorCode.permission_denied.value


async def test_real_app_unwired_admin_get_503_fail_closed() -> None:
    # ASGITransport never runs the lifespan => no listing, no pool. An admin still
    # gets the typed retryable envelope, never a 404 (route exists) or 500 (crash).
    from app.main import create_app

    res = await _call(create_app(), "GET", "/api/v1/strategy-approvals")
    assert res.status_code == 503
    body = res.json()
    assert body["error"]["code"] == ErrorCode.unknown_state.value
    assert body["error"]["retryable"] is True
    assert body["data"] is None


async def test_real_app_unwired_admin_post_503_fail_closed() -> None:
    from app.main import create_app

    res = await _call(create_app(), "POST", "/api/v1/strategy-approvals/state",
                      json_body={"strategy_id": "tf::trend", "version": "2.1",
                                 "new_state": "testing"})
    assert res.status_code == 503
    assert res.json()["error"]["code"] == ErrorCode.unknown_state.value
    assert res.json()["data"] is None


async def test_real_app_proxies_serve_the_preset_store_and_listing() -> None:
    # Lifespan normally composes these; pre-setting them here proves the create_app
    # wiring (router + lazy proxies over app.state) end to end WITHOUT a database.
    from app.main import create_app

    app = create_app()
    db = FakeConn({("tf::trend", "2.1"): _row("tf::trend", "2.1", "approved",
                                              note="g0 ok")})
    app.state.approval_entries = list(ENTRIES)
    app.state.approval_store = StrategyApprovalStore(
        db, known_strategies={"tf::trend", "mr::mean"})

    res = await _call(app, "GET", "/api/v1/strategy-approvals")
    assert res.status_code == 200
    rows = {r["name"]: r for r in res.json()["data"]["strategies"]}
    assert rows["trend_following"]["state"] == "approved"
    assert rows["mean_reversion"]["state"] == "draft"  # implicit draft, no row

    res2 = await _call(app, "POST", "/api/v1/strategy-approvals/state", json_body={
        "strategy_id": "tf::trend", "version": "2.1", "new_state": "testing",
    })
    assert res2.status_code == 200
    assert res2.json()["data"]["state"] == "testing"
    assert db.rows[("tf::trend", "2.1")]["state"] == "testing"


def test_compose_approval_entries_covers_exactly_the_registry() -> None:
    # Real-config composition check (the wiring the lifespan will run): every
    # registry strategy appears once with non-empty sid/version, nothing else.
    from app.main import _compose_approval_entries
    from modules.m4_alpha.strategy.strategy_registry import StrategyRegistry

    entries = _compose_approval_entries()
    assert entries, "registry must yield at least one strategy"
    registry = StrategyRegistry(package="modules.m3_analytics.strategies")
    registry.load()
    names = [n for n, _, _ in entries]
    assert set(names) == set(registry.names())
    assert len(set(names)) == len(names)  # no duplicate display rows
    assert len({sid for _, sid, _ in entries}) == len(entries)  # unique sids
    assert all(isinstance(v, str) and v for _, _, v in entries)
