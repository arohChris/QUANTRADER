from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FRONTEND = _REPO / "frontend"

# The stylesheet is split into ordered partials; tests read the assembled result
# so a rule can live in any partial without weakening the assertions.
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


def test_experiment_market_module_is_mock_only() -> None:
    path = _FRONTEND / "app" / "experiment_market.ts"
    assert path.is_file()
    text = path.read_text(encoding="utf-8").lower()
    assert "experiment" in text
    assert "fetch(" not in text
    assert "websocket" not in text
    assert "secret" not in text


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


def test_app_renders_experiment_chart_with_provenance() -> None:
    app = _read_app_source()
    assert "experiment-chart" in app
    assert "lightweight-charts" in app
    assert "createChart" in app
    assert "LineSeries" in app
    assert "as_of" in app
    assert "quality" in app


def test_access_bar_is_moved_to_the_footer() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    assert 'className="app-footer"' in app
    assert '"access-bar' in app
    assert app.index('className="primary-nav"') < app.index('"access-bar')
    assert app.index('className="app-footer"') < app.index('"access-bar')
    assert "</footer>" in app


def test_responsive_and_readable_text_tokens_are_defined() -> None:
    styles = _read_styles()
    for required in ("--text-sm", "--text-md", "--text-lg", "@media (max-width: 640px)", "@media (min-width: 900px)"):
        assert required in styles
    assert ".data-large" in styles


def test_trader_ticket_has_required_manual_inputs() -> None:
    path = _FRONTEND / "app" / "TradingTicket.tsx"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    for field in ("Instrument", "Side", "Order type", "Quantity", "Entry", "Stop loss", "Take profit", "Preview", "Confirm"):
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
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    styles = _read_styles()
    assert "sidebarCollapsed" in app
    assert "sidebarCollapsed ?" in app
    assert "aria-expanded" in app
    assert "Toggle navigation" in app
    assert "<aside" in app
    assert 'className="sidebar"' in app
    assert ".app-body.sidebar-collapsed" in styles
    nav_block = styles.split(".primary-nav {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column" in nav_block


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
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
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
    styles = _read_styles()
    assert 'className="app-header"' in app
    assert 'className="header-actions"' in app
    assert 'className="brand"' in app
    assert "<header" in app
    assert "status-strip" not in app
    topbar_block = styles.split(".app-header {", 1)[1].split("}", 1)[0]
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
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    styles = _read_styles()
    assert 'aria-label="Footer navigation"' in app
    assert 'className="back-to-top"' in app
    assert 'className="footer-legal"' in app
    assert "new Date().getFullYear()" in app
    footer_block = app.split('className="app-footer"', 1)[1].split("</footer>", 1)[0]
    assert footer_block.count('className="footer-group"') + footer_block.count('access-bar footer-group') >= 4
    assert app.index('className="back-to-top"') < app.index('className="footer-legal"')
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


def test_footer_uses_even_columns_and_consistent_rhythm() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    styles = _read_styles()
    assert 'className="footer-inner"' in app
    assert "ArrowUp" in app
    assert app.index('className="back-to-top"') < app.index('className="footer-main"')
    inner_block = styles.split(".footer-inner {", 1)[1].split("}", 1)[0]
    assert "max-width" in inner_block
    assert "margin: 0 auto" in inner_block
    nav_block = styles.split(".footer-nav {", 1)[1].split("}", 1)[0]
    assert "repeat(4" in nav_block
    legal_block = styles.split(".footer-legal {", 1)[1].split("}", 1)[0]
    assert "justify-content: space-between" in legal_block


def test_footer_has_no_brand_logo() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
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
    assert "trend" in grid.lower()
    assert "trend of the day" in grid.lower()
    assert "from high" in grid.lower()
    assert "changePct" in grid
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
    assert "as_of" in grid
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
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
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
    legal_css = styles.split(".footer-legal {", 1)[1].split("}", 1)[0]
    assert "border-top" not in legal_css
    group_link_css = styles.split(".footer-group a {", 1)[1].split("}", 1)[0]
    assert "text-decoration: none" in group_link_css
    legal_link_css = styles.split(".footer-legal-links a {", 1)[1].split("}", 1)[0]
    assert "text-decoration: none" in legal_link_css


def test_footer_carries_real_copy_not_just_links() -> None:
    import re

    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    styles = _read_styles()
    footer_block = app.split('className="app-footer"', 1)[1].split("</footer>", 1)[0]
    assert 'className="footer-story"' in footer_block
    assert ".footer-story" in styles
    assert ".story-beat" in styles
    words = re.sub(r"<[^>]+>", " ", footer_block).split()
    assert len(words) >= 40, f"footer copy is too thin: {len(words)} words"

    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    assert 'addEventListener("mousedown"' in app
    assert 'removeEventListener("mousedown"' in app
    assert ".contains(" in app
    assert 'setSidebarCollapsed(true)' in app
    assert 'event.key === "Escape"' in app


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
    trend_block = styles.split(".trend-value {", 1)[1].split("}", 1)[0]
    assert "font-size: 1rem" in trend_block
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
    assert "RESEARCH_COMMANDS" in chat
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


def test_research_commands_match_the_documented_command_language() -> None:
    chat = (_FRONTEND / "app" / "ResearchChat.tsx").read_text(encoding="utf-8")
    # Command language comes from frontend_spec.md §5 / ui_ux_design_plan.md §7.5.
    for command in ("/analyze", "/compare", "/show", "/open", "/find", "/explain", "/why", "/research", "/help"):
        assert f'command: "{command}' in chat, f"missing documented command {command}"
    assert '"Why is oil moving today?"' in chat or "why is" in chat.lower()
    assert "PRESET_QUESTIONS" in chat


def test_research_answers_carry_evidence_status_and_provenance() -> None:
    chat = (_FRONTEND / "app" / "ResearchChat.tsx").read_text(encoding="utf-8")
    styles = _read_styles()
    # frontend_spec.md: "No bare AI chat bubbles" + mandatory AI provenance footer.
    for token in ("COMPLETE", "UNVERIFIED", "INSUFFICIENT_CONTEXT"):
        assert token in chat
    assert "evidence" in chat.lower()
    assert "data cut" in chat.lower()
    assert "prompt v" in chat.lower()
    assert "challenge" in chat.lower()
    assert "deep research" in chat.lower()
    assert ".evidence-bar" in styles
    assert ".answer-provenance" in styles
    # Read-only: the assistant never offers an execution action.
    lowered = chat.lower()
    for forbidden in ("place order", "confirm order", "execute trade"):
        assert forbidden not in lowered


def test_footer_tells_a_concise_story_not_filler() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    styles = _read_styles()
    footer_block = app.split('className="app-footer"', 1)[1].split("</footer>", 1)[0]
    assert 'className="footer-story"' in footer_block
    assert ".footer-story" in styles
    for beat in ("Built", "Challenge", "Need"):
        assert beat in footer_block
    # The old per-group essay paragraphs are gone: the footer carries the story instead.
    assert "footer-group-note" not in footer_block
    import re

    words = re.sub(r"<[^>]+>", " ", footer_block).split()
    assert len(words) <= 220, f"footer copy is not concise: {len(words)} words"
    import json

    package = json.loads((_FRONTEND / "package.json").read_text(encoding="utf-8"))
    dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    assert "react-router-dom" in dependencies
    main = (_FRONTEND / "app" / "main.tsx").read_text(encoding="utf-8")
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    assert "BrowserRouter" in main
    for route in ('path="/"', 'path="/login"', 'path="/app"', 'path="/admin"'):
        assert route in app
    assert "Link" in app
    assert "window.location" not in app

def test_spa_routing_is_client_side() -> None:
    pass
