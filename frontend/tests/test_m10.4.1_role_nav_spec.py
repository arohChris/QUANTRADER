from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FRONTEND = _REPO / "frontend"


def test_navigation_config_exists_and_is_frontend_only() -> None:
    assert (_FRONTEND / "app" / "nav_config.ts").is_file()
    text = (_FRONTEND / "app" / "nav_config.ts").read_text(encoding="utf-8").lower()
    assert "from shared" not in text
    assert "from modules" not in text
    assert "fetch(" not in text
    assert "websocket" not in text


def test_navigation_config_defines_all_access_classes() -> None:
    text = (_FRONTEND / "app" / "nav_config.ts").read_text(encoding="utf-8")
    for required in ("Viewer", "Trader", "Admin", "/login", "/app", "/admin"):
        assert required in text
    # The privileged role is named Admin, not Owner/Admin.
    assert "Owner/Admin" not in text


def test_navigation_config_keeps_viewer_read_only_and_public() -> None:
    text = (_FRONTEND / "app" / "nav_config.ts").read_text(encoding="utf-8")
    viewer = text[text.index("Viewer") :]
    assert "read_only" in viewer
    assert "market" in viewer.lower()
    assert "health" in viewer.lower()
    assert "order" not in viewer.lower()
    assert "portfolio" not in viewer.lower()
    assert "kill" not in viewer.lower()


def test_navigation_config_exposes_trader_own_workspace_actions() -> None:
    text = (_FRONTEND / "app" / "nav_config.ts").read_text(encoding="utf-8")
    assert "Trader" in text
    assert "trading" in text.lower()
    assert "portfolio" in text.lower()
    assert "orders" in text.lower()
    assert "settings" in text.lower()
    assert "admin" not in text[text.index("Trader") : text.index("Admin")].lower()


def test_navigation_config_exposes_admin_workspace() -> None:
    text = (_FRONTEND / "app" / "nav_config.ts").read_text(encoding="utf-8")
    admin = text[text.index("Admin:") :]
    for required in ("admin", "system", "audit", "configuration", "kill", "resume"):
        assert required in admin.lower()


def test_app_wires_role_aware_navigation() -> None:
    text = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    assert "nav_config" in text
    assert "role" in text
    assert "nav" in text
    assert "aria-label" in text or "aria-current" in text


def test_navigation_is_mock_driven_and_has_no_backend_authority() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8").lower()
    nav = (_FRONTEND / "app" / "nav_config.ts").read_text(encoding="utf-8").lower()
    assert "mock_data" in app or "mockdata" in app
    assert "fetch(" not in app
    assert "websocket" not in app
    assert "x-role" not in app
    assert "localstorage" not in app
    assert "authorization" not in nav


def test_navigation_stays_small_and_modular() -> None:
    nav = (_FRONTEND / "app" / "nav_config.ts")
    app = (_FRONTEND / "app" / "App.tsx")
    assert nav.stat().st_size < 20_000
    assert app.stat().st_size < 20_000
    assert (_FRONTEND / "app" / "nav_config.ts").is_file()
    assert (_FRONTEND / "shared" / "ui" / "index.ts").is_file()
