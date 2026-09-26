from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FRONTEND = _REPO / "frontend"

# The stylesheet is split into ordered partials; tests read the assembled result.
_STYLE_PARTS = (
    "tokens.css",
    "base.css",
    "shell.css",
    "panels.css",
    "market.css",
    "research.css",
    "footer.css",
    "light-type.css",
    "responsive.css",
)


_APP_MODULES = ("App.tsx", "MarketPanel.tsx", "HomeDashboard.tsx", "TradePane.tsx", "MarketsGrid.tsx", "TradingTicket.tsx")


def _read_app_source() -> str:
    """Whole app source across the split feature modules."""
    return "\n".join((_FRONTEND / "app" / name).read_text(encoding="utf-8") for name in _APP_MODULES)

def _read_styles() -> str:
    parts = [(_FRONTEND / "app" / "styles.css").read_text(encoding="utf-8")]
    parts += [(_FRONTEND / "app" / "styles" / name).read_text(encoding="utf-8") for name in _STYLE_PARTS]
    return "\n".join(parts)


def test_viewer_dashboard_stylesheet_exists() -> None:
    assert (_FRONTEND / "app" / "styles.css").is_file()
    assert (_FRONTEND / "app" / "main.tsx").read_text(encoding="utf-8").count("./styles.css") == 1


def test_design_system_foundations_are_wired() -> None:
    styles = _read_styles()
    for foundation in ("colors.css", "typography.css", "spacing.css"):
        assert foundation in styles
    assert "--bg" in styles
    assert "font-family" in styles


def test_viewer_shell_uses_design_system_classes() -> None:
    app = _read_app_source()
    for class_name in (
        "app-shell",
        "app-header",
        "header-actions",
        "primary-nav",
        "workspace",
        "viewer-dashboard",
        "panel",
    ):
        assert class_name in app, f"missing shell class: {class_name}"


def test_viewer_dashboard_exposes_public_surfaces_without_watchlist() -> None:
    app = _read_app_source()
    for surface in ("Markets", "Public intelligence", "News", "Predictive quality for today"):
        assert surface in app
    # Viewer is public: no personal watchlist, no portfolio framing.
    assert "Watchlist" not in app


def test_top_market_experiment_status_chips_are_removed() -> None:
    app = _read_app_source()
    for removed in ("Market: experiment", "sequence:", "status-strip", "System status"):
        assert removed not in app


def test_viewer_dashboard_is_mock_only_and_public() -> None:
    # Scope: the PUBLIC Viewer surface only. Private words are legitimate on the Trader
    # surfaces (Portfolio / Orders / Settings) — what must never happen is the Viewer
    # dashboard, or any Viewer route, exposing them
    # (FRONTEND_BACKEND_INTEGRATION_PROPOSAL.md:107, :233).
    home = (_FRONTEND / "app" / "HomeDashboard.tsx").read_text(encoding="utf-8").lower()
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8").lower()
    for forbidden in ("fetch(", "websocket", "x-role", "portfolio", "orders", "kill", "secret", "capability", "health"):
        assert forbidden not in home, f"public Viewer surface must not mention {forbidden}"
    for forbidden in ("fetch(", "websocket", "x-role", "authorization", "localstorage"):
        assert forbidden not in app
    assert "estimated accuracy" in home


def test_viewer_dashboard_has_no_presentation_state_scratch_row() -> None:
    # AMENDED 2026-09-27 by human directive: the "loading / empty / stale" row is removed.
    # It was a scratch strip showing the presentation-state vocabulary, not a real surface,
    # and the states it named now appear where they are actually produced.
    home = (_FRONTEND / "app" / "HomeDashboard.tsx").read_text(encoding="utf-8")
    assert "state-row" not in home
    assert "Presentation states" not in home
    # And the CSS for it goes with it.
    styles = _read_styles()
    assert ".state-row" not in styles, "state-row CSS is now dead and must be deleted"


def test_stylesheet_defines_responsive_and_accessible_shell() -> None:
    styles = _read_styles()
    assert "@media" in styles
    assert ":focus-visible" in styles
    assert "prefers-reduced-motion" in styles
    assert "aria" not in styles.lower()


def test_viewer_dashboard_stays_modular_and_small() -> None:
    for relative in ("app/styles.css", "app/App.tsx", "app/nav_config.ts"):
        path = _FRONTEND / relative
        assert path.is_file()
        assert path.stat().st_size < 20_000
