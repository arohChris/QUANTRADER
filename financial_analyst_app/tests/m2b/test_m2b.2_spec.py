"""
M2b.2 — Reference Snapshot Lineage (current_snapshot_id) — Spec tests (WRITTEN BEFORE CODE).
WHY: AGENTS.md 2 Step 3 — tests exist first against docs/specs/m2b.2_reference_snapshot.spec.md.
Implementation imported only via the modules.reference facade — collection/attribute RED until Step 5.

Consumer contract under test: M2.2 normalize.py:96 calls reference.current_snapshot_id() with
NO try/except and stamps MarketData.reference_snapshot_id — so the facade must return str|None
and NEVER raise (None -> Drop(missing_field) = fail-safe, no lineage-less MarketData).

Edges covered 1:1 from spec + 3 adversarial (sql-injection id, directory path, wrong YAML types).
No money math (hand-calc N/A per spec).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# Direct import ONLY — no importorskip: missing impl must FAIL collection (honest RED),
# never skip to exit 0 (AGENTS.md 2 Step 6 zero-skips, 10 anti-pattern).
from modules.reference import reference

_SNAPSHOT_RE = re.compile(r"^REF-\d{4}-\d{2}-\d{2}-\d{3}$")
_REPO_SNAPSHOT_PATH = Path(__file__).resolve().parents[2] / "config" / "reference" / "snapshot.yaml"


@pytest.fixture
def fresh_snapshot_cache():
    """Isolate the process-level snapshot cache (spec Edge: cached per process)."""
    reference.current_snapshot_id.cache_clear()
    yield
    reference.current_snapshot_id.cache_clear()


def _write_snapshot(path: Path, *, snapshot_id="REF-2026-01-02-003",
                    effective_from="2026-01-02T00:00:00+00:00",
                    drop_id=False, drop_effective=False, raw=None) -> Path:
    if raw is not None:
        path.write_text(raw, encoding="utf-8")
        return path
    data: dict = {}
    if not drop_id:
        data["snapshot_id"] = snapshot_id
    if not drop_effective:
        data["effective_from"] = effective_from
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


# --- valid paths -----------------------------------------------------------

def test_valid_snapshot_returns_ref_id(tmp_path):
    p = _write_snapshot(tmp_path / "snapshot.yaml")
    assert reference.load_snapshot_id(p) == "REF-2026-01-02-003"


def test_seeded_repo_config_format_and_current(fresh_snapshot_cache):
    """Spec Correct: repo seeds config/reference/snapshot.yaml; golden (I08)."""
    assert _REPO_SNAPSHOT_PATH.exists(), f"seed file missing: {_REPO_SNAPSHOT_PATH}"
    loaded = reference.load_snapshot_id(_REPO_SNAPSHOT_PATH)
    assert loaded is not None and _SNAPSHOT_RE.fullmatch(loaded)
    assert reference.current_snapshot_id() == loaded
    # Determinism (I08): same process, same value, twice.
    assert reference.current_snapshot_id() == loaded


def test_offset_effective_from_accepted(tmp_path):
    p = _write_snapshot(tmp_path / "snapshot.yaml", effective_from="2026-01-02T02:00:00+02:00")
    assert reference.load_snapshot_id(p) == "REF-2026-01-02-003"


def test_future_effective_from_still_returned(tmp_path):
    """Spec Edge: no clock dependency — future effective_from still returns the id."""
    p = _write_snapshot(tmp_path / "snapshot.yaml", effective_from="2099-01-01T00:00:00+00:00")
    assert reference.load_snapshot_id(p) == "REF-2026-01-02-003"


# --- Edges: None on every failure path ------------------------------------

def test_missing_file_returns_none(tmp_path):
    assert reference.load_snapshot_id(tmp_path / "nope.yaml") is None


def test_invalid_yaml_returns_none(tmp_path):
    p = _write_snapshot(tmp_path / "snapshot.yaml", raw='snapshot_id: "unclosed [')
    assert reference.load_snapshot_id(p) is None


def test_non_mapping_yaml_returns_none(tmp_path):
    p = _write_snapshot(tmp_path / "snapshot.yaml", raw="- REF-2026-01-02-003\n- other\n")
    assert reference.load_snapshot_id(p) is None


def test_missing_snapshot_id_key_returns_none(tmp_path):
    p = _write_snapshot(tmp_path / "snapshot.yaml", drop_id=True)
    assert reference.load_snapshot_id(p) is None


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_snapshot_id_returns_none(tmp_path, bad):
    p = _write_snapshot(tmp_path / "snapshot.yaml", snapshot_id=bad)
    assert reference.load_snapshot_id(p) is None


@pytest.mark.parametrize("bad", [123, None, ["REF-2026-01-02-003"], {"id": "x"}])
def test_non_string_snapshot_id_returns_none(tmp_path, bad):
    p = _write_snapshot(tmp_path / "snapshot.yaml", snapshot_id=bad)
    assert reference.load_snapshot_id(p) is None


@pytest.mark.parametrize("bad", [
    "ref-2026-9-1-1",
    "REF-2026-09-25-1",
    "REF-2026-09-25-1234",
    "REF-2026-09-25-001-EXTRA",
    "REF-20260925001",
    "2026-09-25-001",
])
def test_malformed_ref_format_returns_none(tmp_path, bad):
    p = _write_snapshot(tmp_path / "snapshot.yaml", snapshot_id=bad)
    assert reference.load_snapshot_id(p) is None


def test_missing_effective_from_returns_none(tmp_path):
    p = _write_snapshot(tmp_path / "snapshot.yaml", drop_effective=True)
    assert reference.load_snapshot_id(p) is None


def test_naive_effective_from_returns_none(tmp_path):
    p = _write_snapshot(tmp_path / "snapshot.yaml", effective_from="2026-01-02T00:00:00")
    assert reference.load_snapshot_id(p) is None


def test_invalid_effective_from_returns_none(tmp_path):
    p = _write_snapshot(tmp_path / "snapshot.yaml", effective_from="not-a-date")
    assert reference.load_snapshot_id(p) is None


# --- Adversarial (not in spec Edges) --------------------------------------

def test_sql_injection_snapshot_id_returns_none(tmp_path):
    p = _write_snapshot(tmp_path / "snapshot.yaml", snapshot_id="'; DROP TABLE reference;--")
    assert reference.load_snapshot_id(p) is None


def test_directory_path_returns_none(tmp_path):
    """Adversarial: path is a directory — never raises (spec: OSError -> None)."""
    d = tmp_path / "adir"
    d.mkdir()
    assert reference.load_snapshot_id(d) is None


def test_non_string_effective_from_returns_none(tmp_path):
    """Adversarial: wrong YAML type for effective_from (int epoch) -> None, not crash."""
    p = _write_snapshot(tmp_path / "snapshot.yaml", effective_from=1774208800)
    assert reference.load_snapshot_id(p) is None


# --- current_snapshot_id(): cache + never-raises ---------------------------

def test_current_snapshot_id_cached_deterministic(fresh_snapshot_cache):
    a = reference.current_snapshot_id()
    b = reference.current_snapshot_id()
    assert a == b
    assert a is None or _SNAPSHOT_RE.fullmatch(a)


def test_current_snapshot_id_never_raises_on_missing(fresh_snapshot_cache, monkeypatch):
    """Spec: repo file absent -> None (not exception). Cache isolated via fixture."""
    monkeypatch.setattr(reference, "_SNAPSHOT_PATH", Path(__file__).resolve().parents[1] / "no_such.yaml")
    reference.current_snapshot_id.cache_clear()
    assert reference.current_snapshot_id() is None
