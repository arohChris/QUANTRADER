"""M4.9 - approval UI in the Admin workspace: source-text locks. WRITTEN BEFORE CODE.

Spec: docs/specs/m4.9_approval_ui.spec.md (LGTM @you 2026-10-07 "just do a pro job").
Style: the established `frontend/tests` precedent (m10.4.x) - these tests read the
authored source as text, because the frontend suite has no renderer; they pin LAWS
(the service is the sole network choke point, actions derive from the frozen matrix,
the panel never fabricates rows), not markup layout.

They fail honestly until Step 5 wires the panel and service.
"""
from __future__ import annotations

import re
from pathlib import Path

_FRONTEND = Path(__file__).resolve().parents[1]
_REPO = _FRONTEND.parent
_APP = _FRONTEND / "app"
_PANEL = _APP / "AdminTradingSurface.tsx"
_APP_TSX = _APP / "App.tsx"
_SERVICE = _FRONTEND / "services" / "approval_api.ts"
_STORE_PY = (
    _REPO / "financial_analyst_app" / "shared" / "core" / "strategy_approval_store.py"
)

_STATES = ("draft", "testing", "approved", "rejected", "discontinued")


def _read(path: Path) -> str:
    assert path.exists(), f"{path.name} missing - slice not implemented yet"
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Collapse whitespace so prose assertions survive source line wrapping."""
    return " ".join(text.split())


# ---------------- service = the only network choke point ----------------

def test_service_exists_and_app_never_calls_fetch_or_the_endpoint_directly() -> None:
    service = _read(_SERVICE)
    assert "fetch(" in service
    assert "strategy-approvals" in service
    # No app component may know the endpoint path or call fetch itself - they go
    # through the service (market_data_api.ts law, restated for this endpoint).
    for py in _APP.glob("*.ts*"):
        text = py.read_text(encoding="utf-8")
        assert "strategy-approvals" not in text, f"{py.name} bypasses the service"
        if py.name != "main.tsx":
            # Word-boundary match: MarketChart.tsx legitimately calls `prefetch(`
            # (a cache-warm through market_data_api.ts), whose tail contains
            # `fetch(` - the naive substring assert was a false positive (amended
            # 2026-10-07, disclosed in spec section 9).
            assert not re.search(r"\bfetch\(", text), f"{py.name} calls fetch directly"


def test_admin_surface_imports_the_service() -> None:
    panel = _read(_PANEL)
    assert "../services/approval_api" in panel


def test_service_sends_role_and_correlation_headers_with_explicit_role_arg() -> None:
    service = _read(_SERVICE)
    assert '"X-Role"' in service
    assert '"X-Correlation-Id"' in service
    # Fail-closed: role is an explicit parameter, never a module-global.
    assert re.search(r"listApprovals\(\s*role\b", service)
    assert re.search(r"setApprovalState\(\s*role\b", service)


# ---------------- the matrix: frontend LEGAL_TRANSITIONS == backend _LEGAL ----------------

def _backend_legal() -> dict[str, set[str]]:
    text = _read(_STORE_PY)
    block = re.search(r"_LEGAL[^=]*=\s*\{(.*?)\n\}", text, re.S)
    assert block, "_LEGAL not found in the m4.8 store"
    pairs = re.findall(r'"(\w+)"\s*:\s*frozenset\(\{(.*?)\}\)', block.group(1), re.S)
    assert pairs, "_LEGAL body did not parse"
    return {key: set(re.findall(r'"(\w+)"', body)) for key, body in pairs}


def _frontend_legal() -> dict[str, set[str]]:
    text = _read(_PANEL)
    block = re.search(
        r"LEGAL_TRANSITIONS[^=]*=\s*\{(.*?)\n\};", text, re.S
    )
    assert block, "LEGAL_TRANSITIONS not found in the panel"
    pairs = re.findall(r"(\w+)\s*:\s*\[([^\]]*)\]", block.group(1))
    assert pairs, "LEGAL_TRANSITIONS body did not parse"
    return {key: set(re.findall(r'"(\w+)"', body)) for key, body in pairs}


def test_legal_transitions_parity_with_backend_matrix_is_exact() -> None:
    # The same law as m4.10 timeframe parity: cross-tree read is the parity LOCK.
    backend, frontend = _backend_legal(), _frontend_legal()
    assert set(backend) == set(frontend) == set(_STATES)
    assert backend == frontend, f"matrix drift: backend={backend} frontend={frontend}"


def test_action_buttons_derive_from_the_matrix_not_hardcoded_targets() -> None:
    panel = _read(_PANEL)
    assert "LEGAL_TRANSITIONS" in panel
    # The panel must map over the matrix for row actions; a hardcoded pair of
    # Approve/Reject buttons would silently miss legal targets like testing->draft.
    assert re.search(r"LEGAL_TRANSITIONS\s*\[\s*\w+(\.\w+)?\s*\]", panel)


# ---------------- honesty: no fabricated rows, unavailable is a real state ----------------

def test_panel_has_no_mock_strategy_rows() -> None:
    panel = _read(_PANEL)
    assert not re.search(r"MOCK_\w*STRATEG", panel), "panel must not invent strategy rows"
    # It may keep its unrelated imports from mock_admin_data (lineage/matrix only).
    import_line = re.search(r'import \{([^}]*)\} from "\./mock_admin_data"', panel)
    if import_line:
        assert not re.search(r"STRATEG", import_line.group(1).upper())


def test_panel_declares_honest_unavailable_state_with_banner_text() -> None:
    panel = _flat(_read(_PANEL))
    assert '"unavailable"' in panel, "503 must be a declared UI state"
    assert "approval store unavailable" in panel, "banner text missing"
    # Fabricating rows on failure = the M10.4.4 honesty violation; the state
    # machine must keep data separate from status.
    assert "loading" in panel and "ready" in panel


def test_every_transition_is_confirmed_before_send() -> None:
    panel = _read(_PANEL)
    assert re.search(r"\bconfirm\(", panel), "transitions must be confirmed"


def test_state_chips_cover_all_five_states() -> None:
    panel = _read(_PANEL)
    for state in _STATES:
        assert f'"{state}"' in panel, f"state chip missing: {state}"


def test_no_contract_note_removed_only_from_the_strategies_panel() -> None:
    # AMENDED 2026-10-07 (builder, disclosed in spec section 9 + the slice report):
    # the original test asserted `"NoContractNote" in panel` AND `not in Strategies
    # block`, but AdminTradingSurface.tsx held the note ONLY in its Strategies block -
    # self-contradictory the moment the contract landed. The honest law (spec section
    # 3 intent) is: the trading surface needs no note and carries no dead import,
    # its Strategies block is clean, and the still-uncontracted admin surfaces keep
    # theirs unchanged.
    panel = _read(_PANEL)
    assert "NoContractNote" not in panel, "dead import: the strategy contract exists"
    match = re.search(
        r'title="Strategies"(.*?)(?:title="|</section>)', panel, re.S
    )
    assert match, "Strategies panel block not found"
    assert "NoContractNote" not in match.group(1)
    # The other admin surfaces (spec section 8: they "stay mock, honest notes intact").
    for sibling in (
        "AdminAuditSurface.tsx",
        "AdminConfigurationSurface.tsx",
        "AdminKillSurface.tsx",
    ):
        text = (_APP / sibling).read_text(encoding="utf-8")
        assert "NoContractNote" in text, f"{sibling} must keep its honest note"


# ---------------- wiring ----------------

def test_app_passes_the_role_prop_into_the_surface() -> None:
    app = _read(_APP_TSX)
    assert "<AdminTradingSurface role={role}" in app


def test_route_gate_for_non_admin_is_untouched() -> None:
    # E12: routing logic belongs to no slice but the original one; assert the gate
    # still refuses everyone who is not Admin.
    app = _read(_APP_TSX)
    assert re.search(
        r'path="/admin/trading"\s*element=\{role !== "Admin"', app
    ), "admin route gate changed - M4.9 must not touch it"
