# Spec-locked tests for M10.4.12 — mobile menu hidden until clicked.
# Written against frontend/specs/m10.4.12_mobile_menu.spec.md BEFORE the implementation
# (AGENTS.md §2 Step 3). Text-parses App.tsx + responsive.css; never imports backend code.
# Step-4 LGTM: human plan approval 2026-09-30 ("the small screen should follow suit,
# lets move on" after the written plan = standing approval for this slice).
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FRONTEND = _REPO / "frontend"


def _app() -> str:
    return (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")


def _responsive() -> str:
    return (_FRONTEND / "app" / "styles" / "responsive.css").read_text(encoding="utf-8")


def test_mobile_starts_collapsed_and_desktop_starts_expanded() -> None:
    # Spec: initial state derives from the viewport — mobile hidden, desktop expanded.
    app = _app()
    assert "useState(() =>" in app, "initial state must be lazy (viewport-aware)"
    assert 'matchMedia("(max-width: 900px)")' in app


def test_breakpoint_change_listener_syncs_the_sidebar_state() -> None:
    # Edge: rotating/resizing across 900px mirrors state in BOTH directions, with cleanup.
    app = _app()
    assert 'addEventListener("change"' in app, "no matchMedia change listener"
    assert 'removeEventListener("change"' in app, "listener leak: no cleanup"
    assert "setSidebarCollapsed(event.matches)" in app


def test_mobile_collapsed_sidebar_is_display_none() -> None:
    # The complaint: collapsed on mobile still SHOWED a band. Now it hides.
    css = _responsive()
    match = re.search(r"(?m)^\s*\.sidebar\.is-collapsed\s*\{(.*?)\}", css, re.DOTALL)
    assert match is not None, "mobile .sidebar.is-collapsed rule missing from responsive.css"
    assert "display: none" in match.group(1), "collapsed must hide the menu on mobile"


def test_sidebar_is_never_unmounted() -> None:
    # Frozen law: collapse hides, it does not delete the aside.
    app = _app()
    assert "<aside" in app
    assert "sidebarCollapsed ? null" not in app


def test_outside_click_and_escape_still_close_the_menu() -> None:
    # Regression: the existing close paths must survive this slice untouched.
    app = _app()
    for required in (
        'addEventListener("mousedown"',
        ".contains(",
        "setSidebarCollapsed(true)",
        'event.key === "Escape"',
    ):
        assert required in app, f"close path missing: {required}"


def test_nav_link_tap_closes_the_open_menu_on_mobile() -> None:
    # Edge: navigating from the open mobile menu must not leave it open;
    # on desktop the tap must NOT force-collapse the rail (matchMedia check inside).
    app = _app()
    assert "onClick={closeOnMobile}" in app, "nav links do not close the mobile menu"
    assert "const closeOnMobile" in app
    close_block = app.split("const closeOnMobile", 1)[1].split("};", 1)[0]
    assert 'matchMedia("(max-width: 900px)")' in close_block, "desktop taps must be a no-op"
    assert "setSidebarCollapsed(true)" in close_block


def test_dead_mobile_collapsed_band_rules_are_deleted() -> None:
    # Dead-CSS law: display:none makes every collapsed-descendant rule in the <=900px
    # block unreachable. Allowed survivors in responsive.css are exactly two:
    # the display:none rule itself and the max-height desktop-rail compaction rule.
    css = _responsive()
    allowed = (".sidebar.is-collapsed", ".sidebar.is-collapsed .primary-nav a")
    for match in re.finditer(r"(?m)^\s*(\.sidebar\.is-collapsed[^,{]*)", css):
        selector = match.group(1).strip()
        assert selector in allowed, f"dead rule survived: {selector}"
