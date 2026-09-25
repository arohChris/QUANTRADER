#!/usr/bin/env python3
"""
PIT Guard — blocks M4/alpha direct Lake access without Temporal Resolver + as_of T,
and blocks the same banned patterns when they appear in the BIBLE (System_Development_Manual).
WHY docs too: direct lake.list(limit=) reintroduces look-ahead (AGENTS.md Rule 5 / §6a),
and a manual that teaches it re-seeds the bypass in every future session.
Usage: python scripts/check_pit_guard.py [--root PATH]
Exit: 0 = no bypass (or no alpha code yet — pre-P22 skip); 1 = violation.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import tokenize
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]

# Alpha/strategy trees that must not touch Lake directly (bible §33 / §6a).
# WHY m4*/m8*: M4 families and the M8 replayer are the look-ahead risk classes —
# a folder name that does not match here is simply never scanned (fail-open).
ALPHA_GLOBS = (
    "modules/alpha/**/*.py",
    "modules/**/alpha/**/*.py",
    "modules/**/strategy/**/*.py",
    "modules/strategy/**/*.py",
    "modules/m4*/**/*.py",
    "modules/m8*/**/*.py",
)

# WHY receiver-agnostic: `l = lake; l.list(limit=50)` and `pit.get_candles()`
# are the same bypass as `lake.list(limit=50)` — matching only the literal name
# `lake.` let an alias walk straight through.
RE_LAKE_IMPORT = re.compile(
    r"(?:from\s+[\w.]*\blake\b\s+import|\bimport\s+[\w.]*\blake\b)",
    re.IGNORECASE,
)
RE_LAKE_LIST_LIMIT = re.compile(r"\.\s*list\s*\([^)]*\blimit\b", re.IGNORECASE)
RE_LAKE_GET = re.compile(r"\.\s*get_candles\b", re.IGNORECASE)
RE_TEMPORAL = re.compile(r"\btemporal_resolver\b|\bTemporalResolver\b|\bfrom\s+[\w.]*temporal", re.IGNORECASE)
RE_AS_OF = re.compile(r"\bas_of\b")

FIX_SNIPPET = """\
# FIX: replace direct lake read with:
#   from shared.temporal.temporal_resolver import resolve
#   window = resolve(requirement, as_of=T)
#   candles = pit.get_market_state(..., as_of=T)  # availability_time <= T
"""


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def code_view(text: str) -> str:
    """Source with comments and string literals blanked.

    WHY: guard keywords (`temporal_resolver`, `as_of`) and the banned call
    itself must come from *executable code*. Reading them from a comment
    (`# resolved via temporal_resolver with as_of`) or from a docstring turned
    the guard into a rubber stamp — anyone could whitelist their own bypass.
    Blank in place (not re-joined from tokens): tokenize drops inter-token
    whitespace, which would fuse `from lake` into `fromlake`.
    """
    lines = text.splitlines(keepends=True)
    try:
        spans = [
            (tok.start, tok.end)
            for tok in tokenize.generate_tokens(io.StringIO(text).readline)
            if tok.type in (tokenize.COMMENT, tokenize.STRING)
        ]
    except (tokenize.TokenError, IndentationError, SyntaxError, EOFError):
        # Fail closed: unparseable input falls back to line-comment stripping only,
        # so a hostile/odd file can never be read as 'guarded'.
        return "\n".join(_strip_line_comment(ln) for ln in text.splitlines())

    for (s_row, s_col), (e_row, e_col) in spans:
        for li in range(s_row - 1, min(e_row, len(lines))):
            line = lines[li]
            start = s_col if li == s_row - 1 else 0
            end = e_col if li == e_row - 1 else len(line)
            lines[li] = (
                line[:start]
                + "".join(c if c in "\r\n" else " " for c in line[start:end])
                + line[end:]
            )
    return "".join(lines)


def _strip_line_comment(line: str) -> str:
    return line.split("#", 1)[0]


# --- Bible docs scan -------------------------------------------------------
# WHY: the manual is how a future builder learns the system. 08_M8_Validation.md
# literally taught `lake.list(..., user_id=...)` — the AGENTS §10 hard-ban — while
# the guard only read *.py. A banned pattern in the bible is a standing invitation
# to re-introduce look-ahead. Docs are scanned for the *banned output*, never for
# prose keywords (a sentence explaining the rule must not trip it).

DOCS_GLOBS = ("*.md", "modules/*.md")

# Per-tenant market-data reads: AGENTS §4.3/Appendix B say market data is stored
# ONCE per provider+instrument with NO user_id; only private data is tenant-scoped.
RE_DOCS_LIST_USER = re.compile(r"lake\s*\.\s*list\s*\([^)]*\buser_id\b", re.IGNORECASE)
RE_DOCS_GET_USER = re.compile(
    r"lake\s*\.\s*(?:get|get_candles)\s*\([^)]*\buser_id\b", re.IGNORECASE
)
# Legacy layout from the pre-v1.4 bible: data/lake/brokers/<broker>/user_id=<id>/...
RE_DOCS_LEGACY_PATH = re.compile(r"data\s*/\s*lake\s*/\s*brokers\s*/", re.IGNORECASE)


def collect_docs_files(docs_root: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in DOCS_GLOBS:
        files.extend(docs_root.glob(pattern))
    return sorted({p.resolve() for p in files if p.is_file()})


def docs_violations(path: Path) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(_read(path).splitlines(), 1):
        if RE_DOCS_LIST_USER.search(line):
            hits.append(
                (lineno, "per-tenant lake.list(user_id) - market data is global (AGENTS §4.3)")
            )
        if RE_DOCS_GET_USER.search(line):
            hits.append(
                (lineno, "per-tenant lake read (lake.get user_id) - use PIT get_market_state(as_of=T)")
            )
        if RE_DOCS_LEGACY_PATH.search(line):
            hits.append(
                (lineno, "legacy per-tenant market-data path data/lake/brokers/ - use market-data/provider=<p>/instrument=<i>")
            )
    return hits


def run_docs(docs_root: Path) -> int:
    """Scan bible .md files for banned lake/user_id patterns. 0 = clean."""
    if not docs_root.is_dir():
        print(f"[OK] bible docs scan skipped (no manual at {docs_root})")
        return 0

    files = collect_docs_files(docs_root)
    failed = False
    for path in files:
        rel = path.relative_to(docs_root) if path.is_relative_to(docs_root) else path
        for lineno, msg in docs_violations(path):
            failed = True
            print(f"[FAIL] {rel}:{lineno}: {msg}")

    if failed:
        return 1
    print(f"[OK] bible docs: {len(files)} file(s), no banned lake/user_id pattern")
    return 0


def collect_alpha_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in ALPHA_GLOBS:
        files.extend(root.glob(pattern))
    # unique, sorted
    return sorted({p.resolve() for p in files if p.is_file()})


def file_violations(path: Path) -> list[str]:
    text = code_view(_read(path))
    hits: list[str] = []
    if RE_LAKE_IMPORT.search(text):
        hits.append("direct lake import (from/import lake)")
    uses_lake_read = bool(RE_LAKE_LIST_LIMIT.search(text) or RE_LAKE_GET.search(text))
    if uses_lake_read:
        has_guard = bool(RE_TEMPORAL.search(text) and RE_AS_OF.search(text))
        if not has_guard:
            hits.append("lake.list(limit=) / lake.get_candles without temporal_resolver + as_of")
    return hits


def run(root: Path) -> int:
    alpha = collect_alpha_files(root)
    if not alpha:
        print("[OK] no alpha/strategy code yet (pre-P22 skip)")
        return 0

    failed = False
    for path in alpha:
        rel = path.relative_to(root) if path.is_relative_to(root) else path
        for msg in file_violations(path):
            failed = True
            print(f"[FAIL] {rel}: {msg}")
            print(FIX_SNIPPET, end="")

    if failed:
        return 1
    print(f"[OK] pit_guard: {len(alpha)} alpha file(s), no direct Lake bypass")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PIT guard — no M4 lake bypass")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="app root (default: financial_analyst_app)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        print(f"[FAIL] root not found: {root}")
        return 1
    code_rc = run(root)
    # WHY sibling manual: app root is QUANTRADER2/financial_analyst_app, the bible
    # is QUANTRADER2/System_Development_Manual — one level up, never inside app.
    docs_rc = run_docs(root.parent / "System_Development_Manual")
    return code_rc or docs_rc


if __name__ == "__main__":
    sys.exit(main())
