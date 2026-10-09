import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FRONTEND = _REPO / "frontend"

# The stylesheet is split into ordered partials; tests read the assembled result
# so a rule can live in any partial without weakening the assertions.
# Read the DIRECTORY, not a hand-kept list: a hardcoded list silently omitted
# surfaces.css / admin.css / commands.css, which made every style assertion blind to
# those three surfaces. Globbing cannot fall behind a new file.
def _style_parts() -> list[str]:
    return sorted(path.name for path in (_FRONTEND / "app" / "styles").glob("*.css"))


_STYLE_PARTS = _style_parts()


_APP_MODULES = ("App.tsx", "MarketPanel.tsx", "HomeDashboard.tsx", "TradePane.tsx", "MarketsGrid.tsx", "TradingTicket.tsx")


def _read_app_source() -> str:
    """Whole app source across the split feature modules."""
    return "\n".join((_FRONTEND / "app" / name).read_text(encoding="utf-8") for name in _APP_MODULES)


def _read_styles() -> str:
    parts = [(_FRONTEND / "app" / "styles.css").read_text(encoding="utf-8")]
    parts += [(_FRONTEND / "app" / "styles" / name).read_text(encoding="utf-8") for name in _STYLE_PARTS]
    return "\n".join(parts)


# The footer is its own component (app/SiteFooter.tsx), not inline App markup, so the
# footer specs read it from there. App.tsx only renders <SiteFooter />.
_FOOTER = _FRONTEND / "app" / "SiteFooter.tsx"


def _footer_source() -> str:
    return _FOOTER.read_text(encoding="utf-8")


def _read(name: str) -> str:
    """One app file by name."""
    return (_FRONTEND / "app" / name).read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    """Body of one CSS rule. Fails loudly instead of raising IndexError on a typo.

    Anchored to the start of a line on purpose: a plain substring match also hits
    compound selectors, so `.markets-view-head h1` used to match the first line of
    `.theme-light .markets-view-head h1` and silently return the wrong rule.
    """
    match = re.search(rf"^\s*{re.escape(selector)}\s*\{{(.*?)\}}", css, re.DOTALL | re.MULTILINE)
    assert match is not None, f"no such CSS rule: {selector}"
    return match.group(1)


def _font_rem(css: str, block: str) -> float:
    """Font size in rem, resolving a `var(--type-*)` or legacy `var(--text-*)` token.

    Token definitions are read from tokens.css, not from `css` — the caller passes
    whichever partial holds the rule, and only tokens.css holds the scale. Resolving
    rather than skipping means a size that moved into a token is still checked, and a
    token that is referenced but never defined fails loudly.
    """
    literal = re.search(r"font-size:\s*([\d.]+)rem", block)
    if literal is not None:
        return float(literal.group(1))
    token = re.search(r"font-size:\s*var\((--[\w-]+)\)", block)
    assert token is not None, f"rule has no rem font-size: {block[:60]!r}"
    root = _rule((_FRONTEND / "app" / "styles" / "tokens.css").read_text(encoding="utf-8"), ":root")
    defined = re.search(rf"{re.escape(token.group(1))}:\s*([\d.]+)rem", root)
    if defined is not None:
        return float(defined.group(1))
    # A legacy alias, e.g. --text-sm: var(--type-caption). Follow it one hop.
    alias = re.search(rf"{re.escape(token.group(1))}:\s*var\((--[\w-]+)\)", root)
    assert alias is not None, f"undefined type token: {token.group(1)}"
    hop = re.search(rf"{re.escape(alias.group(1))}:\s*([\d.]+)rem", root)
    assert hop is not None, f"unresolved type alias: {alias.group(1)}"
    return float(hop.group(1))


def test_experiment_market_module_is_mock_only() -> None:
    path = _FRONTEND / "app" / "experiment_market.ts"
    assert path.is_file()
    text = path.read_text(encoding="utf-8").lower()
    assert "experiment" in text
    assert "fetch(" not in text
    assert "websocket" not in text
    assert "secret" not in text


def test_access_control_lives_at_the_end_of_the_sidebar() -> None:
    # Human directive 2026-09-27: the Access control moves from the footer to the END of
    # the sidebar, and the sidebar is redesigned around it. Role switching belongs next
    # to the navigation it changes — in the footer it was a control divorced from its
    # effect, at the opposite end of the page.
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    footer = _footer_source()
    shell = (_FRONTEND / "app" / "styles" / "shell.css").read_text(encoding="utf-8")

    # In the sidebar, and after the nav.
    assert 'className="sidebar-access"' in app
    sidebar = app.split("<aside", 1)[1].split("</aside>", 1)[0]
    assert sidebar.index('className="primary-nav"') < sidebar.index('className="sidebar-access"')
    # Out of the footer entirely, controls and all.
    assert "access-bar" not in footer
    assert "id=\"role\"" not in footer
    assert "theme-toggle" not in footer
    # Pinned to the end of a column flex, so it sits at the bottom on a short window.
    assert "display: flex" in _rule(shell, ".sidebar")
    assert "flex-direction: column" in _rule(shell, ".sidebar")
    assert "margin-top: auto" in _rule(shell, ".sidebar-access")
    assert "border-top" in _rule(shell, ".sidebar-access")
    # The rail stays icon-only: the control is hidden in the collapsed rail and revealed
    # on hover, exactly like the labels.
    assert "visibility: hidden" in _rule(shell, ".sidebar.is-collapsed .sidebar-access")
    assert "visibility: visible" in _rule(shell, ".sidebar:hover .sidebar-access")


def test_sidebar_groups_navigation_and_shows_the_active_role() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    shell = (_FRONTEND / "app" / "styles" / "shell.css").read_text(encoding="utf-8")
    nav = _read("nav_config.ts")

    # The config has always carried `group`; nothing rendered it. Group headings are the
    # single biggest readability win in a 9-item rail.
    assert "item.group" in app
    assert 'className="nav-group"' in app
    assert 'className="nav-group-label"' in app
    assert ".nav-group-label" in shell
    groups = {group for group in re.findall(r'group: "([A-Za-z ]+)"', nav)}
    assert len(groups) >= 4, f"expected real groups in the nav config, found {groups}"
    # Each group is labelled for assistive tech, so collapsing the heading on the rail
    # costs a screen-reader user nothing.
    assert 'role="group"' in app


def test_sidebar_access_shows_no_role_caption_or_label() -> None:
    # Human directive 2026-09-27: remove the "Viewing as Viewer" caption and the "Role"
    # label. The selected value in the control already says which role is active, so the
    # caption was a second, louder statement of the same fact.
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    shell = (_FRONTEND / "app" / "styles" / "shell.css").read_text(encoding="utf-8")
    block = app.split('className="sidebar-access"', 1)[1].split("</aside>", 1)[0]

    assert "Viewing as" not in block
    assert "sidebar-role" not in app
    assert "<label" not in block, "the visible Role label must be removed"
    # Removing a visible label must NOT remove the accessible name — a bare select is
    # unusable with a screen reader.
    assert 'aria-label="Role"' in block
    assert 'id="role"' in block
    # And the CSS for what was removed goes with it.
    assert ".sidebar-role" not in shell
    assert ".sidebar-access label" not in shell


def test_sidebar_is_static_pinned_and_never_scrolls() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    # Read the defining partials, not the assembled sheet. `responsive.css` also declares
    # `.sidebar` (the ≤900px horizontal band) and sorts before `shell.css`, so a
    # first-match lookup was asserting against the mobile override.
    shell = (_FRONTEND / "app" / "styles" / "shell.css").read_text(encoding="utf-8")
    responsive = (_FRONTEND / "app" / "styles" / "responsive.css").read_text(encoding="utf-8")
    sidebar_block = _rule(shell, ".sidebar")
    # Static: pinned to the viewport, viewport height, content clipped rather than scrolled.
    assert "position: sticky" in sidebar_block
    assert "calc(100dvh" in sidebar_block
    assert "overflow: hidden" in sidebar_block
    # Mobile: it becomes a full-width horizontal band rather than a rail.
    assert "position: static" in _rule(responsive, ".sidebar")
    # The rail is never removed from the DOM — collapse shrinks it, it does not delete it.
    assert "sidebarCollapsed ? null" not in app
    assert "<aside" in app
    assert '"sidebar is-collapsed"' in app
    # Frozen spec:114 — collapsed is a glyph rail that hover-expands.
    assert ".sidebar.is-collapsed" in shell
    assert "flex-basis: 2.6rem" in _rule(shell, ".sidebar.is-collapsed")
    assert "flex-basis: 15.5rem" in _rule(shell, ".sidebar.is-collapsed:hover")
    # Short windows compact the rail instead of clipping it.
    assert "@media (max-height: 700px)" in shell or "@media (max-height: 700px)" in responsive


def test_sidebar_closes_on_outside_click_without_using_the_toggle() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    assert 'addEventListener("mousedown"' in app
    assert 'removeEventListener("mousedown"' in app
    assert ".contains(" in app
    assert "setSidebarCollapsed(true)" in app
    assert 'event.key === "Escape"' in app


def test_experiment_market_changes_over_time_without_backend_streams() -> None:
    path = _FRONTEND / "app" / "experiment_market.ts"
    text = path.read_text(encoding="utf-8")
    for required in ("createExperimentMarket", "stepExperimentMarket", "timestamp", "sequence"):
        assert required in text
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    assert "useEffect" in app
    assert "setInterval" in app
    assert "WebSocket" not in app


def test_app_renders_experiment_chart_with_a_readable_legend() -> None:
    app = _read_app_source()
    assert "experiment-chart" in app
    assert "lightweight-charts" in app
    assert "createChart" in app
    assert "LineSeries" in app
    # AMENDED 2026-09-27 by human directive: the raw as_of/quality lineage strip under the
    # chart was deleted. The legend is the readable identification that replaced it.
    assert "series-legend" in app
    assert "as_of:" not in app
    assert "reference_snapshot_id:" not in app


def test_access_control_is_not_in_the_footer() -> None:
    # AMENDED 2026-09-27 by human directive: the Access control MOVED from the footer to
    # the end of the sidebar. The original assertion here required it in the footer, which
    # is the opposite of the current intent — a control that changes the whole page
    # belongs beside the navigation it changes, not at the far end of the page.
    footer = _footer_source()
    shell = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")

    assert 'className="app-footer"' in footer
    assert "</footer>" in footer
    # The role switcher and the theme toggle live in the rail, not the footer.
    assert "access-bar" not in footer
    assert "theme-toggle" not in footer
    assert 'className="sidebar-access"' in shell
    assert "primary-nav" in shell


# The app type scale (tokens.css). Seven named roles, smallest to largest. Every
# font-size in every app partial must reference one of these — no raw rem values —
# so "the standard size for this part" is a decision made once, in one file.
TYPE_SCALE = {
    "--type-micro": 0.7,
    "--type-caption": 0.78,
    "--type-body-sm": 0.86,
    "--type-body": 0.95,
    "--type-lead": 1.05,
    "--type-h3": 1.15,
    "--type-h1": 1.35,
}

def test_type_scale_is_defined_once_in_tokens() -> None:
    tokens = (_FRONTEND / "app" / "styles" / "tokens.css").read_text(encoding="utf-8")
    for name, value in TYPE_SCALE.items():
        assert f"{name}: {value}rem" in tokens, f"{name} must be defined in tokens.css as {value}rem"
    # Monotonic, so the scale cannot be edited into nonsense.
    values = [TYPE_SCALE[name] for name in TYPE_SCALE]
    assert values == sorted(values), f"the type scale is not monotonic: {values}"
    # The legacy names stay as aliases so nothing that referenced them breaks.
    for legacy, target in (("--text-sm", "--type-caption"), ("--text-md", "--type-body"), ("--text-lg", "--type-h3")):
        assert f"{legacy}: var({target})" in tokens, f"{legacy} must alias {target}"


def test_no_raw_font_sizes_survive_outside_the_scale() -> None:
    # The whole point: one scale, referenced by role. A raw rem in a partial is a new
    # one-off size, which is exactly the drift this replaces.
    offenders: list[str] = []
    for name in _STYLE_PARTS:
        css = (_FRONTEND / "app" / "styles" / name).read_text(encoding="utf-8")
        # Strip block comments first: prose legitimately talks about font-size.
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        for line in css.splitlines():
            stripped = line.strip()
            if "font-size" not in stripped:
                continue
            # The token definitions themselves are the one legitimate place for a value.
            if name == "tokens.css" and any(token in stripped for token in TYPE_SCALE):
                continue
            if "var(--type-" in stripped or "var(--text-" in stripped:
                continue
            offenders.append(f"{name}: {stripped}")
    assert not offenders, "raw font-sizes outside the scale:\n" + "\n".join(offenders)


def test_each_part_uses_the_size_its_role_calls_for() -> None:
    # A representative part per role, so the scale is a mapping and not just a set.
    styles = _read_styles()
    expected = {
        # page and section titles
        ".markets-view-head h1": "--type-h1",
        ".surface-head h1": "--type-h3",
        ".market-card-head h3": "--type-body-sm",
        # body
        ".story-beat": "--type-body",
        # secondary body / links
        ".footer-group a": "--type-body-sm",
        # captions and micro labels
        ".footer-group .footer-heading": "--type-body-sm",
        ".trend-caption": "--type-caption",
        # micro
        ".heat-symbol": "--type-caption",
    }
    for selector, token in expected.items():
        block = _rule(styles, selector)
        assert f"font-size: var({token})" in block, f"{selector} should use {token}, got: {block[:70]!r}"


def test_responsive_and_readable_text_tokens_are_defined() -> None:
    styles = _read_styles()
    for required in ("--text-sm", "--text-md", "--text-lg", "@media (max-width: 640px)", "@media (min-width: 900px)"):
        assert required in styles
    assert ".data-large" in styles


def test_trader_ticket_has_required_manual_inputs() -> None:
    path = _FRONTEND / "app" / "TradingTicket.tsx"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    # Amendment m10.4.10 (logged in frontend/specs/m10.4.10_timeframe_dropdown.spec.md):
    # "Timeframe" added to the required manual inputs — the ticket gains the field.
    for field in ("Instrument", "Side", "Order type", "Quantity", "Entry", "Stop loss", "Take profit", "Timeframe", "Preview", "Confirm"):
        assert field in text


def test_trading_ticket_is_role_gated_and_mock_only() -> None:
    ticket = (_FRONTEND / "app" / "TradingTicket.tsx").read_text(encoding="utf-8").lower()
    app = _read_app_source()
    assert "tradingticket" in app.lower()
    assert "trader" in app.lower()
    assert "fetch(" not in ticket
    assert "websocket" not in ticket
    assert "x-role" not in ticket
    assert "secret" not in ticket


def test_responsive_trader_dashboard_stays_modular() -> None:
    for relative in ("app/styles.css", "app/App.tsx", "app/TradingTicket.tsx", "app/experiment_market.ts"):
        path = _FRONTEND / relative
        assert path.is_file()
        assert path.stat().st_size < 20_000


def test_trade_pane_is_separate_from_home_dashboard() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    home = (_FRONTEND / "app" / "HomeDashboard.tsx").read_text(encoding="utf-8")
    trade = (_FRONTEND / "app" / "TradePane.tsx").read_text(encoding="utf-8")
    assert "HomeDashboard" in app
    assert "TradePane" in app
    assert 'path="/app/trading"' in app
    assert "HomeDashboard" in home
    assert "TradingTicket" in trade
    assert "ViewerDashboard" not in app


def test_sidebar_toggle_hides_sidebar_and_menu_is_vertical() -> None:
    import re

    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    styles = _read_styles()
    assert "sidebarCollapsed" in app
    assert "sidebarCollapsed ?" in app
    assert "aria-expanded" in app
    assert "Toggle navigation" in app
    assert "<aside" in app
    assert "is-collapsed" in app
    assert ".app-body.sidebar-collapsed" in styles
    nav_blocks = re.findall(r"\.primary-nav \{[^}]*\}", styles)
    assert any("flex-direction: column" in block for block in nav_blocks), "nav must stack vertically"


def test_theme_switch_defaults_to_dark_with_light_palette() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    styles = _read_styles()
    assert 'useState<Theme>("dark")' in app
    assert "Toggle theme" in app
    assert "theme-${theme}" in app
    assert "theme-light" in app
    assert ".theme-light" in styles
    assert "color-scheme" in styles


def test_footer_looks_like_a_real_site_footer_with_link_groups() -> None:
    app = _footer_source()
    styles = _read_styles()
    assert 'className="app-footer"' in app
    assert "footer-group" in app
    assert "footer-heading" in app
    assert 'className="footer-nav"' in app
    assert app.index('className="back-to-top"') < app.index('className="footer-nav"')
    assert "min-height: 100vh" in styles
    assert "margin-top: auto" in styles
    assert ".footer-heading" in styles


def test_theme_is_applied_to_body_and_light_palette_is_coherent() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    styles = _read_styles()
    assert "document.body" in app
    assert 'classList.toggle("theme-light"' in app
    assert "color-scheme: light" in styles
    assert "color-scheme: dark" in styles
    light_block = styles.split(".theme-light {", 1)[1].split("}", 1)[0]
    for token in ("--bg:", "--text:", "--muted:", "--border:", "--panel:"):
        assert token in light_block


def test_dark_palette_uses_bright_accent_colors() -> None:
    app = (_FRONTEND / "app" / "MarketPanel.tsx").read_text(encoding="utf-8")
    styles = _read_styles()
    market = (_FRONTEND / "app" / "experiment_market.ts").read_text(encoding="utf-8")
    for accent in ("--accent-cyan", "--accent-violet", "--accent-lime", "--accent-amber", "--accent-magenta"):
        assert accent in styles
    assert app.count("addSeries(") >= 2
    assert "CHART_PALETTE" in app
    assert "reference" in market


def test_header_is_sticky_with_brand_and_icon_actions() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    assert 'className="app-header"' in app
    assert 'className="header-actions"' in app
    assert 'className="brand"' in app
    assert "<header" in app
    assert "status-strip" not in app
    # Read the defining partial, not the assembled sheet: responsive.css also declares
    # `.app-header {`, and alphabetical assembly put its media-query override first, so a
    # first-match split was asserting against the wrong rule.
    shell = (_FRONTEND / "app" / "styles" / "shell.css").read_text(encoding="utf-8")
    topbar_block = shell.split(".app-header {", 1)[1].split("}", 1)[0]
    assert "position: sticky" in topbar_block
    assert "z-index" in topbar_block


def test_market_toolbar_supports_multiple_assets() -> None:
    app = _read_app_source()
    panel = (_FRONTEND / "app" / "MarketPanel.tsx").read_text(encoding="utf-8")
    market = (_FRONTEND / "app" / "experiment_market.ts").read_text(encoding="utf-8")
    assert 'aria-label="Select assets"' in app
    assert "selectedSymbols" in app
    assert "aria-expanded" in panel
    assert "SERIES_COLORS" in panel
    assert "markets.map" in panel
    assert "withSymbol" in app
    assert market.count("MOCK_INSTRUMENTS") >= 2
    lowered = app.lower()
    assert "fetch(" not in lowered
    assert "websocket" not in lowered


def test_icon_buttons_use_lucide_with_accessible_names() -> None:
    import json

    package = json.loads((_FRONTEND / "package.json").read_text(encoding="utf-8"))
    assert "lucide-react" in {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    styles = _read_styles()
    assert 'from "lucide-react"' in app
    assert "NAV_ICONS" in app
    assert 'className="icon-button"' in app
    # accessible name lives on the button, never on the decorative icon
    for label in ("Toggle navigation", "Toggle theme"):
        assert f'aria-label="{label}"' in app
    assert "title=" in app
    icon_block = styles.split(".icon-button {", 1)[1].split("}", 1)[0]
    assert "min-width" in icon_block
    assert "min-height" in icon_block


def test_footer_follows_researched_sitemap_lite_pattern() -> None:
    app = _footer_source()
    styles = _read_styles()
    assert 'aria-label="Footer navigation"' in app
    assert 'className="back-to-top"' in app
    assert 'className="footer-copy"' in app
    assert "new Date().getFullYear()" in app
    footer_block = app.split('className="app-footer"', 1)[1].split("</footer>", 1)[0]
    # AMENDED 2026-09-27: was `>= 4` groups, and the `access-bar footer-group` term is
    # gone entirely — Access moved into the sidebar, and the Legal group with it. The
    # sitemap is Workspace, Build, Trading for beginners, plus the social/copyright row.
    assert footer_block.count('className="footer-group"') == 3
    assert app.index('className="back-to-top"') < app.index('className="footer-copy"')
    footer_block_css = styles.split(".app-footer {", 1)[1].split("}", 1)[0]
    assert "grid" in footer_block_css
    group_block = styles.split(".footer-group {", 1)[1].split("}", 1)[0]
    assert "line-height" in group_block


def test_light_theme_is_coherent_and_contrast_safe() -> None:
    styles = _read_styles()
    light = styles.split(".theme-light {", 1)[1].split("\n}", 1)[0]
    for token in (
        "--bg:",
        "--surface:",
        "--surface-strong:",
        "--text:",
        "--muted:",
        "--border:",
        "--focus-ring:",
        "--shadow-card:",
        "--accent-cyan:",
        "--accent-violet:",
        "--positive:",
        "--negative:",
    ):
        assert token in light
    dark = styles.split(":root {", 1)[1].split("\n}", 1)[0]
    for token in ("--focus-ring:", "--shadow-card:", "--positive:", "--negative:"):
        assert token in dark
    assert "color-scheme: light" in light
    assert "color-scheme: dark" in dark


def test_market_panel_has_asset_and_timeframe_selects() -> None:
    panel = (_FRONTEND / "app" / "MarketPanel.tsx").read_text(encoding="utf-8")
    app = _read_app_source()
    market = (_FRONTEND / "app" / "experiment_market.ts").read_text(encoding="utf-8")
    assert "MOCK_INSTRUMENTS" in panel
    assert "MOCK_TIMEFRAMES" in panel
    assert 'aria-label="Select assets"' in panel
    assert "asset-box" in panel
    assert 'aria-label="Select timeframe"' in panel
    assert "market-toolbar" in panel
    assert "clock" in panel
    for token in ("MOCK_INSTRUMENTS", "MOCK_TIMEFRAMES", "withSymbol", "withTimeframe", "TIMEFRAME_WINDOW"):
        assert token in market
    lowered = app.lower()
    assert "fetch(" not in lowered
    assert "websocket" not in lowered


def test_footer_uses_the_page_measure_and_grid_math_that_holds_on_small_screens() -> None:
    # Human directive 2026-09-26: use grid, align the bands to the same measure as the
    # content above, get the column math right, and remedy the small screen.
    tokens = (_FRONTEND / "app" / "styles" / "tokens.css").read_text(encoding="utf-8")
    css = (_FRONTEND / "app" / "styles" / "footer.css").read_text(encoding="utf-8")
    responsive = (_FRONTEND / "app" / "styles" / "responsive.css").read_text(encoding="utf-8")

    # One page measure, declared once in tokens.css. The footer must not invent its own.
    assert "--page-measure:" in tokens
    assert "--footer-measure" not in css, "the footer still declares a private measure"
    assert "var(--page-measure)" in css
    # Every band is grid and every band is centred on that measure.
    for band in (".footer-inner", ".footer-story", ".footer-nav", ".footer-base"):
        block = _rule(css, band)
        assert "display: grid" in block, f"{band} is not a grid"
        assert "var(--page-measure)" in block
        assert "margin: 0 auto" in block

    # The footer's outer padding matches the surfaces above it, so the band edges line
    # up with the content band edges instead of sitting inset from them.
    surfaces = (_FRONTEND / "app" / "styles" / "surfaces.css").read_text(encoding="utf-8")
    base = _rule(css, ".app-footer")
    assert "padding: 2.75rem 1.25rem 1.75rem" in base
    assert "padding: 1.25rem" in _rule(surfaces, ".trader-surface")

    # Column math that cannot overflow a phone: minmax() floors must be capped at 100%.
    # `minmax(16rem, 1fr)` alone overflows below 16rem + padding.
    unguarded = re.findall(r"minmax\((\d+(?:\.\d+)?)rem,\s*1fr\)", css)
    assert not unguarded, f"minmax floors overflow a small screen: {unguarded}"
    assert "minmax(min(" in css

    # The single-column `.footer-main` wrapper was a dead layer around `.footer-nav`.
    assert ".footer-main" not in css, "the dead .footer-main grid layer must be removed"
    assert 'className="footer-main"' not in _footer_source()

    # Small screens: the base row stacks with the nav, not only at the narrowest step.
    wide = responsive.split("@media (max-width: 900px)", 1)
    assert len(wide) > 1, "no 900px breakpoint block"
    assert ".footer-base" in wide[1]
    narrow = responsive.split("@media (max-width: 640px)", 1)
    assert len(narrow) > 1, "no 640px breakpoint block"
    narrow_block = narrow[1]
    for selector in (".app-footer", ".footer-nav", ".footer-base", ".footer-story"):
        assert selector in narrow_block, f"{selector} has no small-screen treatment"


def test_chart_provenance_strips_are_removed() -> None:
    # Human directive 2026-09-27: delete the `as_of / quality / reference_snapshot_id`
    # strip under the chart (HomeDashboard and TradePane). The raw lineage triple is
    # machine output — it belongs in the audit trail, not under a chart a human is
    # reading. Lineage is still carried on the contracts and shown where it qualifies a
    # specific claim.
    home = _read("HomeDashboard.tsx")
    trade = _read("TradePane.tsx")
    styles = _read_styles()

    for name, source in (("HomeDashboard", home), ("TradePane", trade)):
        assert "as_of: " not in source, f"{name} still renders the raw as_of"
        assert "quality: " not in source, f"{name} still renders the quality flag"
        assert "reference_snapshot_id: " not in source, f"{name} still renders the snapshot id"
        assert "chart-footer" not in source, f"{name} still renders the provenance strip"
    assert ".chart-footer" not in styles, "chart-footer CSS is now dead and must be deleted"
    # The legend underneath it stays — that is the readable part.
    assert "series-legend" in home
    # The fields are still on the data model; only the rendering went.
    assert "referenceSnapshotId" in (_read("experiment_market.ts"))


def test_asset_picker_shows_the_asset_names_not_a_count() -> None:
    # Human directive 2026-09-27: the trigger read "1 selected · 12 available", which
    # told the reader how many things existed but not which one they were looking at. It
    # now names the selection; the count is not shown at all.
    panel = _read("MarketPanel.tsx")
    trigger = panel.split('aria-label="Select assets"', 1)[1].split("</button>", 1)[0]

    assert "selected ·" not in trigger
    assert "available" not in trigger
    assert "selectedSymbols.length" not in trigger
    # The names come from the catalogue labels, not the raw symbol, and a multi-selection
    # still reads as a list rather than collapsing to one name.
    assert "assetTriggerNames" in panel
    assert "MOCK_INSTRUMENTS.length" not in trigger


def test_market_toolbar_has_no_mock_path_chip() -> None:
    # Human directive 2026-09-27: delete "mock path: drawdown" entirely. It described
    # the mock generator's internal phase, not the market, and the phase is already
    # visible as the shape of every line on the chart.
    panel = _read("MarketPanel.tsx")
    styles = _read_styles()

    assert "mock path" not in panel
    assert "phase-chip" not in panel
    # The label is gone, so the chip and its colour variant are dead CSS.
    assert ".phase-chip" not in styles
    assert ".is-drawdown" not in styles
    # The generator's phase is still computed for the charts that colour by it.
    assert "pathPhase" in panel


def test_market_toolbar_has_no_as_of_or_window_readout() -> None:
    # Human directive 2026-09-27: remove the raw `as_of <iso timestamp>` and
    # `window 12 pts` lines from the market toolbar. They were implementation detail
    # leaking into the chrome: a wall-clock ISO string is unreadable at a glance, and a
    # point count describes the mock's internals rather than anything about the market.
    panel = _read("MarketPanel.tsx")
    toolbar = panel.split("market-toolbar", 1)[1].split("</div>\n  );", 1)[0]

    assert "as_of {" not in toolbar
    assert "window {" not in toolbar
    assert "pts" not in toolbar
    # Freshness is still stated once, in a readable form, by the session clock.
    assert 'className="clock"' in toolbar
    assert "UTC" in toolbar
    # The point count still drives the chart; only the readout went.
    assert "TIMEFRAME_WINDOW" in panel
    assert "windowSize" in panel


def test_footer_carries_no_legal_links_and_no_broker_disclaimer() -> None:
    # Human directive 2026-09-27: delete "Privacy / Terms / Status" and the
    # "no broker connection, no trade is submitted" sentence from the footer.
    footer = _footer_source()
    styles = _read_styles()
    block = footer.split('className="app-footer"', 1)[1].split("</footer>", 1)[0]

    for gone in ("/legal/privacy", "/legal/terms", "/legal/status", "Privacy", "Terms", "Status"):
        assert gone not in block, f"{gone!r} must be removed from the footer"
    for gone in ("no broker connection", "no trade is submitted", "no broker connected", "Mock data", "Legal"):
        assert gone not in block, f"{gone!r} must be removed from the footer"

    # The attribution line stays — it is the copyright, not the disclaimer.
    assert "© " in block
    assert "QUANTRADER" in block
    assert "Research build" in block

    # The CSS for what was removed goes with it, rather than lingering as a dead rule.
    assert ".footer-legal-links" not in styles, "legal-link CSS is now dead and must be deleted"
    assert ".footer-legal" not in styles, "the legal strip is gone; its CSS must go too"
    # The right half is now a copyright, not a legal strip, so it is renamed.
    assert ".footer-copy" in styles
    # One fewer group column: Workspace, Build, Trading for beginners.
    assert block.count('className="footer-group"') == 3


def test_footer_social_and_copy_are_opposite_and_equal_length() -> None:
    # Human directive 2026-09-26: the social tags and the right-hand block sit directly
    # opposite each other AND align in length — a mirrored pair of equal halves, not two
    # loose stacks. (Renamed from "legal" on 2026-09-27 when the legal links were removed
    # and the right half became a plain copyright.)
    app = _footer_source()
    css = (_FRONTEND / "app" / "styles" / "footer.css").read_text(encoding="utf-8")

    assert 'className="footer-base"' in app
    # Slice to the end of the footer: the first `</div>` closes the nested social block,
    # which sits before the legal block, so a `</div>` token would cut the pair in half.
    base_markup = app.split('className="footer-base"', 1)[1].split("</footer>", 1)[0]
    # Order matters: social first (left half), legal second (right half).
    assert base_markup.index('className="footer-social"') < base_markup.index('className="footer-copy"')
    assert app.count('className="footer-copy"') == 1

    base = css.split(".footer-base {", 1)[1].split("}", 1)[0]
    # Equal halves is what makes them "align in length" as a mirrored pair.
    assert "display: grid" in base
    assert "grid-template-columns: 1fr 1fr" in base
    assert "align-items: center" in base
    assert "max-width: var(--page-measure)" in base

    # Each half is full width, and neither may claim a private fixed width.
    for half in (".footer-social", ".footer-copy"):
        block = css.split(f"{half} {{", 1)[1].split("}", 1)[0]
        assert "width: 100%" in block, f"{half} must fill its half"
        assert "max-width:" not in block, f"{half} must not be shorter than its half"

    # Content hugs its own outer edge, so the pair reads as mirrored.
    social = css.split(".footer-social {", 1)[1].split("}", 1)[0]
    assert "justify-content: flex-start" in social
    assert "justify-self: start" in social
    copy = css.split(".footer-copy {", 1)[1].split("}", 1)[0]
    assert "justify-content: flex-end" in copy
    assert "justify-self: end" in copy

    # Narrow screens stack the halves instead of squeezing them to nothing.
    responsive = (_FRONTEND / "app" / "styles" / "responsive.css").read_text(encoding="utf-8")
    narrow = responsive.split("@media", 1)[-1]
    assert ".footer-base" in narrow
    assert "grid-template-columns: 1fr" in narrow


def test_footer_bands_share_one_measure_and_one_spacing_rhythm() -> None:
    # Human directive 2026-09-26 (follow-up): space the footer out correctly and align
    # the bands. The bands had five different measures (84/72/68/62rem) so the story sat
    # inset inside the nav on both sides, and centring the story broke its left accent
    # rule — both are alignment defects, not taste.
    css = (_FRONTEND / "app" / "styles" / "footer.css").read_text(encoding="utf-8")

    base = css.split(".app-footer {", 1)[1].split("}", 1)[0]
    for token in ("--footer-band-gap:", "--footer-group-gap:", "--footer-column-gap:"):
        assert token in base, f"footer has no spacing token {token}"

    # Every live band uses the shared measure. No band may hardcode its own.
    # `.footer-copy` is deliberately absent: it is the right HALF of `.footer-base`
    # and inherits the measure from it. `.footer-about` / `.footer-note` were dropped
    # as dead CSS — the markup never rendered them.
    assert not re.search(r"max-width:\s*\d+(\.\d+)?rem", css), "a band still hardcodes its own measure"
    for dead in (".footer-about", ".footer-note"):
        assert dead not in css, f"{dead} is dead CSS and must be deleted"
    for band in (".footer-story", ".footer-nav", ".footer-base"):
        block = _rule(css, band)
        assert "var(--page-measure)" in block, f"{band} does not use the shared measure"

    # One rhythm: bands separated by the band gap, groups by the group gap.
    inner = css.split(".footer-inner {", 1)[1].split("}", 1)[0]
    assert "gap: var(--footer-band-gap)" in inner
    group = css.split(".footer-group {", 1)[1].split("}", 1)[0]
    assert "gap: var(--footer-group-gap)" in group
    nav = css.split(".footer-nav {", 1)[1].split("}", 1)[0]
    assert "var(--footer-column-gap)" in nav

    # The story keeps its left accent rule, so its text stays left-aligned inside equal
    # columns. Centring per-column is what put the cyan borders at ragged positions.
    story = css.split(".footer-story {", 1)[1].split("}", 1)[0]
    assert "justify-items: stretch" in story
    assert "justify-items: center" not in story
    beat = css.split(".story-beat {", 1)[1].split("}", 1)[0]
    assert "text-align: left" in beat
    assert "border-left" in beat

    # A select must not blow out its column next to shrink-to-fit links.
    control = css.split(".footer-group select,", 1)[1].split("}", 1)[0]
    assert "max-width: 100%" in control
    group_min = css.split(".footer-group {", 1)[1].split("}", 1)[0]
    assert "min-width: 0" in group_min


def test_footer_centers_itself_and_carries_scale_and_weight() -> None:
    # Human directive 2026-09-26: the footer centres itself responsively, the text is
    # larger, and it carries strength (weight) instead of sitting at muted-thin.
    styles = _read_styles()

    inner = styles.split(".footer-inner {", 1)[1].split("}", 1)[0]
    assert "margin: 0 auto" in inner
    assert "justify-items: center" in inner

    # A fixed 4-column grid cannot be "even" now the footer carries five groups
    # (Access, Workspace, Build, Trading for beginners, Legal) — the fifth was
    # wrapping into a quarter-width orphan. Flowing columns fix it at every width.
    nav = styles.split(".footer-nav {", 1)[1].split("}", 1)[0]
    assert "auto-fit" in nav
    assert "minmax(" in nav
    assert "repeat(4" not in nav
    assert "justify-items: center" in nav

    group = styles.split(".footer-group {", 1)[1].split("}", 1)[0]
    assert "align-items: center" in group
    assert "align-items: flex-start" not in group
    assert "text-align: center" in group
    assert "font-weight: 500" in group

    back = styles.split(".back-to-top {", 1)[1].split("}", 1)[0]
    assert "justify-self: center" in back

    base = styles.split(".app-footer {", 1)[1].split("}", 1)[0]
    assert "font-weight: 500" in base
    assert "text-align: center" in base

    # Scale: nothing in the footer itself may sit on the old 0.7rem micro floor.
    # Scoped to footer.css — the assembled sheet continues into later partials whose
    # sizes are governed by their own slices.
    footer_css = (_FRONTEND / "app" / "styles" / "footer.css").read_text(encoding="utf-8")
    micro = re.findall(r"font-size:\s*(0\.6\d|0\.7\d)rem", footer_css)
    assert not micro, f"footer text still on the micro floor: {micro}"
    # Floors are expressed as type-scale steps, not magic numbers: a heading may never be
    # smaller than secondary body text, and body may never be micro.
    for selector, floor in (
        (".footer-group .footer-heading", "0.86rem"),
        (".story-beat", "0.95rem"),
        (".footer-group a", "0.86rem"),
        (".footer-copy", "0.86rem"),
        (".footer-beginners", "0.86rem"),
        (".footer-social-chip", "0.86rem"),
    ):
        block = _rule(footer_css, selector)
        size = _font_rem(footer_css, block)
        assert size >= float(floor.removesuffix("rem")), f"{selector} is {size}rem, below {floor}"
        assert "font-weight" in block, f"{selector} carries no weight"


def test_footer_uses_even_columns_and_consistent_rhythm() -> None:
    app = _footer_source()
    styles = _read_styles()
    assert 'className="footer-inner"' in app
    assert "ArrowUp" in app
    assert app.index('className="back-to-top"') < app.index('className="footer-nav"')
    inner_block = styles.split(".footer-inner {", 1)[1].split("}", 1)[0]
    assert "max-width" in inner_block
    assert "margin: 0 auto" in inner_block
    nav_block = styles.split(".footer-nav {", 1)[1].split("}", 1)[0]
    # AMENDED 2026-09-26: was `repeat(4, ...)` — a fixed 4 cannot stay "even" now the
    # footer carries five groups, and a fixed column count is not responsive. The
    # "even columns" intent is now carried by `auto-fit` + `minmax`.
    assert "auto-fit" in nav_block
    assert "minmax(" in nav_block
    copy_block = styles.split(".footer-copy {", 1)[1].split("}", 1)[0]
    # AMENDED 2026-09-26, renamed 2026-09-27: the right half moved into `.footer-base`
    # beside the social tags as a mirrored pair, so the parent owns the columns and the
    # right half hugs its outer edge.
    base_block = styles.split(".footer-base {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: 1fr 1fr" in base_block
    assert "justify-content: flex-end" in copy_block


def test_footer_has_no_brand_logo() -> None:
    app = _footer_source()
    styles = _read_styles()
    assert "footer-brand" not in app
    footer_block = app.split('className="app-footer"', 1)[1].split("</footer>", 1)[0]
    assert 'className="brand-mark"' not in footer_block
    assert ".footer-brand" not in styles


def test_market_toolbar_shows_at_least_ten_assets_at_once() -> None:
    import re

    app = (_FRONTEND / "app" / "MarketPanel.tsx").read_text(encoding="utf-8")
    shell = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    market = (_FRONTEND / "app" / "experiment_market.ts").read_text(encoding="utf-8")
    catalogue = market.split("export const MOCK_INSTRUMENTS = [", 1)[1].split("] as const;", 1)[0]
    assert len(re.findall(r"\{\s*symbol:", catalogue)) >= 10
    colors = app.split("const SERIES_COLORS = [", 1)[1].split("];", 1)[0]
    assert len(re.findall(r"#[0-9a-f]{6}", colors, flags=re.IGNORECASE)) >= 10
    assert "MOCK_INSTRUMENTS.map" in app
    assert "selectedSymbols" in shell


def test_mock_price_follows_time_and_stays_in_drawdown() -> None:
    market = (_FRONTEND / "app" / "experiment_market.ts").read_text(encoding="utf-8")
    assert "function pricePathAt" in market
    assert "pricePathAt(" in market
    assert "drawdown" in market.lower()
    # The path is a function of the time index only, so elapsed time drives price.
    assert "BASE_POINTS" not in market
    assert "drawdownStart" in market
    # Switching asset rescales the same time path instead of resetting to a fixed shape.
    rescale_block = market.split("export function withSymbol", 1)[1].split("export function withTimeframe", 1)[0]
    assert "buildPoints(" in rescale_block
    assert "BASE_POINTS" not in rescale_block


def test_trade_pane_switches_between_auto_and_manual() -> None:
    app = (_FRONTEND / "app" / "TradePane.tsx").read_text(encoding="utf-8")
    assert "tradeMode" in app
    assert 'role="group"' in app
    assert "aria-label=\"Trading mode\"" in app
    assert "Auto" in app
    assert "Manual" in app
    # The mode decides whether the manual ticket is rendered at all.
    assert "TradingTicket" in app
    assert 'tradeMode === "manual"' in app
    assert "is-active" in app


def test_asset_selector_is_a_box_like_the_timeframe() -> None:
    panel = (_FRONTEND / "app" / "MarketPanel.tsx").read_text(encoding="utf-8")
    styles = _read_styles()
    # The chip grid is gone: assets are picked inside a boxed control, same as timeframe.
    assert "asset-chips" not in panel
    assert "asset-chip" not in panel
    assert 'className="asset-box"' in panel
    assert 'aria-label="Select assets"' in panel
    assert "aria-multiselectable" in panel
    assert "aria-expanded" in panel
    assert ".asset-box" in styles
    assert ".asset-box-list" in styles
    assert "asset-chip" not in styles
    assert ".toolbar-select" in styles


def test_markets_section_has_heatmap_and_ranked_leaderboard() -> None:
    grid = (_FRONTEND / "app" / "MarketsGrid.tsx").read_text(encoding="utf-8")
    styles = _read_styles()
    assert "heatmap" in grid.lower()
    assert "leaderboard" in grid.lower()
    assert "heat-tile" in grid
    assert "is-up" in grid and "is-down" in grid
    heat_block = styles.split(".markets-heatmap {", 1)[1].split("}", 1)[0]
    assert "auto-fill" in heat_block
    assert ".heat-tile" in styles
    assert ".leaderboard" in styles
    assert "rank" in grid.lower()
    # Breathing room: the grid and its cells are spaced out, not packed.
    grid_block = styles.split(".markets-grid {", 1)[1].split("}", 1)[0]
    assert "gap: 1.5rem" in grid_block
    card_block = styles.split(".market-card {", 1)[1].split("}", 1)[0]
    assert "gap: 0.9rem" in card_block
    view_block = styles.split(".markets-view {", 1)[1].split("}", 1)[0]
    assert "gap: 1.5rem" in view_block
    assert "padding: 1.5rem" in view_block


def test_asset_drawdown_is_reported_as_the_trend_of_the_day() -> None:
    grid = (_FRONTEND / "app" / "MarketsGrid.tsx").read_text(encoding="utf-8")
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    panel = (_FRONTEND / "app" / "MarketPanel.tsx").read_text(encoding="utf-8")
    # AMENDED 2026-09-26 (human directive, amendment round 11): the card no longer
    # carries a drawdown block or an as_of line. Direction is still reported, as the
    # short "trend is up / trend is down" caption. `from high` and the drawdown stat
    # were removed on purpose, so they are no longer required here.
    assert "trend is" in grid.lower()
    assert "changePct" in grid
    assert "from high" not in grid.lower()
    # The indexed section is gone from the card stats, the toolbar and the grid.
    assert "indexed" not in grid.lower()
    assert "indexed" not in app.lower()
    assert "indexed" not in panel.lower()
    assert "indexed" not in _read_styles().lower()


def test_markets_section_renders_at_least_ten_chart_dashboards() -> None:
    import re

    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    grid = (_FRONTEND / "app" / "MarketsGrid.tsx").read_text(encoding="utf-8")
    styles = _read_styles()
    market = (_FRONTEND / "app" / "experiment_market.ts").read_text(encoding="utf-8")

    assert ( _FRONTEND / "app" / "MarketsGrid.tsx").is_file()
    assert "MarketsGrid" in app
    assert 'path="/markets"' in app
    assert 'path="/app/markets"' in app
    # one card per catalogue entry, so the count follows the catalogue (>= 10)
    assert "MOCK_INSTRUMENTS.map" in grid
    assert "MarketCard" in grid
    catalogue = market.split("export const MOCK_INSTRUMENTS = [", 1)[1].split("] as const;", 1)[0]
    assert len(re.findall(r"\{\s*symbol:", catalogue)) >= 10
    # each dashboard owns a chart and shows what the market is doing
    assert "addSeries" in grid
    assert "pathPhase" in grid
    assert "withSymbol" in grid
    # AMENDED 2026-09-26 (human directive, round 11): the per-card as_of footer is gone.
    # Freshness now lives in one place — the ActiveTrading sections read the same clock.
    assert "as_of" not in grid
    grid_block = styles.split(".markets-grid {", 1)[1].split("}", 1)[0]
    assert "auto-fill" in grid_block
    assert "minmax" in grid_block
    lowered = grid.lower()
    for forbidden in ("fetch(", "websocket", "secret"):
        assert forbidden not in lowered


def test_light_mode_text_has_stronger_contrast_and_weight() -> None:
    styles = _read_styles()
    light = styles.split(".theme-light {", 1)[1].split("\n}", 1)[0]
    dark = styles.split(":root {", 1)[1].split("\n}", 1)[0]

    def channel(block: str, name: str) -> int:
        line = next(row for row in block.splitlines() if row.strip().startswith(f"--{name}:"))
        return int(line.split("#", 1)[1][:2], 16)

    # Light text must be darker than dark-mode text; more "power", not washed out.
    assert channel(light, "text") < channel(dark, "text")
    assert channel(light, "muted") < channel(dark, "muted")
    assert channel(light, "dim") < channel(dark, "dim")
    assert channel(light, "text") <= 0x0F
    # Light-mode borders must stay defined, not washed out.
    assert channel(light, "border") <= 0xC0
    # Heavier type in light mode, because light backgrounds wash out weight.
    assert "--text-weight-strong: 800" in light
    assert ".theme-light" in styles


def test_footer_uses_ten_page_not_its_own_surface() -> None:
    styles = _read_styles()
    footer_block = styles.split(".app-footer {", 1)[1].split("}", 1)[0]
    # Tonal footer: no fill, no card, no divider slab — it blends into the page.
    assert "background: var(--bg)" in footer_block
    assert "--surface-strong" not in footer_block
    assert "--shadow-card" not in footer_block
    assert "border-top" not in footer_block


def test_footer_has_no_surface_no_rule_and_no_entry_route_links() -> None:
    app = _footer_source()
    styles = _read_styles()
    footer_block = app.split('className="app-footer"', 1)[1].split("</footer>", 1)[0]

    # No entry-route links in the footer: no /login, /app, /admin path links.
    assert "ENTRY_ROUTES" not in app
    for route in ('to="/login"', 'to="/app"', 'to="/admin"'):
        assert route not in footer_block
    assert ">Surfaces<" not in footer_block

    # No surface, and no underlining rule anywhere in the footer.
    footer_css = styles.split(".app-footer {", 1)[1].split("}", 1)[0]
    assert "background: var(--bg)" in footer_css
    assert "border-top" not in footer_css
    assert "--surface-strong" not in footer_css
    assert "--shadow-card" not in footer_css
    copy_css = styles.split(".footer-copy {", 1)[1].split("}", 1)[0]
    assert "border-top" not in copy_css
    group_link_css = styles.split(".footer-group a {", 1)[1].split("}", 1)[0]
    assert "text-decoration: none" in group_link_css
    group_link_css = styles.split(".footer-group a {", 1)[1].split("}", 1)[0]
    assert "text-decoration: none" in group_link_css


def test_footer_carries_real_copy_not_just_links() -> None:
    import re

    app = _footer_source()
    styles = _read_styles()
    shell = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    footer_block = app.split('className="app-footer"', 1)[1].split("</footer>", 1)[0]
    assert 'className="footer-story"' in footer_block
    assert ".footer-story" in styles
    assert ".story-beat" in styles
    # The story copy is data (STORY), rendered by a map — not repeated JSX.
    assert "{STORY.map(" in footer_block
    words = re.sub(r"<[^>]+>", " ", footer_block).split()
    assert len(words) >= 40, f"footer copy is too thin: {len(words)} words"

    app = _footer_source()
    assert 'addEventListener("mousedown"' in shell
    assert 'removeEventListener("mousedown"' in shell
    assert ".contains(" in shell
    assert 'setSidebarCollapsed(true)' in shell
    assert 'event.key === "Escape"' in shell


def test_markets_format_prices_for_different_magnitudes_and_decimals() -> None:
    import re

    grid = (_FRONTEND / "app" / "MarketsGrid.tsx").read_text(encoding="utf-8")
    market = (_FRONTEND / "app" / "experiment_market.ts").read_text(encoding="utf-8")
    assert "formatPrice" in grid
    assert "decimals" in grid
    catalogue = market.split("export const MOCK_INSTRUMENTS = [", 1)[1].split("] as const;", 1)[0]
    entries = re.findall(r"\{\s*symbol:[^}]*\}", catalogue)
    assert len(entries) >= 10
    decimals = {int(m.group(1)) for m in (re.search(r"decimals:\s*(\d+)", entry) for entry in entries) if m}
    assert len(decimals) >= 3, "catalogue must mix decimal precisions"
    bases = [float(m.group(1)) for m in (re.search(r"base:\s*([\d.]+)", entry) for entry in entries) if m]
    assert max(bases) / min(bases) > 1000, "catalogue must span multiple magnitudes"
    # Prices are rendered through the formatter, never raw.
    assert "{trend.last}" not in grid
    assert "formatPrice(" in grid


def test_markets_section_uses_compact_sizes() -> None:
    styles = _read_styles()
    trend_block = _rule(styles, ".trend-value")
    assert "font-size: var(--type-lead)" in trend_block
    assert _font_rem(styles, trend_block) >= 1.0, "the headline number must stay emphatic"
    card_block = styles.split(".market-card {", 1)[1].split("}", 1)[0]
    assert "padding: 0.9rem" in card_block
    grid_block = styles.split(".markets-grid {", 1)[1].split("}", 1)[0]
    assert "minmax(16rem" in grid_block


def test_research_section_has_a_command_chat_with_preset_questions() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    chat = (_FRONTEND / "app" / "ResearchChat.tsx").read_text(encoding="utf-8")
    styles = _read_styles()

    assert (_FRONTEND / "app" / "ResearchChat.tsx").is_file()
    assert "ResearchChat" in app
    assert 'path="/app/research"' in app
    assert 'path="/research"' in app
    # Commands live inside Research for Trader/Admin only; Viewer keeps the plain chat.
    assert "withCommands" in app
    assert "withCommands={role !== \"Viewer\"}" in app
    assert "PRESET_QUESTIONS" in chat
    assert "buildContextReply" in chat
    assert "ChatMessage" in chat
    assert ".chat-" in styles
    assert ".command-chip" in styles
    # The assistant surfaces context blocks, not a wall of text.
    assert "context" in chat.lower()
    assert "as_of" in chat
    lowered = chat.lower()
    for forbidden in ("fetch(", "websocket", "secret", "orders", "kill"):
        assert forbidden not in lowered
    assert "mock" in lowered


def test_research_commands_come_from_the_single_registry() -> None:
    raw = (_FRONTEND / "app" / "ResearchChat.tsx").read_text(encoding="utf-8")
    analyst = (_FRONTEND / "app" / "research_analyst.ts").read_text(encoding="utf-8")
    registry = (_FRONTEND / "app" / "commands.ts").read_text(encoding="utf-8")
    # The 27-command registry (M10.4.8) is the ONE command language. The old
    # 9-command chip bar is gone: a second, different vocabulary in the same UI
    # contradicted the approved set (/show risk vs /risk, /open which is not in it).
    assert "RESEARCH_COMMANDS" not in raw + analyst
    assert "command-bar" not in raw
    assert 'command: "/analyze"' not in registry
    assert 'command: "/show"' not in registry
    assert 'command: "/open"' not in registry
    # Natural-language presets are not commands, so they stay.
    assert "PRESET_QUESTIONS" in analyst
    assert '"Why is oil moving today?"' in analyst
    # The chat still answers, and now reads the registry for /help.
    assert "buildContextReply" in raw
    assert 'from "./research_analyst"' in raw
    assert 'from "./commands"' in analyst


def test_viewer_has_no_access_to_research() -> None:
    # Human directive 2026-09-26: research is a tenant surface. Viewer keeps Markets and
    # the public dashboard, but the research workspace is behind the role.
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    nav = (_FRONTEND / "app" / "nav_config.ts").read_text(encoding="utf-8")

    viewer_block = nav.split("Viewer: [", 1)[1].split("],", 1)[0]
    assert "/research" not in viewer_block
    assert 'label: "Research"' not in viewer_block
    for role in ("Trader: [", "Admin: ["):
        block = nav.split(role, 1)[1].split("],", 1)[0]
        assert "/app/research" in block, f"{role} keeps research"

    # The public route is closed to Viewer rather than left open by default.
    public_route = app.split('path="/research"', 1)[1].split("<Route", 1)[0]
    assert 'role === "Viewer" ? <AccessPreview role={role} /> : <ResearchChat' in public_route
    # No Viewer-facing link points at it either.
    assert 'to="/research"' not in app


def test_research_answers_carry_evidence_status_and_provenance() -> None:
    chat = (_FRONTEND / "app" / "ResearchChat.tsx").read_text(encoding="utf-8")
    # The answer builder was split into its own module to hold ResearchChat.tsx under the
    # 20 kB modularity guard; the view renders what it returns.
    analyst = (_FRONTEND / "app" / "research_analyst.ts").read_text(encoding="utf-8")
    assert (_FRONTEND / "app" / "research_analyst.ts").is_file()
    assert "buildContextReply" in chat
    assert "from \"./research_analyst\"" in chat
    styles = _read_styles()
    # frontend_spec.md: "No bare AI chat bubbles" + mandatory AI provenance footer.
    for token in ("COMPLETE", "UNVERIFIED", "INSUFFICIENT_CONTEXT"):
        assert token in analyst
    assert "evidence" in analyst.lower()
    assert "data cut" in chat.lower()
    assert "prompt v" in chat.lower()
    assert "challenge" in chat.lower()
    assert "deep research" in chat.lower()
    assert ".evidence-bar" in styles
    assert ".answer-provenance" in styles
    # Read-only: the assistant never offers an execution action.
    lowered = (chat + analyst).lower()
    for forbidden in ("place order", "confirm order", "execute trade"):
        assert forbidden not in lowered


def test_footer_tells_a_concise_story_not_filler() -> None:
    import json
    import re

    app = _footer_source()
    styles = _read_styles()
    footer_block = app.split('className="app-footer"', 1)[1].split("</footer>", 1)[0]
    assert 'className="footer-story"' in footer_block
    assert ".footer-story" in styles
    # The three beats are DATA rendered by a map, so the copy lives above the JSX.
    # Asserted against the file, and the map is asserted against the rendered block.
    story_data = app.split("const STORY = [", 1)[1].split("] as const;", 1)[0]
    for beat in ("Built", "Challenge", "Need"):
        assert f'beat: "{beat}"' in story_data, f"missing story beat: {beat}"
    assert "{STORY.map(" in footer_block
    # The old per-group essay paragraphs are gone: the footer carries the story instead.
    assert "footer-group-note" not in footer_block

    # Count the copy where it actually lives — the STORY data plus the rendered block —
    # so moving copy into data cannot quietly escape the budget.
    words = re.sub(r"<[^>]+>", " ", f"{story_data} {footer_block}").split()
    assert len(words) <= 220, f"footer copy is not concise: {len(words)} words"

    package = json.loads((_FRONTEND / "package.json").read_text(encoding="utf-8"))
    dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    assert "react-router-dom" in dependencies
    main = (_FRONTEND / "app" / "main.tsx").read_text(encoding="utf-8")
    shell = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    assert "BrowserRouter" in main
    # Routes belong to the shell; the footer navigates with Link and owns no routing.
    for route in ('path="/"', 'path="/login"', 'path="/app"', 'path="/admin"'):
        assert route in shell, f"route moved out of the shell: {route}"
    assert "Link" in app
    assert "window.location" not in app
    assert 'path="/' not in app, "the footer must not declare its own routes"

def _channel(block: str, name: str) -> int:
    line = next(row for row in block.splitlines() if row.strip().startswith(f"--{name}:"))
    return int(line.split("#", 1)[1][:2], 16)


def _hex(block: str, name: str) -> str:
    line = next(row for row in block.splitlines() if row.strip().startswith(f"--{name}:"))
    return line.split("#", 1)[1][:6].lower()


def _relative_luminance(hex_colour: str) -> float:
    def channel(value: int) -> float:
        srgb = value / 255
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast(fg: str, bg: str) -> float:
    light, dark = sorted((_relative_luminance(fg), _relative_luminance(bg)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_footer_carries_social_channels_and_a_beginners_path() -> None:
    app = _footer_source()
    styles = _read_styles()
    footer_block = app.split('className="app-footer"', 1)[1].split("</footer>", 1)[0]

    # Human directive 2026-09-26: social channels and a beginners path in the footer.
    # The names live in config arrays at the top of App.tsx and are mapped into the
    # footer, so both the data and the wiring are asserted.
    for channel in ("Instagram", "Facebook", "TikTok"):
        assert f'"{channel}"' in app, f"missing social channel {channel}"
    assert "SOCIAL_CHANNELS.map" in footer_block
    assert "footer-social" in footer_block
    assert "Trading for beginners" in footer_block
    assert "BEGINNER_TOPICS.map" in footer_block
    for topic in ("What is a pip", "Position sizing", "Reading a candlestick"):
        assert f'"{topic}"' in app, f"missing beginner topic {topic}"

    # No account URL is invented. Until the human supplies the handles these render as
    # labelled, non-navigating items, so the build never ships a dead or wrong link.
    for domain in ("instagram.com", "facebook.com", "tiktok.com", "href=\"#\""):
        assert domain not in app, f"{domain} must not be fabricated"
    assert "pending" in footer_block.lower()
    # And they are not entry-route links, which the footer decision already excluded.
    for route in ('to="/login"', 'to="/app"', 'to="/admin"'):
        assert route not in footer_block

    assert ".footer-social" in styles
    assert ".footer-beginners" in styles
    # The word budget from the footer test still holds with the two new groups.
    import re

    assert len(re.sub(r"<[^>]+>", " ", footer_block).split()) <= 220


def test_dark_base_stays_navy_after_the_warm_theme_was_rejected() -> None:
    tokens = (_FRONTEND / "app" / "styles" / "tokens.css").read_text(encoding="utf-8")
    colours = (_FRONTEND / "design-system" / "foundations" / "colors.css").read_text(encoding="utf-8").lower()
    dark = tokens.split(":root {", 1)[1].split("\n}", 1)[0]

    # The navy base is retained on purpose: a warm near-black read consumer rather
    # than instrument-grade and did not fit a forex product (human, 2026-09-27).
    assert _hex(dark, "bg") == "070a14"
    assert _hex(dark, "panel") == "121a33"
    assert "#070a14" in colours and "#121a33" in colours
    # No brand accent was introduced by the rejected direction.
    assert "--accent-ember" not in tokens
    assert "--accent-cyan" in dark
    assert _hex(dark, "accent-cyan") == "22d3ee"
    # Charts and the brand mark stay on the cyan signal colour.
    for module in ("MarketPanel.tsx", "MarketsGrid.tsx"):
        assert "#22d3ee" in (_FRONTEND / "app" / module).read_text(encoding="utf-8")
    assert ".brand-mark" in _read_styles()
    # Contrast targets still hold on the retained palette.
    bg = _hex(dark, "bg")
    assert _contrast(_hex(dark, "text"), bg) >= 12
    assert _contrast(_hex(dark, "muted"), bg) >= 4.5
    assert _contrast(_hex(dark, "dim"), bg) >= 3
    assert _contrast(_hex(dark, "accent-cyan"), bg) >= 4.5
    assert _hex(dark, "text") != "ffffff", "pure white on near-black causes halation"


def test_spa_routing_is_client_side() -> None:

    pass


# --- Amendment round 11: markets text, active trading sections, overlap ---------
#
# Human directive 2026-09-26:
#   * remove the "N mock dashboards · as_of ... · X advancing / Y in drawdown" line
#   * remove the Leader / Laggard / Widest drawdown summary and put ACTIVE TRADING
#     SECTIONS in its place
#   * remove the per-card stat block (last / from high / day range / window) and the
#     "Trend of the day is down ... Panel N of 12 · as_of ..." prose
#   * the card states one thing instead: "trend is up" or "trend is down"
#   * the market chart and its cards were floating over each other; investigate
#   * touch the text sizes

_REMOVED_MARKETS_TEXT = (
    "mock dashboards",
    "advancing /",
    "Laggard",
    "Widest drawdown",
    "Leader <strong>",
    "market-card-stats",
    "market-card-note",
    "Panel {index + 1} of {total}",
    "as_of {new Date",
    "below the window high",
    "range-drawdown",
)


def test_removed_markets_text_is_gone() -> None:
    grid = (_FRONTEND / "app" / "MarketsGrid.tsx").read_text(encoding="utf-8")
    for text in _REMOVED_MARKETS_TEXT:
        assert text not in grid, f"{text!r} must be removed from the markets view"
    styles = _read_styles()
    for selector in (".market-card-stats", ".market-card-note", ".markets-summary", ".range-drawdown"):
        assert selector not in styles, f"{selector} is dead CSS and must be deleted"


def test_markets_head_is_a_title_only_with_no_status_line() -> None:
    grid = (_FRONTEND / "app" / "MarketsGrid.tsx").read_text(encoding="utf-8")
    head = grid.split("markets-view-head", 1)[1].split("</header>", 1)[0]
    assert "<h1>Markets</h1>" in head
    assert "base.asOf" not in head
    assert "advancing" not in head
    assert "muted" not in head, "the removed status line was a muted paragraph"


def test_active_trading_sections_replace_the_leader_laggard_summary() -> None:
    grid = (_FRONTEND / "app" / "MarketsGrid.tsx").read_text(encoding="utf-8")
    market = (_FRONTEND / "app" / "experiment_market.ts").read_text(encoding="utf-8")
    styles = _read_styles()

    assert "ActiveTrading" in grid
    assert "readSessions" in grid
    # Sections are keyed to the frozen AssetClass, never invented categories.
    for asset_class in ("forex", "crypto", "commodity"):
        assert f'"{asset_class}"' in market
    assert "readSessions" in market
    assert "UTC" in market, "session state must be read off the UTC clock, not asserted"
    section_block = styles.split(".active-trading {", 1)[1].split("}", 1)[0]
    assert "repeat(3" in section_block
    assert ".active-trading-card" in styles
    assert ".session-chip" in styles
    # Honest labelling: these are market sessions, not live trading positions.
    assert "session" in grid.lower()
    for forbidden in ("open position", "current position", "position size", "live order"):
        assert forbidden not in grid.lower()


def test_market_card_states_trend_up_or_down_instead_of_prose() -> None:
    grid = (_FRONTEND / "app" / "MarketsGrid.tsx").read_text(encoding="utf-8")
    assert "trend is {" in grid
    assert 'up ? "up" : "down"' in grid
    # The removed prose sentence, not the leaderboard heading which still ranks by day.
    assert "Trend of the day is" not in grid
    assert "of {total}" not in grid
    card = grid.split("market-card-trend", 1)[1].split("MarketSparkline", 1)[0]
    assert "trend-value" in card
    assert "trend-caption" in card


def test_market_chart_and_cards_no_longer_float_over_each_other() -> None:
    grid = (_FRONTEND / "app" / "MarketsGrid.tsx").read_text(encoding="utf-8")
    styles = _read_styles()

    # The old defect: the chart was built at 96px inside a 4.5rem (72px) box, so the
    # canvas bled over the card below. One constant now drives both sides.
    assert "SPARKLINE_HEIGHT_PX" in grid
    assert "height: SPARKLINE_HEIGHT_PX" in grid
    assert "height: 96" not in grid
    sparkline_block = styles.split(".market-sparkline {", 1)[1].split("}", 1)[0]
    # min-height is the pre-paint floor and is fine; a bare `height` here is the defect.
    assert "min-height: 4.5rem" in sparkline_block
    assert not re.search(r"^\s+height:", sparkline_block, re.MULTILINE)
    canvas_block = styles.split(".market-sparkline canvas {", 1)[1].split("}", 1)[0]
    assert "!important" not in canvas_block, "a forced canvas height fights the chart"
    # And the card clips, so a future size mistake cannot overlap the next card.
    card_block = styles.split(".market-card {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" in card_block
    assert "align-content: start" in card_block
    # Resize: the chart follows its column instead of stretching to 100%.
    assert "ResizeObserver" in grid
    assert "applyOptions" in grid
    # Range bar no longer stacks three absolutely positioned siblings on one track.
    range_block = styles.split(".range-bar {", 1)[1].split("}", 1)[0]
    assert "position: relative" in range_block
    assert styles.count(".range-bar >") >= 1


def test_markets_text_sizes_are_raised_off_the_micro_floor() -> None:
    styles = _read_styles()
    # AMENDED 2026-09-27: these assertions used to pin literal rem values (0.66 / 0.72 /
    # 0.74 / 1rem). Sizes now come from the shared type scale, so the assertion is on the
    # RESOLVED size — which still catches a regression, and now also catches one that
    # arrives via a token.
    for selector in (".market-card-alias", ".trend-caption", ".heat-symbol"):
        block = _rule(styles, selector)
        assert _font_rem(styles, block) >= 0.7, f"{selector} fell below the micro floor"
    assert _font_rem(styles, _rule(styles, ".leaderboard-row")) >= 0.7
    assert _font_rem(styles, _rule(styles, ".heat-value")) >= 1.0, "the headline number stays emphatic"
    # The ActiveTrading sections must not reintroduce micro type either.
    # `.active-trading-card` is a container and sets no size, so only its text parts count.
    for selector in (".session-chip", ".session-breadth", ".session-window"):
        assert _font_rem(styles, _rule(styles, selector)) >= 0.7, f"{selector} is micro type"

