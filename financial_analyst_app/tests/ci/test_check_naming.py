"""Self-tests for scripts/check_naming.py — tests/ci folder."""
from __future__ import annotations

from pathlib import Path

from scripts.check_naming import check_specs, check_tests, check_verify, main


def test_good_spec_name_passes(tmp_path: Path):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "m3.0_capability.spec.md").write_text("x", encoding="utf-8")
    assert check_specs(specs) == []


def test_bad_spec_name_fails(tmp_path: Path):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "foo.md").write_text("x", encoding="utf-8")
    fails = check_specs(specs)
    assert fails and "mX.Y" in fails[0]


def test_missing_specs_dir_fails(tmp_path: Path):
    assert check_specs(tmp_path / "nope")


def test_good_module_test_passes(tmp_path: Path):
    t = tmp_path / "tests" / "m3_analytics"
    t.mkdir(parents=True)
    (t / "test_m3.0_spec.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    assert check_tests(tmp_path) == []


def test_bad_module_test_fails(tmp_path: Path):
    t = tmp_path / "tests" / "m3_analytics"
    t.mkdir(parents=True)
    (t / "test_rsi.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    fails = check_tests(tmp_path)
    assert fails and "test_mX.Y_spec.py" in fails[0]


def test_unallowlisted_foundation_test_fails(tmp_path: Path):
    t = tmp_path / "tests"
    t.mkdir()
    (t / "test_random_thing.py").write_text("x", encoding="utf-8")
    fails = check_tests(tmp_path)
    assert fails and "allowlist" in fails[0]


def test_verify_good_and_bad(tmp_path: Path):
    s = tmp_path / "scripts"
    s.mkdir()
    (s / "verify_m3.0_capability.py").write_text("x", encoding="utf-8")
    (s / "verify_rsi.py").write_text("x", encoding="utf-8")
    fails = check_verify(tmp_path)
    assert any("verify_mX.Y" in f for f in fails)


def test_adversarial_sql_name_in_specs(tmp_path: Path):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "'; DROP TABLE--.md").write_text("x", encoding="utf-8")
    assert check_specs(specs)


def test_main_with_args(tmp_path: Path):
    specs = tmp_path / "specs"
    specs.mkdir()
    (specs / "m1.0_ok.spec.md").write_text("x", encoding="utf-8")
    root = tmp_path / "app"
    (root / "tests").mkdir(parents=True)
    (root / "scripts").mkdir(parents=True)
    rc = main(["--root", str(root), "--specs", str(specs)])
    assert rc == 0


# --- Regression: guard bugs found 2026-09-24 (tests written first) ---


def test_verify_name_with_digit_after_underscore_passes(tmp_path: Path):
    """BUG 2026-09-24: RE_VERIFY_M used `[a-z09_]` — digits 1-8 were rejected."""
    s = tmp_path / "scripts"
    s.mkdir()
    (s / "verify_m3.0_dedupe5.py").write_text("x", encoding="utf-8")
    assert check_verify(tmp_path) == []


def test_verify_name_with_digits_and_suffix_passes(tmp_path: Path):
    s = tmp_path / "scripts"
    s.mkdir()
    (s / "verify_m2.8_storage_1234.py").write_text("x", encoding="utf-8")
    assert check_verify(tmp_path) == []


def test_verify_name_with_uppercase_fails(tmp_path: Path):
    s = tmp_path / "scripts"
    s.mkdir()
    (s / "verify_m3.0_Dedup.py").write_text("x", encoding="utf-8")
    assert check_verify(tmp_path)


def test_verify_name_missing_major_minor_fails(tmp_path: Path):
    s = tmp_path / "scripts"
    s.mkdir()
    (s / "verify_m30_foo.py").write_text("x", encoding="utf-8")
    assert check_verify(tmp_path)
