from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]  # QUANTRADER2/ (parent of frontend/)
_FRONTEND = _REPO / "frontend"


def test_only_generated_snapshots_are_exempt_from_the_size_cap() -> None:
    # The size cap is a real guard; the exemption must stay narrow or it is worthless.
    exempt = sorted(p.name for p in (_FRONTEND / "app").glob("*.generated.ts"))
    assert exempt == ["news_snapshot.generated.ts"], f"unexpected size-cap exemption: {exempt}"
    for name in exempt:
        text = (_FRONTEND / "app" / name).read_text(encoding="utf-8")
        assert "GENERATED FILE" in text, f"{name} is exempt but does not say it is generated"
        assert "DO NOT EDIT" in text


def test_bundle_is_split_into_vendor_chunks() -> None:
    # The single JS chunk crossed Vite's 500 kB warning line and kept climbing — the
    # generated news snapshot added ~45 kB of it. Splitting vendor code out means a content
    # change does not invalidate the framework cache in a returning visitor's browser.
    config = (_FRONTEND / "vite.config.ts").read_text(encoding="utf-8")
    assert "manualChunks" in config
    for dependency in ("lightweight-charts", "lucide-react"):
        assert dependency in config, f"{dependency} is not split into its own chunk"
    # react and react-router-dom deliberately share the `vendor` chunk with the rest of
    # node_modules: giving them a chunk apart produced a circular chunk that Rollup warns
    # about and that can bite on load order.
    assert "framework" not in config.split("export default", 1)[1], (
        "the framework/vendor split reintroduces the circular-chunk warning"
    )
    assert "circular" in config.lower() or "CIRCULAR" in config


def test_vite_react_typescript_shell_exists() -> None:
    for relative in (
        "package.json",
        "tsconfig.json",
        "vite.config.ts",
        "index.html",
        "app/main.tsx",
        "app/App.tsx",
    ):
        assert (_FRONTEND / relative).is_file(), f"missing M10.4 shell file: {relative}"


def test_package_uses_one_frontend_toolchain() -> None:
    import json

    package = json.loads((_FRONTEND / "package.json").read_text(encoding="utf-8"))
    scripts = package.get("scripts", {})
    assert "dev" in scripts
    assert "build" in scripts
    assert "typecheck" in scripts
    dependencies = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    for required in ("react", "react-dom", "vite", "typescript", "@types/react", "@types/react-dom"):
        assert required in dependencies, f"missing frontend dependency: {required}"


def test_shell_has_role_specific_entry_surfaces() -> None:
    app = (_FRONTEND / "app/App.tsx").read_text(encoding="utf-8")
    nav = (_FRONTEND / "app/nav_config.ts").read_text(encoding="utf-8")
    # The role vocabulary lives beside the nav it switches (nav_config), not inline in
    # App.tsx; App.tsx owns the routes and the shell.
    for role in ("Viewer", "Trader", "Admin"):
        assert role in nav, f"missing role in the nav config: {role}"
        assert f'role === "{role}"' in app or f"{role}:" in nav, f"role never handled: {role}"
    for route in ("/login", "/app", "/admin"):
        assert route in app, f"missing access surface: {route}"


def test_shell_uses_mock_data_for_backend_independent_slice() -> None:
    assert (_FRONTEND / "app/mock_data.ts").is_file()
    app = (_FRONTEND / "app/App.tsx").read_text(encoding="utf-8")
    assert "mock_data" in app
    assert "fetch(" not in app
    assert "WebSocket" not in app


def test_shell_keeps_frontend_and_backend_decoupled() -> None:
    for path in _FRONTEND.rglob("*"):
        if "node_modules" in path.parts or "dist" in path.parts:
            continue
        if not path.is_file() or path.suffix not in {".ts", ".tsx", ".json", ".html", ".css"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        assert "from shared" not in text
        assert "from modules" not in text
        assert "import shared" not in text
        assert "api_token" not in text


def test_shell_exposes_only_documented_access_surfaces() -> None:
    app = (_FRONTEND / "app/App.tsx").read_text(encoding="utf-8")
    for forbidden in ("portfolio.write", "secret_value", "broker_token", "vault_secret"):
        assert forbidden not in app.lower()


def test_existing_day_one_account_panel_remains_available() -> None:
    account = _FRONTEND / "features" / "account_mode"
    for filename in ("index.html", "styles.css", "main.js", "guards.js"):
        assert (account / filename).is_file(), f"existing account panel file missing: {filename}"
    assert (_FRONTEND / "services" / "account_api.js").is_file()


def test_shell_acceptance_test_is_small_and_feature_modular() -> None:
    for relative in (
        "design-system",
        "features",
        "services",
        "state",
        "realtime",
        "types",
        "shared",
    ):
        assert (_FRONTEND / relative).is_dir(), f"missing M10.4 directory: {relative}"
    for path in (_FRONTEND / "app").glob("*"):
        # The 20 kB cap exists to stop HAND-WRITTEN module bloat. A generated data snapshot
        # is the opposite case: capping it just means re-running the generator with a
        # smaller number and showing the reader less news. Exempt by the narrow
        # `*.generated.ts` suffix, and the exemption is asserted below so it cannot widen.
        if path.name.endswith(".generated.ts"):
            continue
        if path.is_file() and path.suffix in {".ts", ".tsx"}:
            assert path.stat().st_size < 20_000, f"shell file is too large: {path.name}"
