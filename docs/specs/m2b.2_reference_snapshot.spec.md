# Spec: M2b.2 Reference Snapshot Lineage — `current_snapshot_id()`

Purpose: Return the effective immutable reference snapshot id (`REF-YYYY-MM-DD-NNN`) that M2.2 stamps on every canonical `MarketData.reference_snapshot_id` — lineage for audit/replay (M8 re-runs the same REF).
Bible ref: System_Development_Manual/modules/02b_M2b_Reference.md §5 (entry point "Holds current_snapshot_id"), §6 (MarketData lineage), invariant M2b-I03 (snapshots immutable, never UPDATE).
Blocker closed: `modules/m2_market_data/normalize.py:96` calls `self._reference.current_snapshot_id()` — facade had no such function (stubs only in `tests/m2_market_data/test_m2.2_spec.py:66` + `scripts/verify_m2.2_normalize.py:30`).

## Inputs
- `config/reference/snapshot.yaml` (repo-local; new file) — exactly the snapshot record:
  ```yaml
  snapshot_id: REF-2026-09-25-001
  effective_from: 2026-09-25T00:00:00+00:00   # ISO-8601, timezone-aware, UTC
  ```
- `load_snapshot_id(path: Path) -> str | None` — path argument (tests/tools pass tmp files).
- `current_snapshot_id() -> str | None` — no arguments; reads the repo file (cached).

## Outputs
- `snapshot_id: str` — matches regex `^REF-\d{4}-\d{2}-\d{2}-\d{3}$` (e.g. `REF-2026-09-25-001`).
- `None` on **every** failure path — never raises (M2.2 `normalize.py:96` has no try/except; a raise would crash ingestion; `None` → M2.2 `Drop(missing_field)` = fail-safe, no MarketData without lineage).
- Both functions **sync** (M2b facade law: no await, no I/O in hot path — file read happens once, then cached).

## Invariants
- **I03 immutability:** code never writes/updates `snapshot.yaml` — rotation = human edits the file + process restart (new snapshot = new id, never in-place mutation of a published id).
- **I08 determinism:** same file content → same id, always (golden test on the seeded repo file).
- **Lineage mandatory:** no valid snapshot → no `MarketData` (M2.2 drops) — empty/None is never stamped.
- Effective dating is recorded but informational for now (clock purity: no `now()` comparison — future `effective_from` still returns the id; M2b.3 `get_spec(effective_at)` owns historical lookup).

## Edges (one test each)
- valid file → returns `snapshot_id` string, format-valid.
- file missing → `None`.
- file not valid YAML (parse error) → `None`.
- YAML top-level not a mapping (list/string) → `None`.
- `snapshot_id` key missing → `None`.
- `snapshot_id` empty string / whitespace-only → `None`.
- `snapshot_id` not a str (int, e.g. `123`) → `None`.
- `snapshot_id` wrong format (`ref-2026-9-1-1`, `REF-2026-09-25-1`, `REF-2026-09-25-1234`, `REF-2026-09-25-001-EXTRA`) → `None`.
- `effective_from` key missing → `None`.
- `effective_from` naive (no tz) → `None`.
- `effective_from` unparseable (`"not-a-date"`) → `None`.
- `effective_from` in the future → **still returns the id** (no clock dependency).
- path is a directory → `None` (never raises, adversarial).
- `current_snapshot_id()` cached: two consecutive calls return the identical value; file replacement after first call is NOT picked up until `current_snapshot_id.cache_clear()` / restart (documented rotation semantics).
- repo seeded file: `current_snapshot_id()` equals `load_snapshot_id(<repo path>)` and matches format.

## Correct
- Format gate: `re.fullmatch(r"REF-\d{4}-\d{2}-\d{2}-\d{3}", snapshot_id)` — exactly 3-digit sequence.
- `effective_from` gate: `datetime.fromisoformat(s)` succeeds **and** `tzinfo is not None` **and** `utcoffset() is not None` (§3.8 UTC discipline).
- Any `OSError` / `UnicodeDecodeError` / `yaml.YAMLError` / unexpected exception → caught, logged once at WARNING with the path, return `None` (§3.9: visible, never silent success).
- Seed file committed: `config/reference/snapshot.yaml` with `snapshot_id: REF-2026-09-25-001`, `effective_from: 2026-09-25T00:00:00+00:00`.
- No money math (hand-calc N/A).

## Adversarial (not in edges above)
- `snapshot_id: "'; DROP TABLE reference;--"` → format fail → `None` (never executed, never stamped).
- YAML file containing a Python-repr-looking string / wrong types → `None`, typed-safe.
- Unreadable path (directory) → `None`, no exception escapes.

## Contract note
- **No shared contract change** — `MarketData.reference_snapshot_id: str | None` already frozen (v1.4); consumers: M2.2 normalize (exists), M2.4/M2.6 verify scripts (switch stubs to real facade in Step 5), M8 replay (future).
- `modules/reference/reference.py` gains two additive functions (M2b.1 behavior untouched — existing 36 tests must stay green).
- `normalize.py` is FROZEN M2.2 and is **not edited** — the facade conforms to the call shape already written.
- Deferred (P06): startup validation gate (`reference_validator` FAIL STARTUP) — here invalid config fails safe per-call (None → Drop), not process-wide.
