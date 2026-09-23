"""
M1.3 Account Mode + Auto-Trade — 2-Gate Step 3 tests (BEFORE impl).
Spec: docs/specs/m1.3_account_mode_two_gate.spec.md (LGTM @you 2026-09-23).
WHY new file: two-gate law; tests/test_account_switch.py is 1-gate obsolete path.
Mode switch never flips AUTO; AUTO ON at DEMO allowed; LIVE+OFF = armed but paused.
Collection RED until Step 5 AccountGateService + two-gate API exist — honest.
"""
from __future__ import annotations

import os

import pytest

# Direct planned-impl imports — RED until Step 5 (spec Step 3 order).
from shared.core.account_mode import (  # type: ignore[attr-defined]  # noqa: F401
    AccountGateService,
    InvalidAccountModeError,
    InvalidAutoTradeError,
    LiveNotArmedError,
)


def _armed_accounts():
    from shared.core.config import AccountsConfig

    return AccountsConfig.model_validate(
        {
            "default_mode": "demo",
            "brokers": {
                "deriv": {
                    "mode": "demo",
                    "demo": {"enabled": True},
                    "live": {"enabled": True, "account_id_env": "DERIV_LIVE_ACCOUNT_ID"},
                }
            },
        }
    )


def _disarmed_accounts():
    from shared.core.config import AccountsConfig

    return AccountsConfig.model_validate(
        {
            "default_mode": "demo",
            "brokers": {
                "deriv": {
                    "mode": "demo",
                    "demo": {"enabled": True},
                    "live": {"enabled": False},
                }
            },
        }
    )


def _svc_armed() -> AccountGateService:
    return AccountGateService(accounts=_armed_accounts(), env={})


def _svc_disarmed() -> AccountGateService:
    return AccountGateService(accounts=_disarmed_accounts(), env={})


# --- Mode / boot / source (Gate 1) — test plan 1-10 ---


def test_default_is_demo_auto_off() -> None:
    # Spec Correct #1 / test #1
    svc = _svc_disarmed()
    snap = svc.snapshot()
    assert snap["mode"] == "demo"
    assert snap["auto"] == "OFF"
    assert snap["source"] == "default"
    assert snap["auto_source"] == "default"
    assert snap["bot_will_trade"] is False


def test_switch_live_without_arm_raises_and_stays_demo() -> None:
    # Spec Correct #3 / test #2 — typed LiveNotArmedError, not bare ValueError-as-contract
    svc = _svc_disarmed()
    with pytest.raises(LiveNotArmedError):
        svc.set_mode("live")
    assert svc.get_mode() == "demo"


def test_switch_live_when_armed_then_get_live() -> None:
    # Spec Correct #4 / test #3
    svc = _svc_armed()
    assert svc.set_mode("live") == "live"
    assert svc.get_mode() == "live"


def test_switch_back_to_demo() -> None:
    # Spec Correct #5 / test #4
    svc = _svc_armed()
    svc.set_mode("live")
    assert svc.set_mode("demo") == "demo"
    assert svc.get_mode() == "demo"


def test_env_seed_live_armed_starts_live_source_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Spec boot tree / test #5
    monkeypatch.setenv("ACCOUNT_MODE", "live")
    svc = AccountGateService(accounts=_armed_accounts(), env=os.environ)
    snap = svc.snapshot()
    assert snap["mode"] == "live"
    assert snap["source"] == "env"


def test_env_seed_live_without_arm_starts_demo_source_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Spec boot tree / test #6 — env seed ignored, no crash, no bypass
    monkeypatch.setenv("ACCOUNT_MODE", "live")
    svc = AccountGateService(accounts=_disarmed_accounts(), env=os.environ)
    snap = svc.snapshot()
    assert snap["mode"] == "demo"
    assert snap["source"] == "default"


def test_env_seed_invalid_account_mode_falls_back_demo_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Spec #12 / test #7 — invalid boot ACCOUNT_MODE → demo + default (deterministic)
    monkeypatch.setenv("ACCOUNT_MODE", "live_armed")
    svc = AccountGateService(accounts=_armed_accounts(), env=os.environ)
    snap = svc.snapshot()
    assert snap["mode"] == "demo"
    assert snap["source"] == "default"
    assert snap["auto"] == "OFF"


def test_invalid_mode_rejected_typed() -> None:
    # Spec Correct / test #8 — InvalidAccountModeError, not ValueError-as-contract
    svc = _svc_armed()
    with pytest.raises(InvalidAccountModeError):
        svc.set_mode("paper")  # type: ignore[arg-type]
    assert svc.get_mode() == "demo"


def test_set_demo_idempotent_source_api() -> None:
    # Spec source table / test #9 — successful API write → source=api even if same value
    svc = _svc_disarmed()
    svc.set_mode("demo")
    svc.set_mode("demo")
    assert svc.get_mode() == "demo"
    assert svc.snapshot()["source"] == "api"


def test_mode_switch_does_not_change_auto() -> None:
    # Spec invariant / test #10 — MODE never flips AUTO
    svc = _svc_armed()
    assert svc.snapshot()["auto"] == "OFF"
    svc.set_mode("live")
    assert svc.get_mode() == "live"
    assert svc.get_auto() == "OFF"
    svc.set_mode("demo")
    assert svc.get_auto() == "OFF"


# --- AUTO (Gate 2) — test plan 11-18 ---


def test_auto_default_off() -> None:
    # test #11
    svc = _svc_armed()
    assert svc.get_auto() == "OFF"
    assert svc.snapshot()["auto_source"] == "default"


def test_auto_on_at_demo_allowed() -> None:
    # Spec A3 / Correct #6 / test #12 — paper path
    svc = _svc_disarmed()
    assert svc.get_mode() == "demo"
    assert svc.set_auto("ON") == "ON"
    snap = svc.snapshot()
    assert snap["mode"] == "demo"
    assert snap["auto"] == "ON"
    assert snap["bot_will_trade"] is False  # not live
    assert snap["auto_source"] == "api"


def test_auto_on_at_live_armed_ok_bot_will_trade_true() -> None:
    # Spec Correct #7 / test #13
    svc = _svc_armed()
    svc.set_mode("live")
    svc.set_auto("ON")
    snap = svc.snapshot()
    assert snap["mode"] == "live"
    assert snap["auto"] == "ON"
    assert snap["bot_will_trade"] is True


def test_auto_on_at_live_not_armed_409_and_stays_off() -> None:
    # Spec Correct #8 / test #14 — mode==live + live_armed flipped false → LiveNotArmedError;
    # mode and auto unchanged (API maps LiveNotArmedError → 409).
    # WHY config swap: Day-1 arm is static in AccountsConfig; public API cannot go live
    # while unarmed (covered by set_mode tests). This freezes the AUTO gate for that state.
    svc = _svc_armed()
    svc.set_mode("live")
    assert svc.live_armed() is True
    svc._accounts = _disarmed_accounts()  # arm revoked while mode stays live
    with pytest.raises(LiveNotArmedError):
        svc.set_auto("ON")
    assert svc.get_mode() == "live"
    assert svc.get_auto() == "OFF"
    assert svc.snapshot()["bot_will_trade"] is False


def test_auto_off_always_allowed() -> None:
    # Spec Correct #9 / test #15
    svc = _svc_disarmed()
    svc.set_auto("ON")
    assert svc.set_auto("OFF") == "OFF"
    assert svc.snapshot()["auto"] == "OFF"


def test_auto_invalid_value_422_typed() -> None:
    # Spec Correct / test #16 — InvalidAutoTradeError
    svc = _svc_armed()
    with pytest.raises(InvalidAutoTradeError):
        svc.set_auto("maybe")  # type: ignore[arg-type]
    assert svc.get_auto() == "OFF"


def test_auto_switch_does_not_change_mode() -> None:
    # Spec invariant / test #17 — AUTO never flips MODE
    svc = _svc_armed()
    before = svc.get_mode()
    svc.set_auto("ON")
    assert svc.get_mode() == before
    svc.set_auto("OFF")
    assert svc.get_mode() == before


def test_idempotent_auto_on_sets_auto_source_api() -> None:
    # Spec source table / test #18
    svc = _svc_disarmed()
    svc.set_auto("ON")
    svc.set_auto("ON")
    snap = svc.snapshot()
    assert snap["auto"] == "ON"
    assert snap["auto_source"] == "api"


# --- API / RBAC — test plan 19-30 ---


@pytest.fixture()
def api_armed():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app(accounts=_armed_accounts()))


@pytest.fixture()
def api_disarmed():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app(accounts=_disarmed_accounts()))


def test_api_get_returns_status_json_no_perm_needed(api_armed) -> None:
    # test #19 — GET requires neither account.switch nor trading.enable; no X-Role
    r = api_armed.get("/api/v1/account/status")
    if r.status_code == 404:
        # Accept GET /api/v1/account/mode as the frozen alternate path (spec allows either)
        r = api_armed.get("/api/v1/account/mode")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "demo"
    assert body["auto"] == "OFF"
    assert "live_armed" in body
    assert "bot_will_trade" in body
    assert "source" in body
    assert "auto_source" in body


def test_api_post_mode_live_armed_ok(api_armed) -> None:
    # test #20
    r = api_armed.post(
        "/api/v1/account/mode",
        json={"mode": "live"},
        headers={"X-Role": "trader"},
    )
    assert r.status_code == 200
    assert r.json()["mode"] == "live"
    assert r.json()["auto"] == "OFF"  # MODE does not flip AUTO


def test_api_post_mode_not_armed_409(api_disarmed) -> None:
    # test #21
    r = api_disarmed.post(
        "/api/v1/account/mode",
        json={"mode": "live"},
        headers={"X-Role": "trader"},
    )
    assert r.status_code == 409
    assert api_disarmed.get("/api/v1/account/status").json()["mode"] == "demo"


def test_api_post_mode_invalid_422(api_armed) -> None:
    # test #22
    r = api_armed.post(
        "/api/v1/account/mode",
        json={"mode": "yolo"},
        headers={"X-Role": "trader"},
    )
    assert r.status_code == 422


def test_api_viewer_forbidden_403_both_endpoints(api_armed) -> None:
    # test #23 — adversarial: viewer denied both gates
    r_mode = api_armed.post(
        "/api/v1/account/mode",
        json={"mode": "demo"},
        headers={"X-Role": "viewer"},
    )
    r_auto = api_armed.post(
        "/api/v1/account/auto-trade",
        json={"auto": "ON"},
        headers={"X-Role": "viewer"},
    )
    assert r_mode.status_code == 403
    assert r_auto.status_code == 403
    assert api_armed.get("/api/v1/account/status").json()["auto"] == "OFF"


def test_api_missing_x_role_post_forbidden_403(api_armed) -> None:
    # test #24 — missing header must NOT default to privileged
    r = api_armed.post("/api/v1/account/mode", json={"mode": "demo"})
    assert r.status_code == 403
    r2 = api_armed.post("/api/v1/account/auto-trade", json={"auto": "ON"})
    assert r2.status_code == 403


def test_api_unknown_x_role_post_forbidden_403(api_armed) -> None:
    # test #25 — unknown role → 403 (not silently allowed)
    r = api_armed.post(
        "/api/v1/account/mode",
        json={"mode": "demo"},
        headers={"X-Role": "superuser"},
    )
    assert r.status_code == 403
    r2 = api_armed.post(
        "/api/v1/account/auto-trade",
        json={"auto": "ON"},
        headers={"X-Role": ""},
    )
    assert r2.status_code == 403


def test_api_post_auto_on_ok_includes_auto_field(api_armed) -> None:
    # test #26
    r = api_armed.post(
        "/api/v1/account/auto-trade",
        json={"auto": "ON"},
        headers={"X-Role": "trader"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["auto"] == "ON"
    assert body["auto_source"] == "api"
    assert body["mode"] == "demo"
    assert body["bot_will_trade"] is False


def test_source_changes_to_api_after_successful_post(api_armed) -> None:
    # test #27 — source transition table
    assert api_armed.get("/api/v1/account/status").json()["source"] == "default"
    r = api_armed.post(
        "/api/v1/account/mode",
        json={"mode": "demo"},  # idempotent same value
        headers={"X-Role": "admin"},
    )
    assert r.status_code == 200
    assert r.json()["source"] == "api"
    assert r.json()["mode"] == "demo"


def test_live_armed_independent_of_mode(api_armed) -> None:
    # test #28 — demo + armed is valid (armed ≠ currently live)
    body = api_armed.get("/api/v1/account/status").json()
    assert body["mode"] == "demo"
    assert body["live_armed"] is True
    assert body["bot_will_trade"] is False


def test_bot_will_trade_only_live_and_on(api_armed) -> None:
    # test #29 — Day-1 formula mode==live && auto==ON
    st = api_armed.get("/api/v1/account/status").json()
    assert st["bot_will_trade"] is False  # demo + OFF

    api_armed.post(
        "/api/v1/account/auto-trade",
        json={"auto": "ON"},
        headers={"X-Role": "trader"},
    )
    st = api_armed.get("/api/v1/account/status").json()
    assert st["bot_will_trade"] is False  # demo + ON still False

    api_armed.post(
        "/api/v1/account/mode",
        json={"mode": "live"},
        headers={"X-Role": "trader"},
    )
    st = api_armed.get("/api/v1/account/status").json()
    assert st["bot_will_trade"] is True  # live + ON


def test_api_root_404_and_no_static_or_html_routes(api_armed) -> None:
    # test #30 — behavioral 404 + structural: no StaticFiles/FileResponse/HTMLResponse/Mount
    r = api_armed.get("/")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")

    app = api_armed.app
    from starlette.responses import FileResponse, HTMLResponse
    from starlette.staticfiles import StaticFiles

    for route in app.routes:
        assert not isinstance(route, (FileResponse, HTMLResponse, StaticFiles)), (
            f"static/html route installed: {route}"
        )
    mounts = [rt for rt in app.routes if type(rt).__name__ == "Mount"]
    assert mounts == [], f"unexpected Mount routes (static): {mounts}"
