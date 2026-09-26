"""Spec: M10.4.4 Admin workspace — Trading, Risk, System, Audit, Configuration, Kill.

The Admin role had nine nav items of which five were dead ends falling through to
AccessPreview (App.tsx). This slice gives each one a real surface.

Authority for what Admin must see and do is NOT the manual — `M10.4.4` does not exist
in System_Development_Manual (0 matches). It is:
  - frontend/FRONTEND_BACKEND_INTEGRATION_PROPOSAL.md:111-125 (privilege scope),
    :743-762 (kill/resume), :764-776 (system control centre), :607-616 (config files)
  - frontend/specs/ui_ux_design_plan.md:404-418 (system status tree), :56-66 (matrix)

THE HONESTY RULE FOR THIS SLICE. Three of the six surfaces have frozen contracts and
three do NOT. Verified by repo-wide search of financial_analyst_app/**/*.py:
  - HAS a contract: RiskLimits/SystemConfig (shared/core/config.py), RiskDecision
    (shared/contracts/risk.py), HealthSnapshot/HealthStatus/EngineHealth/EngineState
    (shared/core/health.py, engine_interface.py), Lifecycle.health() (lifecycle.py),
    Environment (shared/core/environment.py), feature_flags.all_flags()
  - HAS NO CONTRACT: kill switch state, audit records, config_versions, and any typed
    Strategy. The only kill reference in the backend is the action string
    "risk.kill_switch" (shared/core/permissions.py:48). Those three surfaces therefore
    state the required shape and the missing backend, and MUST NOT invent plausible
    looking rows. The spec fails if they do.
"""

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_FRONTEND = _REPO / "frontend"
_APP = _FRONTEND / "app"


def _read(name: str) -> str:
    return (_APP / name).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Collapse whitespace so prose assertions survive source line wrapping."""
    return " ".join(text.split())


def _array_literal(data: str, name: str) -> str:
    """The `[...]` assigned to a named const.

    Slicing to the first `[` breaks on a `Type[]` annotation, and slicing to the first
    `]` breaks on the same thing, so match the assignment instead.
    """
    match = re.search(rf"{re.escape(name)}[^=\n]*=\s*(\[[^\]]*\])", data)
    assert match is not None, f"no array assignment for: {name}"
    return match.group(1)


def _rule(css: str, selector: str) -> str:
    marker = f"{selector} {{"
    assert marker in css, f"no such CSS rule: {selector}"
    return css.split(marker, 1)[1].split("}", 1)[0]


def _styles() -> str:
    parts = [(_FRONTEND / "app" / "styles.css").read_text(encoding="utf-8")]
    parts += [path.read_text(encoding="utf-8") for path in sorted((_APP / "styles").glob("*.css"))]
    return "\n".join(parts)


SURFACES = (
    "AdminTradingSurface",
    "AdminRiskSurface",
    "AdminSystemSurface",
    "AdminAuditSurface",
    "AdminConfigurationSurface",
    "AdminKillSurface",
)


def test_every_admin_surface_exists_and_is_routed() -> None:
    app = _read("App.tsx")
    for surface in SURFACES:
        assert (_APP / f"{surface}.tsx").is_file(), f"missing surface file {surface}.tsx"
        assert surface in app, f"{surface} is never routed"
    # One route per nav item, and Kill gets its own instead of aliasing Risk.
    for route in (
        'path="/admin/trading"',
        'path="/admin/risk"',
        'path="/admin/system"',
        'path="/admin/audit"',
        'path="/admin/configuration"',
        'path="/admin/kill"',
    ):
        assert route in app, f"missing admin route {route}"
    # Every nav-reachable admin route renders a real surface for Admin, with the access
    # preview only as the refusal branch — never as the surface itself.
    for route, surface in (
        ("trading", "AdminTradingSurface"),
        ("risk", "AdminRiskSurface"),
        ("system", "AdminSystemSurface"),
        ("audit", "AdminAuditSurface"),
        ("configuration", "AdminConfigurationSurface"),
        ("kill", "AdminKillSurface"),
    ):
        window = app.split(f'path="/admin/{route}"', 1)[1].split("<Route", 1)[0]
        assert surface in window, f"/admin/{route} does not render {surface}"
        assert 'role !== "Admin"' in window, f"/admin/{route} is not Admin-gated"
    # The catch-all may stay a stub: it is not reachable from the nav.
    assert 'path="/admin/*"' in app


def test_admin_nav_items_are_unique_and_reach_a_surface() -> None:
    nav = _read("nav_config.ts")
    admin = nav.split("Admin: [", 1)[1].split("\n  ],", 1)[0]
    # Kill no longer aliases Risk onto one route.
    assert admin.count('href: "/admin/risk"') == 1
    assert 'href: "/admin/kill"' in admin
    for label in ("Trading", "Risk", "System", "Audit", "Configuration", "Kill / Resume"):
        assert f'label: "{label}"' in admin, f"missing admin nav item {label}"
    # Icons for every admin route.
    icons = nav.split("NAV_ICONS", 1)[1].split("};", 1)[0]
    for route in ("/admin/risk", "/admin/system", "/admin/audit", "/admin/configuration", "/admin/kill"):
        assert f'"{route}"' in icons, f"no nav icon for {route}"


def test_admin_mock_data_mirrors_the_frozen_contracts() -> None:
    data = _read("mock_admin_data.ts")
    # RiskLimits / SystemConfig — shared/core/config.py:34-52
    for field in (
        "max_exposure_per_symbol:",
        "max_leverage:",
        "min_free_margin:",
        "max_drawdown:",
        "base_currency:",
        "clock:",
        "log_level:",
    ):
        assert field in data, f"missing config field {field}"
    # RiskDecision — shared/contracts/risk.py:21-30
    for field in (
        "decision_id:",
        "approved:",
        "approved_qty:",
        "rejected_reason:",
        "sizing_method:",
        "limits_checked:",
        "latency_budget_ms:",
    ):
        assert field in data, f"missing RiskDecision field {field}"
    # HealthStatus — shared/core/engine_interface.py:30-36 (4 values)
    for field in ("engine:", "latency_ms:", "version:"):
        assert field in data, f"missing HealthStatus field {field}"
    # HealthSnapshot — shared/core/health.py:25-26 (3 values, NO offline)
    for field in ("modules:",):
        assert field in data, f"missing HealthSnapshot field {field}"
    # Lifecycle aggregation — lifecycle.py:59-82
    for module in ("m2_market_data", "m3_analytics", "m4_alpha", "m5_risk", "m6_execution", "m7_ledger"):
        assert module in data, f"missing engine in the health roll-up: {module}"
    # Environment — shared/core/environment.py:17
    for env in ("development", "testing", "paper", "canary", "production"):
        assert f'"{env}"' in data, f"missing environment {env}"
    # BaseContract lineage — shared/contracts/base.py:53-65
    for field in ("version:", "correlation_id:", "trace_id:", "timestamp:"):
        assert field in data, f"missing lineage field {field}"


def test_admin_surfaces_never_invent_data_for_the_three_unbacked_surfaces() -> None:
    # Kill, Audit and Configuration have NO backend contract. They must say so on
    # screen and must not render fabricated rows that look like real records.
    for surface, marker in (
        ("AdminKillSurface.tsx", "risk.kill_switch"),
        ("AdminAuditSurface.tsx", "append-only"),
        ("AdminConfigurationSurface.tsx", "config_versions"),
    ):
        text = _flat(_read(surface))
        assert marker in text, f"{surface} must cite its real backend anchor {marker}"
    # The gap wording lives in the shared shell so it is stated once, and every one of
    # the three surfaces actually renders that component.
    shell = _flat(_read("admin_ui.tsx"))
    assert "no contract" in shell.lower(), "the shared shell must state the gap"
    for surface in ("AdminKillSurface.tsx", "AdminAuditSurface.tsx", "AdminConfigurationSurface.tsx"):
        assert "NoContractNote" in _read(surface), f"{surface} must render the gap notice"
    # The invention is quarantined in the mock and labelled, not passed off as a contract.
    data = _read("mock_admin_data.ts")
    assert "INVENTED" in data
    assert "no backend contract" in data.lower()
    # Audit rows are a declared shape, not records: it is empty and says so.
    audit = _read("AdminAuditSurface.tsx")
    assert "MOCK_AUDIT_RECORDS" in audit
    assert "length === 0" in audit or "no records" in audit.lower()


def test_kill_and_resume_follow_the_documented_flow() -> None:
    kill = _flat(_read("AdminKillSurface.tsx"))
    # proposal:743-762 — confirmation -> request -> state -> scope -> audit record.
    for step in ("Confirm", "scope", "audit"):
        assert step.lower() in kill.lower(), f"kill flow is missing {step}"
    # Resume is an explicit human action with a reason, and never automatic.
    assert "never auto-resume" in kill.lower() or "never resumes" in kill.lower()
    # §3.3 fail-safe: an unreadable kill state must read as the safer state.
    assert "safer state" in kill.lower()
    # Read-only slice: nothing here can actually halt or resume trading.
    for forbidden in ("fetch(", "websocket", "localstorage", "bearer"):
        assert forbidden not in kill.lower(), f"kill surface must stay mock-only: {forbidden}"


def test_admin_surfaces_are_role_gated_and_viewer_sees_nothing() -> None:
    app = _read("App.tsx")
    nav = _read("nav_config.ts")
    # `role !== "Admin"` is the gate: it refuses Viewer AND Trader in one condition.
    for route in ("risk", "system", "audit", "configuration", "kill"):
        window = app.split(f'path="/admin/{route}"', 1)[1].split("<Route", 1)[0]
        assert 'role !== "Admin"' in window, f"/admin/{route} is not Admin-gated"
        assert "<AccessPreview" in window, f"/admin/{route} has no refusal branch"
    # Trading is global for Admin and private for a Trader, so it keeps the two gates.
    trading = app.split('path="/app/trading"', 1)[1].split("<Route", 1)[0]
    assert 'role === "Viewer"' in trading
    # Navigation hiding is a UX measure only — the backend stays authoritative.
    viewer = nav.split("Viewer: [", 1)[1].split("\n  ],", 1)[0]
    for private in ("/admin", "/app/portfolio", "/app/orders", "/app/settings", "/app/risk", "kill"):
        assert private not in viewer, f"Viewer nav exposes {private}"


def test_admin_never_shows_a_secret_and_states_its_scope() -> None:
    for surface in SURFACES:
        text = _read(f"{surface}.tsx").lower()
        for secret in ("api_token", "vault_key", "password", "private_key", "secret_value"):
            assert secret not in text, f"{surface} references {secret}"
    # proposal:125 — Admin never receives secret values; the redaction is stated once
    # in the shared shell and every surface inherits it.
    shell = _read("admin_ui.tsx")
    assert "redact" in shell.lower()
    # proposal:123 — the UI must show the active tenant scope.
    assert "scope" in shell.lower()
    # And the backend stays authoritative for authorisation.
    assert "backend" in shell.lower()


def test_admin_health_states_do_not_mix_the_two_vocabularies() -> None:
    # health.py:25 HealthSnapshot.status has THREE values (no offline); engine_interface.py:31
    # HealthStatus.status has FOUR. lifecycle.py:80-81 downgrades offline -> degraded.
    # Mixing them would show an Admin a state the aggregator can never emit.
    data = _read("mock_admin_data.ts")
    snapshot_block = _array_literal(data, "HEALTH_SNAPSHOT_STATUS")
    assert "offline" not in snapshot_block
    status_block = _array_literal(data, "ENGINE_HEALTH_VALUES")
    for value in ("healthy", "degraded", "unhealthy", "offline"):
        assert f'"{value}"' in status_block, f"EngineHealth is missing {value}"
    # The downgrade is documented where it is applied, not silently flattened.
    system = _read("AdminSystemSurface.tsx")
    assert "degraded" in system.lower()
    assert "offline" in system.lower()


def test_admin_ui_has_a_status_mapper_that_is_not_the_order_one() -> None:
    # surface_ui.stateClass is OrderState-typed and cannot classify health, so the
    # admin surfaces get their own mapper — plus the CSS to back the classes.
    shell = _read("admin_ui.tsx")
    assert "healthStateClass" in shell
    # It must not CALL the OrderState mapper. Naming it in a WHY comment is fine and is
    # the point — so this checks usage, not the bare substring.
    assert "stateClass(" not in shell, "the admin shell must not reuse the OrderState mapper"
    assert "stateClass" not in shell.split("export function healthStateClass")[1], (
        "healthStateClass must not delegate to the order mapper"
    )
    styles = _styles()
    for css_class in ("is-healthy", "is-degraded", "is-unhealthy", "is-offline"):
        assert f".{css_class}" in styles, f"missing CSS for {css_class}"
    assert ".admin-surface" in styles
