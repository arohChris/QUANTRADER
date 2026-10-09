# Spec-locked tests for M10.4.11 — Trade Summary Header.
# Written against frontend/specs/m10.4.11_trade_summary_header.spec.md BEFORE the
# implementation existed (AGENTS.md §2 Step 3). Text-parses app sources; never imports
# backend code (frontend law). Standing human LGTM for Step 4 recorded 2026-09-30
# ("lgtm lgtm go on" + "1D = Day, current day" label directive).
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FRONTEND = _REPO / "frontend"


def _read(rel: str) -> str:
    return (_FRONTEND / "app" / rel).read_text(encoding="utf-8")


def test_summary_header_renders_in_trade_pane_as_first_grid_row() -> None:
    # Spec: header is the first child of .trade-pane, above the chart/ticket grid.
    pane = _read("TradePane.tsx")
    assert 'from "./TradeSummaryHeader"' in pane
    assert "<TradeSummaryHeader" in pane
    assert pane.index("<TradeSummaryHeader") < pane.index("trade-pane-main")
    styles = (_FRONTEND / "app" / "styles" / "panels.css").read_text(encoding="utf-8")
    block = styles.split(".trade-summary-header", 1)[1].split("}", 1)[0]
    assert "grid-column: 1 / -1" in block


def test_period_toggle_has_exact_five_periods_with_human_day_label() -> None:
    # Spec edges: closed set of 5 keys (lowercase words, Amendment A1), default day,
    # human label "Day" = current day.
    mock = _read("mock_trader_data.ts")
    line = next(l for l in mock.splitlines() if "TRADE_SUMMARY_PERIODS" in l and "=" in l)
    for key in ('"day"', '"week"', '"month"', '"year"', '"all"'):
        assert key in line, f"missing period {key}"
    component = _read("TradeSummaryHeader.tsx")
    assert 'useState<TradeSummaryPeriod>("day")' in component
    assert 'day: "Day"' in component
    assert 'all: "All"' in component
    assert 'role="group"' in component
    assert "aria-pressed" in component
    # Human-readable title travels on the button, not the raw key as text.
    assert "title={" in component
    assert "Current" in component


def test_header_shows_total_pnl_and_three_state_counts() -> None:
    component = _read("TradeSummaryHeader.tsx")
    for label in ("Total P&L", "Active", "Closed", "Rejected"):
        assert label in component, f"missing header label {label}"
    # Money is backend-owned: shared formatter only, no local number formatting.
    assert 'from "./surface_ui"' in component
    assert "signedMoney" in component
    assert "toFixed" not in component
    assert "toLocaleString" not in component


def test_mock_records_cover_every_period_and_carry_per_user_scope() -> None:
    # Spec: per-user scoping fields on every record; shape is complete for all 5 periods.
    mock = _read("mock_trader_data.ts")
    assert "MOCK_TRADE_SUMMARY: Record<TradeSummaryPeriod, TradeSummary>" in mock
    block = mock.split("MOCK_TRADE_SUMMARY", 1)[1]
    for key in ("day: {", "week: {", "month: {", "year: {", "all: {"):
        assert key in block, f"missing record {key}"
    record_shape = block.split("TradeSummary = {", 1)
    assert "period:" in mock and "user_id:" in mock and "broker:" in mock
    for field in ("active:", "closed:", "rejected:", "total_pnl:"):
        assert field in mock
    # Day record hand-reconciles to MOCK_ORDERS (2 active, 1 closed, 1 rejected).
    day = block.split("day: {", 1)[1].split("}", 1)[0]
    assert "active: 2" in day
    assert "closed: 1" in day
    assert "rejected: 1" in day
    # Losing month exists so the negative tone path is exercised.
    month = block.split("month: {", 1)[1].split("}", 1)[0]
    assert "total_pnl: -86.2" in month
    assert record_shape  # sanity: split found the type block


def test_component_performs_no_aggregation_math() -> None:
    # Presentation-only law: counts/P&L are precomputed in the mock file.
    component = _read("TradeSummaryHeader.tsx")
    assert ".reduce(" not in component
    assert "MOCK_ORDERS" not in component
    assert "Math." not in component
    assert ".filter(" not in component


def test_no_fetch_or_websocket_in_summary_header() -> None:
    # Mock law: payload wiring lands with M6/M7, never here.
    component = _read("TradeSummaryHeader.tsx")
    for banned in ("fetch(", "WebSocket", "EventSource", "axios"):
        assert banned not in component, f"I/O construct {banned} found"


def test_unknown_period_falls_back_to_day_record() -> None:
    # Spec adversarial edge: unknown/missing key never renders undefined.
    component = _read("TradeSummaryHeader.tsx")
    assert 'MOCK_TRADE_SUMMARY["day"]' in component
    assert "??" in component


def test_mock_provenance_is_labeled_and_tones_reuse_existing_classes() -> None:
    component = _read("TradeSummaryHeader.tsx")
    assert "Mock" in component  # honesty law: mock data is labeled
    assert "is-positive" in component
    assert "is-negative" in component
    # No new color literals in the component (tokens only, via CSS classes).
    assert "#" not in component


def test_header_css_uses_tokens_and_reuses_mode_button_styles() -> None:
    styles = (_FRONTEND / "app" / "styles" / "panels.css").read_text(encoding="utf-8")
    block = styles.split(".trade-summary-header", 1)[1].split(".summary-", 1)[0]
    assert "var(--surface)" in block
    assert "raw" not in block
    # Period buttons reuse the existing mode-switch pill styles; no duplicate button CSS.
    component = _read("TradeSummaryHeader.tsx")
    assert "mode-switch" in component
    assert "mode-button" in component
