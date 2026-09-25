"""
M10.4 Frontend tests — static checks ONLY (no browser).
Architecture lock: multi-file native ES modules — not one HTML monolith, not React, not Vite.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]  # QUANTRADER2/
_APP = _REPO / "financial_analyst_app"
_FE = _REPO / "frontend"
_INDEX = _FE / "features" / "account_mode" / "index.html"
_MAIN_JS = _FE / "features" / "account_mode" / "main.js"
_STYLES = _FE / "features" / "account_mode" / "styles.css"
_GUARDS = _FE / "features" / "account_mode" / "guards.js"
_API = _FE / "services" / "account_api.js"
_FOUNDATIONS = _FE / "design-system" / "foundations"
_BACKEND_MAIN = _APP / "app" / "main.py"

_MAX_HTML_BYTES = 12_000
_MAX_INLINE_STYLE_BYTES = 400
_MAX_INLINE_SCRIPT_BYTES = 200


def test_frontend_index_has_toggle_button() -> None:
    assert _INDEX.exists(), "QUANTRADER2/frontend/features/account_mode/index.html must exist"
    text = _INDEX.read_text(encoding="utf-8")
    assert "account/mode" in text or "btn-demo" in text
    assert "DEMO" in text.upper() or "demo" in text
    assert "button" in text.lower()
    assert "import " not in text  # no Python / no classic inline import noise
    assert "api_token" not in text.lower()


def test_frontend_at_project_root_not_inside_app() -> None:
    assert _FE.is_dir(), "QUANTRADER2/frontend/ must exist at project root"
    assert not (_APP / "frontend").exists(), "frontend must not live inside financial_analyst_app/"


def test_frontend_folder_structure_matches_m104() -> None:
    for sub in ("features", "realtime", "state", "services", "components", "types"):
        assert (_FE / sub).is_dir(), f"frontend/{sub}/ missing (M10.4 layout)"


def test_frontend_is_decoupled_from_backend() -> None:
    stray_py = [
        p for p in _FE.rglob("*.py") if "node_modules" not in p.parts and "tests" not in p.parts
    ]
    assert not stray_py, "frontend/ must stay static — .py only in frontend/tests/"
    main_py = _BACKEND_MAIN.read_text(encoding="utf-8")
    assert "FileResponse" not in main_py
    assert "StaticFiles" not in main_py
    assert "HTMLResponse" not in main_py
    assert "index.html" not in main_py


def test_frontend_has_no_backend_module_imports() -> None:
    for f in _FE.rglob("*"):
        if "node_modules" in f.parts or "dist" in f.parts:
            continue
        if f.is_file() and f.suffix in {".html", ".js", ".ts", ".css", ".json"}:
            text = f.read_text(encoding="utf-8", errors="ignore").lower()
            assert "from shared" not in text
            assert "from modules" not in text
            assert "import shared" not in text


def test_index_is_thin_markup_not_monolith() -> None:
    """No 600-line HTML/CSS/JS blob — markup links external CSS + module JS."""
    raw = _INDEX.read_bytes()
    assert len(raw) <= _MAX_HTML_BYTES, (
        f"index.html is {len(raw)} bytes — must stay thin (≤ {_MAX_HTML_BYTES}); "
        "split CSS/JS into separate files"
    )
    text = _INDEX.read_text(encoding="utf-8")
    assert "<style" not in text, "No inline <style> monolith — use design-system + feature .css"
    assert 'type="module"' in text and "main.js" in text, "Must load features/.../main.js as ES module"
    assert "rel=\"stylesheet\"" in text or "rel='stylesheet'" in text
    # tiny inline style/scripts for attrs only (if any) — not logic blocks
    assert text.count("<style>") == 0


def test_feature_is_modular_multi_file() -> None:
    """Account mode feature = HTML + styles.css + main.js + guards.js (not one file)."""
    for path in (_INDEX, _STYLES, _MAIN_JS, _GUARDS, _API):
        assert path.is_file(), f"missing modular frontend file: {path.name}"
    for path in (_MAIN_JS, _GUARDS, _API, _STYLES):
        assert path.stat().st_size < 20_000, f"{path.name} too large — keep modules small"
    html = _INDEX.read_text(encoding="utf-8")
    assert "styles.css" in html
    assert "guards" not in html or "main.js" in html  # guards imported from main.js
    main = _MAIN_JS.read_text(encoding="utf-8")
    assert "account_api.js" in main
    assert "guards.js" in main
    assert main.lstrip().startswith("//") or "import " in main[:500]


def test_no_god_file_and_day1_stays_toolchain_free() -> None:
    """Monolith ban + Day-1: breaker panel opens without npm.
    M10.4 (when opened) may add package.json/vite under frontend/ — structure B in bible §0.1.
    """
    # God-file bans
    for f in _FE.rglob("*"):
        if "node_modules" in f.parts or "dist" in f.parts:
            continue
        if not f.is_file() or f.suffix not in {".html", ".jsx", ".tsx", ".js", ".ts"}:
            continue
        if f.stat().st_size > 40_000 and f.suffix in {".html", ".jsx", ".tsx"}:
            raise AssertionError(f"{f.name} looks like a UI monolith ({f.stat().st_size} bytes)")
    # Day-1 static panel: no toolchain required yet (M10.4 not open)
    if not (_FE / "vite.config.ts").exists() and not (_FE / "package.json").exists():
        assert not (_FE / "node_modules").exists()
        for f in _FE.rglob("*"):
            if f.is_file() and f.suffix in {".js", ".html"}:
                text = f.read_text(encoding="utf-8", errors="ignore")
                assert "from 'react'" not in text and 'from "react"' not in text
                assert "jsx(" not in text
    # Always: no webpack/CRA mess without Vite layout
    assert not list(_FE.rglob("webpack.config.*"))
    assert not list(_FE.rglob("create-react-app*"))
    # account_mode Day-1 remains multi-file static (until M10.4 migration)
    if _MAIN_JS.is_file() and "type=\"module\"" in _INDEX.read_text(encoding="utf-8"):
        assert _STYLES.is_file() and _GUARDS.is_file() and _API.is_file()


def test_feature_folders_not_type_first_junk_drawer() -> None:
    """features/ is the unit of extension; services stay one-domain-per-file."""
    assert (_FE / "features" / "account_mode").is_dir()
    assert _API.is_file()
    # no scattering: account API must not live only inside a single mega index
    feature_files = list((_FE / "features" / "account_mode").iterdir())
    assert len(feature_files) >= 4, "feature must be multi-file (html+css+js modules)"


def test_design_system_foundations_split() -> None:
    assert (_FOUNDATIONS / "colors.css").is_file()
    assert (_FOUNDATIONS / "typography.css").is_file()
    assert (_FOUNDATIONS / "spacing.css").is_file()


def test_account_api_only_touches_account_endpoints() -> None:
    api = _API.read_text(encoding="utf-8")
    assert "account/status" in api
    assert "account/mode" in api
    assert "account/auto-trade" in api
    assert "http://127.0.0.1:8000" in api
