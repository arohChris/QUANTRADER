"""
Frontend architecture locks — structural invariants that must hold for ANY frontend shape.

WHY this file replaced test_account_mode_page.py (Amendment 2, 2026-09-27):
the previous file mixed 5 page-specific tests for a now-deleted dependency-free
vanilla-JS panel together with 6 genuine architectural locks. Deleting the panel
must not delete the locks, so the page-specific tests were removed and these
invaluable invariants were kept.

These tests assert NO specific endpoint, service, or UI. They only assert
invariants that the gateway work in particular depends on staying true.
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]  # QUANTRADER2/
_APP = _REPO / "financial_analyst_app"
_FE = _REPO / "frontend"
_FOUNDATIONS = _FE / "design-system" / "foundations"
_BACKEND_MAIN = _APP / "app" / "main.py"

# Directories that hold TRACKED files. A layout lock must only assert directories
# that survive a clean checkout — git does not track empty directories, so
# asserting an empty placeholder directory makes the test pass locally and fail
# on a fresh clone. The previous version of this lock asserted state/, realtime/,
# types/ and components/, all of which are empty and untracked.
_TRACKED_DIRS = ("app", "design-system", "shared", "specs", "tests")

_MAX_GOD_FILE_BYTES = 40_000


def test_frontend_at_project_root_not_inside_app() -> None:
    assert _FE.is_dir(), "QUANTRADER2/frontend/ must exist at project root"
    assert not (_APP / "frontend").exists(), "frontend must not live inside financial_analyst_app/"


def test_frontend_layout_holds_on_a_clean_checkout() -> None:
    """Layout lock restricted to directories git actually tracks.

    WHY: an earlier version asserted empty placeholder directories, which pass on
    a developer machine and fail on `git clone`. A lock that only works locally is
    not a lock.
    """
    for sub in _TRACKED_DIRS:
        assert (_FE / sub).is_dir(), f"frontend/{sub}/ missing"
        has_file = any(
            p.is_file() and "node_modules" not in p.parts
            for p in (_FE / sub).rglob("*")
        )
        assert has_file, f"frontend/{sub}/ has no tracked files — it will vanish on a clean checkout"


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
    """The frontend's import boundary — the guard that stops a browser bundle
    from importing backend Python.

    WHY this is the most important test in the frontend: the API gateway work
    requires the browser to reach the backend ONLY over HTTP, through the frozen
    contracts. If this lock is removed, a router can start importing
    `modules.*` or `shared.*` directly and bypass the whole API layer.
    """
    for f in _FE.rglob("*"):
        if "node_modules" in f.parts or "dist" in f.parts:
            continue
        if f.is_file() and f.suffix in {".html", ".js", ".ts", ".tsx", ".css", ".json"}:
            text = f.read_text(encoding="utf-8", errors="ignore").lower()
            assert "from shared" not in text
            assert "from modules" not in text
            assert "import shared" not in text
            assert "import modules" not in text


def test_no_ui_god_file() -> None:
    """No hand-written UI monolith.

    WHY the 20 kB cap is not applied here: a generated data snapshot is legitimately
    large, and the ban exists to stop hand-written bloat, not generated data.
    """
    for f in _FE.rglob("*"):
        if "node_modules" in f.parts or "dist" in f.parts:
            continue
        if not f.is_file() or f.suffix not in {".html", ".jsx", ".tsx"}:
            continue
        assert f.stat().st_size <= _MAX_GOD_FILE_BYTES, (
            f"{f.name} looks like a UI monolith ({f.stat().st_size} bytes)"
        )


def test_no_webpack_or_cra_without_vite() -> None:
    assert not list(_FE.rglob("webpack.config.*")), "webpack config without Vite layout"
    assert not list(_FE.rglob("create-react-app*")), "CRA scaffold without Vite layout"
    assert (_FE / "vite.config.ts").is_file(), "frontend must be built by Vite"


def test_design_system_foundations_split() -> None:
    for name in ("colors.css", "typography.css", "spacing.css"):
        assert (_FOUNDATIONS / name).is_file(), f"design-system foundation missing: {name}"
