"""
M1.3 Account Mode Switch — BACKEND tests only (service + thin API).
WHY: docs/specs/m1.3_account_switch.spec.md — one runtime mode for bot + API.
Frontend/page tests live in frontend/tests/ (frontend itself is at project-root frontend/).
Default demo; live requires live.enabled arm; thin API JSON-only.
"""
from __future__ import annotations

import os

import pytest

# Direct impl imports — collection RED until Step 5 (honest).
from shared.core.account_mode import AccountModeService, get_account_mode_service  # noqa: F401


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


def test_default_is_demo() -> None:
    svc = AccountModeService(accounts=_disarmed_accounts())
    assert svc.get_mode() == "demo"
    assert svc.source == "default"


def test_switch_live_without_arm_raises_and_stays_demo() -> None:
    svc = AccountModeService(accounts=_disarmed_accounts())
    with pytest.raises(ValueError, match="live"):
        svc.set_mode("live")
    assert svc.get_mode() == "demo"


def test_switch_live_when_armed_then_get_mode_live() -> None:
    svc = AccountModeService(accounts=_armed_accounts())
    svc.set_mode("live")
    assert svc.get_mode() == "live"
    assert svc.source == "api"
    # bot path — same singleton API would mutate
    assert get_account_mode_service is not None


def test_switch_back_to_demo() -> None:
    svc = AccountModeService(accounts=_armed_accounts())
    svc.set_mode("live")
    svc.set_mode("demo")
    assert svc.get_mode() == "demo"


def test_env_seed_mode_at_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCOUNT_MODE", "live")
    svc = AccountModeService(accounts=_armed_accounts(), env=os.environ)
    assert svc.get_mode() == "live"
    assert svc.source == "env"


def test_env_seed_live_without_arm_defaults_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCOUNT_MODE", "live")
    svc = AccountModeService(accounts=_disarmed_accounts(), env=os.environ)
    # cannot arm live from env alone — stay safe default
    assert svc.get_mode() == "demo"


def test_invalid_mode_rejected() -> None:
    svc = AccountModeService(accounts=_armed_accounts())
    with pytest.raises(ValueError, match="mode"):
        svc.set_mode("paper")  # type: ignore[arg-type]
    assert svc.get_mode() == "demo"


def test_set_demo_idempotent() -> None:
    svc = AccountModeService(accounts=_armed_accounts())
    svc.set_mode("demo")
    svc.set_mode("demo")
    assert svc.get_mode() == "demo"


# --- thin API (FastAPI TestClient) ---

@pytest.fixture()
def api_client():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from app.main import create_app  # path lock: pythonpath=app root, not package prefix

    accounts = _armed_accounts()
    app = create_app(accounts=accounts)
    return TestClient(app)


def test_api_get_returns_demo_json(api_client) -> None:
    r = api_client.get("/api/v1/account/mode")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "demo"
    assert "live_armed" in body


def test_api_post_live_armed_ok(api_client) -> None:
    r = api_client.post(
        "/api/v1/account/mode",
        json={"mode": "live"},
        headers={"X-Role": "trader"},
    )
    assert r.status_code == 200
    assert r.json()["mode"] == "live"
    r2 = api_client.get("/api/v1/account/mode")
    assert r2.json()["mode"] == "live"


def test_api_post_switch_back_demo(api_client) -> None:
    api_client.post("/api/v1/account/mode", json={"mode": "live"}, headers={"X-Role": "admin"})
    r = api_client.post("/api/v1/account/mode", json={"mode": "demo"}, headers={"X-Role": "trader"})
    assert r.status_code == 200
    assert r.json()["mode"] == "demo"


def test_api_viewer_forbidden_403(api_client) -> None:
    r = api_client.post(
        "/api/v1/account/mode",
        json={"mode": "demo"},
        headers={"X-Role": "viewer"},
    )
    assert r.status_code == 403


def test_api_post_live_not_armed_409() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app  # path lock: pythonpath=app root, not package prefix

    app = create_app(accounts=_disarmed_accounts())
    client = TestClient(app)
    r = client.post(
        "/api/v1/account/mode",
        json={"mode": "live"},
        headers={"X-Role": "trader"},
    )
    assert r.status_code == 409
    # mode unchanged
    assert client.get("/api/v1/account/mode").json()["mode"] == "demo"


def test_api_invalid_mode_422(api_client) -> None:
    r = api_client.post(
        "/api/v1/account/mode",
        json={"mode": "yolo"},
        headers={"X-Role": "trader"},
    )
    assert r.status_code == 422


def test_api_root_is_json_only_not_html(api_client) -> None:
    """GET / is 404 — API never serves HTML (frontend is separate module)."""
    r = api_client.get("/")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")
