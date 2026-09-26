"""Spec: M10.4.5 Trader surfaces — Portfolio, Orders/Executions, Settings.

Field names are taken from the FROZEN contracts so the surface is ready for the real
endpoints without a redesign:

- Position / PortfolioState  -> financial_analyst_app/shared/contracts/portfolio.py
- Order / OrderIntent        -> shared/contracts/order.py
- Fill / ExecutionResult     -> shared/contracts/execution.py
- TradePlan                  -> shared/contracts/trade_plan.py
- RiskDecision               -> shared/contracts/risk.py
- BaseContract lineage       -> shared/contracts/base.py
- account two-gate fields    -> docs/specs/m1.3_account_mode_two_gate.spec.md
- risk limit names           -> shared/core/config.py
- order state machine        -> modules/06_M6_Execution.md

All values are mock. Money, margin, exposure and risk percentages are backend-owned
(frontend_spec.md:61) and are labelled as such on screen.
"""

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FRONTEND = _REPO / "frontend"

_APP_MODULES = (
    "App.tsx",
    "MarketPanel.tsx",
    "HomeDashboard.tsx",
    "TradePane.tsx",
    "MarketsGrid.tsx",
    "ResearchChat.tsx",
    "PortfolioSurface.tsx",
    "OrdersSurface.tsx",
    "SettingsSurface.tsx",
    "surface_ui.tsx",
    "TradingTicket.tsx",
)


def _app_source() -> str:
    return "\n".join((_FRONTEND / "app" / name).read_text(encoding="utf-8") for name in _APP_MODULES)


def _styles() -> str:
    parts = [(_FRONTEND / "app" / "styles.css").read_text(encoding="utf-8")]
    parts += [(_FRONTEND / "app" / "styles" / p).read_text(encoding="utf-8") for p in sorted(p.name for p in (_FRONTEND / "app" / "styles").glob("*.css"))]
    return "\n".join(parts)


def test_surfaces_mirror_the_frozen_contract_field_names() -> None:
    data = (_FRONTEND / "app" / "mock_trader_data.ts").read_text(encoding="utf-8")
    # Position + PortfolioState (portfolio.py:11-35)
    for field in (
        "symbol:",
        "qty:",
        "avg_entry:",
        "unrealized_pnl:",
        "realized_pnl:",
        "last_trade_plan_id:",
        "cash:",
        "equity:",
        "margin_used:",
        "margin_available:",
        "leverage:",
        "exposure:",
    ):
        assert field in data, f"missing contract field {field}"
    # Order + lineage (order.py:33-59, base.py:53-68)
    for field in (
        "order_id:",
        "client_order_id:",
        "trade_plan_id:",
        "side:",
        "order_type:",
        "price:",
        "stop_loss:",
        "take_profit:",
        "state:",
        "broker_capability_checked:",
        "correlation_id:",
        "trace_id:",
        "timestamp:",
    ):
        assert field in data, f"missing contract field {field}"
    # Fill + ExecutionResult (execution.py:14-39)
    for field in ("fill_id:", "commission:", "avg_fill_price:", "latency_ms:", "slippage_bps:", "broker_order_id:"):
        assert field in data, f"missing contract field {field}"
    # Account two-gate status (m1.3 spec:84-89)
    for field in ("mode:", "auto:", "source:", "auto_source:", "live_armed:", "bot_will_trade:"):
        assert field in data, f"missing account field {field}"
    # Risk limit names (config.py:38-41)
    for field in ("max_exposure_per_symbol:", "max_leverage:", "min_free_margin:", "max_drawdown:"):
        assert field in data, f"missing limit field {field}"


def test_surfaces_are_role_gated_and_viewer_sees_nothing_private() -> None:
    app = (_FRONTEND / "app" / "App.tsx").read_text(encoding="utf-8")
    nav = (_FRONTEND / "app" / "nav_config.ts").read_text(encoding="utf-8")
    home = (_FRONTEND / "app" / "HomeDashboard.tsx").read_text(encoding="utf-8").lower()

    for route in ('path="/app/portfolio"', 'path="/app/orders"', 'path="/app/settings"'):
        assert route in app
    # Viewer is redirected to the access preview on every private route.
    assert app.count('role === "Viewer" ? <AccessPreview role={role} />') >= 6
    # Viewer navigation carries no private surface. Sliced to the Viewer array only —
    # a slice-to-end-of-file would sweep up ACCESS_ROLES and the comments after it.
    viewer_block = nav.split("Viewer: [", 1)[1].split("],", 1)[0]
    for private in ("portfolio", "orders", "settings", "risk", "kill", "admin"):
        assert private not in viewer_block.lower()
    # The public Viewer dashboard mentions nothing private.
    for private in ("portfolio", "order", "risk", "position", "ledger", "pnl"):
        assert private not in home


def test_orders_surface_shows_the_state_machine_and_the_trade_chain() -> None:
    orders = (_FRONTEND / "app" / "OrdersSurface.tsx").read_text(encoding="utf-8")
    data = (_FRONTEND / "app" / "mock_trader_data.ts").read_text(encoding="utf-8")
    # Contract vocabulary (order.py:55-57) — all eight states present.
    for state in ("pending", "submitted", "acked", "partially_filled", "filled", "cancelled", "rejected", "unknown"):
        assert state in data, f"missing order state {state}"
    assert "ORDER_STATES" in orders
    assert "UNKNOWN" in orders.upper()
    # Trade chain per integration proposal:572-581
    for link in ("TradePlan", "RiskDecision", "Order", "ExecutionResult", "Fill", "Ledger"):
        assert link in orders
    # Filled qty is derived from fills, not invented on the order (no such contract field).
    assert "fills" in orders
    assert "filled_qty:" not in data
    # Every state chip carries text, never colour alone.
    assert "stateClass" in orders
    assert "state-chip" in _styles()


def test_settings_surface_follows_the_two_gate_and_three_layer_model() -> None:
    settings = (_FRONTEND / "app" / "SettingsSurface.tsx").read_text(encoding="utf-8")
    # Two independent gates (account_mode.py:3-4, frontend_spec.md:191-194)
    assert "MODE" in settings
    assert "AUTO" in settings
    assert "live_armed" in settings
    assert "bot_will_trade" in settings
    # Three-layer override (System_Development_Manual.md:108-130)
    for layer in ("Automatic", "Your Override", "Reset to Default"):
        assert layer in settings
    assert "hard" in settings.lower() and "boundary" in settings.lower()
    # Kill switch: Trader read-only, never auto-resumes (AGENTS.md 3.3)
    assert "read-only" in settings.lower()
    assert "auto-resume" in settings.lower() or "never auto" in settings.lower()
    # Secrets are never displayed, for any role.
    for secret_word in ("api_token", "vault_key", "password", "deriv_token"):
        assert secret_word not in settings


def test_tables_are_dense_mono_and_show_provenance_before_price() -> None:
    surfaces = _app_source()
    styles = _styles()
    assert "data-table" in surfaces
    assert "mono" in surfaces
    assert 'title=' in surfaces
    # Density levels L1/L2/L3 (frontend_spec.md:269)
    for level in ("L1", "L2", "L3"):
        assert level in surfaces
    assert ".data-table" in styles
    assert ".mono" in styles
    assert "font-variant-numeric" not in styles.lower()
    # Backend-owned money is labelled, not computed client-side.
    assert "backend" in surfaces.lower()


def test_surfaces_are_mock_only_and_never_display_credentials() -> None:
    for name in ("OrdersSurface.tsx", "SettingsSurface.tsx", "PortfolioSurface.tsx", "surface_ui.tsx", "mock_trader_data.ts"):
        text = (_FRONTEND / "app" / name).read_text(encoding="utf-8").lower()
        for forbidden in ("fetch(", "websocket", "authorization", "bearer", "localstorage"):
            assert forbidden not in text, f"{name} must stay mock-only"
        for secret_word in ("api_token", "vault_key", "password", "private_key"):
            assert secret_word not in text
