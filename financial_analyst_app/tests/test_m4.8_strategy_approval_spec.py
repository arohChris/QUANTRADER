"""
M4.8 — Strategy Approval — spec tests (WRITTEN AGAINST THE SPEC BEFORE CODE, AGENTS §2 Step 3).

Source of truth: docs/specs/m4.8_strategy_approval.spec.md (Step 1 CLOSED — human LGTM
2026-10-03 with REV1/REV2 amendments). Every edge in spec "Edges" + "Adversarial" has a
test here, named after the edge; the 25 transition pairs and the 20 runnability cells
are parametrized so no pair/cell can silently go untested (spec Correct #1/#2).

What exists already (module-level imports, GREEN expected):
  - the frozen contract `shared/contracts/strategy_approval.py` (Step 2, pkg 2.3.0).
What does NOT exist yet (lazy imports inside tests => each test fails individually,
honest RED until Step 5 — no collection blocker needed):
  - `shared/core/strategy_approval_store.py` (store + typed errors),
  - the runner gate itself (`approvals=` ctor kwarg, `audience=` run kwarg).

Money math: NONE in this sub-module (spec Scope: enum strings, UTC instants, booleans).
Hand-calc tests: n/a by design (spec Correct #8). Fakes never import asyncpg.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.feature_engine import FeatureRequest
from modules.m4_alpha.strategy.lifecycle import Lifecycle
from modules.m4_alpha.strategy.parameter_registry import ParameterRegistry
from modules.m4_alpha.strategy.runner import StrategyRunner
from modules.m4_alpha.strategy.strategy_registry import StrategyRegistry
from shared.contracts import StrategyApproval, StrategyState
from shared.contracts.analytics import AnalyticsResult
from shared.contracts.market_data import MarketData, Tick
from shared.contracts.signal import Signal

_APP = Path(__file__).resolve().parents[1]
_CAP_YAML = _APP / "config" / "capabilities.yaml"
_STRAT_DIR = _APP / "config" / "strategies"

NAME = "trend_following"
_TS = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
STATES: tuple[str, ...] = ("draft", "testing", "approved", "rejected", "discontinued")

# The spec transition matrix (single source of truth, spec State machine section).
# same-state = idempotent no-op (tested in the matrix, (x,x) pairs); everything
# not listed here must raise ApprovalTransitionError.
LEGAL_MOVES: dict[str, frozenset[str]] = {
    "draft": frozenset({"testing", "rejected", "discontinued"}),       # implicit row
    "testing": frozenset({"draft", "approved", "rejected"}),
    "approved": frozenset({"testing", "rejected", "discontinued"}),
    "rejected": frozenset({"testing"}),
    "discontinued": frozenset({"testing", "approved"}),                # restore = human law
}

_GOLDEN = {"ema_20": 101.0, "sma_20": 100.0, "rsi_14": 65.0}  # m4.1 E12 hand -> +1.0


def _approval(**over: Any) -> StrategyApproval:
    over.setdefault("strategy_id", NAME)
    over.setdefault("version", "1.0")
    over.setdefault("state", "approved")
    over.setdefault("changed_by", "admin")
    over.setdefault("changed_at", datetime(2026, 10, 3, 12, 0, tzinfo=UTC))
    return StrategyApproval(**over)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------
# FakeApprovalsDB — single-table stand-in that routes REAL SQL text and enforces the
# laws this spec claims to test (m1.5 FakePrefsDB pattern, so scoping/atomicity/
# uniqueness are verified, not assumed):
#   - row reads must be PK-scoped (strategy_id+version, order-insensitive);
#   - list reads filter `WHERE state = $1` only (audience law);
#   - writes must be SINGLE-STATEMENT INSERT..ON CONFLICT upserts;
#   - REV3 (human 2026-10-07, multi-strategy testing): NO unique index on state —
#     concurrent state='testing' rows are legal and must both persist;
#   - fail={"fetch"|"fetchval"|"execute"} -> OSError, so the wrap-everything-into-
#     ApprovalStoreError law is observable;
#   - every (sql, params) recorded so "before any SQL" assertions are real;
#   - `await asyncio.sleep(0)` inside fetch/execute creates true yield points so
#     asyncio.gather tests exercise interleaving, not sequential runs.
# ----------------------------------------------------------------------------------


class FakeApprovalsDB:
    TABLE = "strategy_approvals"
    PK: ClassVar[tuple[str, ...]] = ("strategy_id", "version")

    def __init__(self, fail: set[str] | None = None) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.fail: set[str] = set(fail or ())

    def _check_table(self, sql: str) -> None:
        if self.TABLE not in sql:
            raise AssertionError(f"FakeApprovalsDB: unknown table in SQL: {sql!r}")

    async def _read(self, sql: str, args: tuple[object, ...]) -> list[dict[str, Any]]:
        self._check_table(sql)
        if not sql.lstrip().upper().startswith("SELECT"):
            raise AssertionError(f"fetch used for non-SELECT: {sql!r}")
        where = sql.split("WHERE", 1)[1] if "WHERE" in sql else ""
        cols = tuple(re.findall(r"(\w+)\s*=\s*\$\d+", where))
        if cols == ("state",):  # list_visible audience filter
            return [dict(r) for r in self.rows.values() if r["state"] == args[0]]
        if len(args) == 2 and set(cols) == set(self.PK):  # PK-scoped row read
            bound = dict(zip(cols, args))
            row = self.rows.get((str(bound["strategy_id"]), str(bound["version"])))
            return [dict(row)] if row else []
        raise AssertionError(
            f"read WHERE {cols!r} must be full PK {self.PK} or state-only, got {args!r}"
        )

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        await asyncio.sleep(0)
        self.calls.append((sql, args))
        if "fetch" in self.fail:
            raise OSError("simulated outage")
        return await self._read(sql, args)

    async def fetchval(self, sql: str, *args: object) -> Any:
        await asyncio.sleep(0)
        self.calls.append((sql, args))
        if "fetchval" in self.fail:
            raise OSError("simulated outage")
        if "COUNT" in sql.upper():
            return len(self.rows)
        got = await self._read(sql, args)
        return next(iter(got[0].values()), None) if got else None

    async def execute(self, sql: str, *args: object) -> str:
        await asyncio.sleep(0)
        self.calls.append((sql, args))
        if "execute" in self.fail:
            raise OSError("simulated outage")
        self._check_table(sql)
        match = re.search(r"INSERT INTO \w+ \(([^)]*)\)", sql)
        if not match or "ON CONFLICT" not in sql.upper():
            raise AssertionError(
                f"writes must be single-statement INSERT..ON CONFLICT upserts: {sql!r}"
            )
        cols = tuple(c.strip() for c in match.group(1).split(","))
        if len(cols) != len(args):
            raise AssertionError(f"column/param mismatch: {len(cols)} cols vs {len(args)} params")
        row = dict(zip(cols, args))
        missing = set(self.PK) - set(cols)
        if missing:
            raise AssertionError(f"write missing PK column(s): {sorted(missing)}")
        key = (str(row["strategy_id"]), str(row["version"]))
        if "DO NOTHING" in sql:
            if key in self.rows:
                return "INSERT 0 0"  # idempotent seed — existing row untouched
            self.rows[key] = row
            return "INSERT 0 1"
        if "DO UPDATE" in sql:
            self.rows[key] = row
            return "INSERT 0 1"
        raise AssertionError(f"write missing ON CONFLICT clause: {sql!r}")

    def writes_since(self, idx: int) -> list[str]:
        """SQL statements issued after call index idx that would mutate the table."""
        return [
            sql for sql, _ in self.calls[idx:]
            if sql.lstrip().upper().startswith(("INSERT", "UPDATE"))
        ]


def _store(
    db: FakeApprovalsDB | None = None, *, known: set[str] | frozenset[str] | None = None
) -> tuple[Any, FakeApprovalsDB]:
    from shared.core.strategy_approval_store import StrategyApprovalStore  # RED until S5

    db = FakeApprovalsDB() if db is None else db
    store = StrategyApprovalStore(db, known_strategies=set(known or ()))
    return store, db


def _unwired() -> Any:
    from shared.core.strategy_approval_store import StrategyApprovalStore  # RED until S5

    return StrategyApprovalStore(None, known_strategies=set())


def _mod() -> Any:
    """Lazy module handle for the typed errors (each test RED individually)."""
    import shared.core.strategy_approval_store as m  # RED until S5

    return m


async def _reach(store: Any, sid: str, ver: str, target: str) -> StrategyApproval | None:
    """Walk a legal path to `target` so a matrix pair starts from `frm` (spec Edges)."""
    if target == "draft":
        return None  # implicit draft = no row at all
    if target == "approved":  # dry-run-first: approved only via testing
        await store.set_state(sid, ver, "testing", changed_by="admin")
        await store.set_state(sid, ver, "approved", changed_by="admin")
        return await store.get(sid, ver)
    await store.set_state(sid, ver, target, changed_by="admin")
    return await store.get(sid, ver)


# ----------------------------------------------------------------------------------
# Runner-gate fixtures (runner deps exist at Step 3; `approvals=`/`audience=` do not)
# ----------------------------------------------------------------------------------


def _md() -> MarketData:
    return MarketData(
        instrument_id="FX.EURUSD", symbol="EURUSD", broker="deriv", provider="deriv",
        provider_symbol="frxEURUSD", kind="tick", watermark=7,
        payload=Tick(price=1.0854, bid=1.08538, ask=1.08542, spread=0.00004),
        exchange_timestamp=_TS, reference_snapshot_id="REF-2026-09-25-001",
    )


def _analytics() -> AnalyticsResult:
    return AnalyticsResult(symbol="EURUSD", broker="deriv", timeframe="1m",
                           features=dict(_GOLDEN))


class FakeEngine:
    """Scripted FeatureEngine — records every request, replays one canned result."""

    def __init__(self, *results: AnalyticsResult) -> None:
        self._results = list(results)
        self.requests: list[FeatureRequest] = []

    async def get_features(self, request: FeatureRequest) -> AnalyticsResult:
        self.requests.append(request)
        if not self._results:
            raise AssertionError("FakeEngine exhausted — test bug")
        return self._results.pop(0)


class FakeGate:
    """Scripted ApprovalGate (spec: store satisfies it structurally; fakes inject state)."""

    def __init__(self, state: str = "approved", *, error: BaseException | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._state = state
        self._error = error

    async def state(self, strategy_id: str, version: str) -> str:
        self.calls.append((strategy_id, version))
        if self._error is not None:
            raise self._error
        return self._state


def _runner(gate: FakeGate, engine: FakeEngine, *, allowlist: frozenset[str] = frozenset(),
            lifecycle: Lifecycle | None = None) -> StrategyRunner:
    registry = StrategyRegistry(package="modules.m3_analytics.strategies")
    registry.load()
    params = ParameterRegistry(_STRAT_DIR)
    params.load(registry.names())
    capability = CapabilityRegistry.from_yaml(_CAP_YAML)
    return StrategyRunner(
        registry=registry, params=params,
        lifecycle=lifecycle or Lifecycle(registry.names()),
        capability=capability, engine=engine, allowlist=allowlist,
        approvals=gate,  # Step 5 addition — RED (TypeError) until then
    )


def _runner_plain(engine: FakeEngine, *, lifecycle: Lifecycle | None = None) -> StrategyRunner:
    """Legacy construction WITHOUT approvals= — the 5 existing sites, default-off law."""
    registry = StrategyRegistry(package="modules.m3_analytics.strategies")
    registry.load()
    params = ParameterRegistry(_STRAT_DIR)
    params.load(registry.names())
    capability = CapabilityRegistry.from_yaml(_CAP_YAML)
    return StrategyRunner(
        registry=registry, params=params,
        lifecycle=lifecycle or Lifecycle(registry.names()),
        capability=capability, engine=engine,
    )


async def _run(runner: StrategyRunner, **over: object) -> Signal | None:
    base: dict[str, object] = {
        "market": _md(), "instrument_id": "FX.EURUSD", "broker": "deriv",
        "timeframe": "1m", "asset_class": "forex", "as_of": _TS, "now": _TS,
        "env": "paper",
    }
    base.update(over)
    return await runner.run(NAME, **base)  # type: ignore[arg-type]


# ==================================================================================
# A. Contract (Step 2 exists — GREEN expected at Step 3)
# ==================================================================================


def test_contract_roundtrip_all_six_columns() -> None:
    row = _approval(state="testing", changed_by="alice", note="first refinement run")
    assert row.strategy_id == NAME
    assert row.version == "1.0"
    assert row.state == "testing"
    assert row.changed_by == "alice"
    assert row.changed_at == datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    assert row.note == "first refinement run"
    assert row.model_config.get("frozen") is True


@pytest.mark.parametrize("state", STATES)
def test_contract_state_literal_accepts_exact_five(state: str) -> None:
    assert _approval(state=state).state == state


@pytest.mark.parametrize("fuzz", ["approved ", "APPROVED", "approved;--"])
def test_contract_state_fuzz_rejected_no_folding_no_trimming(fuzz: str) -> None:
    # spec Edges: Literal is exact — no case folding, no trimming, ever.
    with pytest.raises(ValidationError):
        _approval(state=fuzz)


def test_contract_changed_at_must_be_aware_utc() -> None:
    with pytest.raises(ValidationError):
        _approval(changed_at=datetime(2026, 10, 3, 12, 0))  # noqa: DTZ001 - naive under test
    assert _approval(changed_at=datetime(2026, 10, 3, 12, 0, tzinfo=UTC)).changed_at.tzinfo is UTC


def test_contract_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        _approval(state="draft", sneaky="inject")


def test_contract_version_is_required_strategy_version() -> None:
    # Overrides BaseContract.schema version by law (Signal.version precedent):
    # a row without a strategy version is nonsense, never defaulted to "1.0".
    base: dict[str, Any] = {
        "strategy_id": NAME, "state": "draft", "changed_by": "admin",
        "changed_at": datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
    }
    with pytest.raises(ValidationError):
        StrategyApproval(**base)  # type: ignore[call-arg]
    assert StrategyApproval(**base, version="2.1").version == "2.1"


def test_contract_strategy_state_literal_members_are_exactly_the_five() -> None:
    from typing import get_args

    assert set(get_args(StrategyState)) == set(STATES)


def test_contract_changed_by_and_note_enforced_at_boundary() -> None:
    with pytest.raises(ValidationError):
        _approval(changed_by="")
    with pytest.raises(ValidationError):
        _approval(changed_by="   ")  # strip -> empty -> rejected
    with pytest.raises(ValidationError):
        _approval(note="n" * 201)
    assert _approval(note="n" * 200).note is not None


# ==================================================================================
# B. State reads (spec Edges: state reads) — RED until Step 5
# ==================================================================================


async def test_state_no_row_returns_draft() -> None:
    store, _ = _store(known={NAME})
    assert await store.state(NAME, "1.0") == "draft"


async def test_get_no_row_returns_none() -> None:
    store, _ = _store(known={NAME})
    assert await store.get(NAME, "1.0") is None


async def test_state_row_roundtrip_all_six_columns() -> None:
    store, _ = _store(known={NAME})
    written = await store.set_state(NAME, "1.0", "testing", changed_by="alice",
                                    note="refinement")
    read = await store.get(NAME, "1.0")
    assert read is not None
    assert read == written
    for col in ("strategy_id", "version", "state", "changed_by", "changed_at", "note"):
        assert getattr(read, col) is not None, col
    assert read.state == "testing"
    assert read.changed_by == "alice"
    assert read.note == "refinement"


async def test_read_db_failure_raises_approval_store_error_never_a_default_state() -> None:
    m = _mod()
    db = FakeApprovalsDB(fail={"fetch", "fetchval"})
    store, _ = _store(db, known={NAME})
    with pytest.raises(m.ApprovalStoreError):
        await store.state(NAME, "1.0")
    with pytest.raises(m.ApprovalStoreError):
        await store.get(NAME, "1.0")


async def test_unwired_store_raises_approval_store_error_never_attribute_error() -> None:
    m = _mod()
    store = _unwired()
    with pytest.raises(m.ApprovalStoreError):
        await store.state(NAME, "1.0")


# ==================================================================================
# C. Transition matrix — all 25 (from, to) pairs (spec Correct #1: none untested)
# ==================================================================================


@pytest.mark.parametrize(
    ("frm", "to"),
    [(f, t) for f in STATES for t in STATES],
    ids=[f"{f}->{t}" for f in STATES for t in STATES],
)
async def test_transition_matrix(frm: str, to: str) -> None:
    m = _mod()
    store, db = _store(known={"t1"})
    pre = await _reach(store, "t1", "1.0", frm)
    writes_before = len(db.writes_since(0))

    if to == frm:  # idempotent no-op: returned row unchanged, NO write, set twice
        row = await store.set_state("t1", "1.0", to, changed_by="admin2")
        row2 = await store.set_state("t1", "1.0", to, changed_by="admin2")
        assert row.state == frm and row2.state == frm
        assert len(db.writes_since(0)) == writes_before
        if pre is not None:
            assert row == pre          # all columns unchanged, changed_by NOT re-recorded
            assert row2 == pre         # ... set twice
            assert await store.get("t1", "1.0") == pre
        else:  # implicit draft no-op: nothing materializes, nothing writes
            assert isinstance(row, StrategyApproval) and row.state == "draft"
            assert await store.get("t1", "1.0") is None
        return

    if to in LEGAL_MOVES[frm]:
        row = await store.set_state("t1", "1.0", to, changed_by="admin2", note="flip")
        assert isinstance(row, StrategyApproval)
        assert (row.strategy_id, row.version) == ("t1", "1.0")
        assert row.state == to
        assert row.changed_by == "admin2"
        assert row.note == "flip"
        assert row.changed_at.tzinfo is not None
        assert row.changed_at.utcoffset() == timedelta(0)
        if pre is not None:
            assert row.changed_at > pre.changed_at  # strictly increased
        assert await store.get("t1", "1.0") == row  # persisted exactly
        return

    # illegal move -> typed error carrying from/to + row unchanged + no write
    with pytest.raises(m.ApprovalTransitionError) as exc:
        await store.set_state("t1", "1.0", to, changed_by="admin2")
    msg = str(exc.value)
    assert frm in msg and to in msg  # spec: error carries from/to
    assert await store.get("t1", "1.0") == pre  # pre/post read equal
    assert len(db.writes_since(0)) == writes_before


# ==================================================================================
# D. Named transition edges (spec Edges: transitions — explicit names required)
# ==================================================================================


async def test_discontinued_to_approved_legal_human_law_2026_10_03() -> None:
    store, _ = _store(known={"t1"})
    await _reach(store, "t1", "1.0", "discontinued")
    row = await store.set_state("t1", "1.0", "approved", changed_by="admin",
                                note="restore by history")
    assert row.state == "approved"
    assert row.changed_by == "admin"


async def test_draft_to_approved_forbidden_dry_run_first() -> None:
    m = _mod()
    store, _ = _store(known={"t1"})  # no row = implicit draft
    with pytest.raises(m.ApprovalTransitionError):
        await store.set_state("t1", "1.0", "approved", changed_by="admin")
    assert await store.get("t1", "1.0") is None  # draft never materializes by failing


async def test_rejected_to_approved_forbidden_must_retest() -> None:
    m = _mod()
    store, _ = _store(known={"t1"})
    pre = await _reach(store, "t1", "1.0", "rejected")
    with pytest.raises(m.ApprovalTransitionError):
        await store.set_state("t1", "1.0", "approved", changed_by="admin")
    assert await store.get("t1", "1.0") == pre


async def test_two_strategies_may_hold_testing_concurrently_rev3() -> None:
    # REV3 (human 2026-10-07: "we should be able to test multiple strategies at a
    # time") — the former single-slot refusal is REMOVED; both rows must persist.
    store, _ = _store(known={"t1", "t2"})
    holder = await _reach(store, "t1", "1.0", "testing")
    second = await store.set_state("t2", "1.0", "testing", changed_by="admin")
    assert second.state == "testing"
    assert await store.get("t2", "1.0") == second  # second row written
    assert await store.get("t1", "1.0") == holder  # first row untouched


async def test_second_strategy_enters_testing_after_first_promotes() -> None:
    store, _ = _store(known={"t1", "t2"})
    await _reach(store, "t1", "1.0", "testing")
    await store.set_state("t1", "1.0", "approved", changed_by="admin")  # leaves testing
    entered = await store.set_state("t2", "1.0", "testing", changed_by="admin")
    assert entered.state == "testing"
    assert (await store.state("t1", "1.0")) == "approved"


async def test_version_bump_new_key_is_implicit_draft_old_key_untouched() -> None:
    store, _ = _store(known={NAME})
    old = await _reach(store, NAME, "1.0", "approved")
    # new key: no row -> draft (trader-hidden via list, blocked via draft cells)
    assert await store.state(NAME, "2.1") == "draft"
    assert await store.get(NAME, "2.1") is None
    trader_rows = await store.list_visible("trader")
    assert [(r.strategy_id, r.version) for r in trader_rows] == [(NAME, "1.0")]
    # old key's row untouched
    assert await store.get(NAME, "1.0") == old
    # admin iteration (registry x state) sees the new version as draft
    assert await store.state(NAME, "2.1") == "draft"


async def test_concurrent_double_testing_both_commit_rev3() -> None:
    # REV3: concurrent testing entries are legal — BOTH commit; per-row last-write
    # wins (§3.6) and no torn rows; the old exactly-one-wins law is removed.
    db = FakeApprovalsDB()
    store_a, _ = _store(db, known={"t1", "t2"})
    store_b, _ = _store(db, known={"t1", "t2"})  # second "connection", same table
    results = await asyncio.gather(
        store_a.set_state("t1", "1.0", "testing", changed_by="a"),
        store_b.set_state("t2", "1.0", "testing", changed_by="b"),
        return_exceptions=True,
    )
    errs = [r for r in results if isinstance(r, BaseException)]
    wins = [r for r in results if not isinstance(r, BaseException)]
    assert errs == [], results
    assert len(wins) == 2, results
    assert all(isinstance(w, StrategyApproval) and w.state == "testing" for w in wins)
    testing_keys = {k for k, r in db.rows.items() if r["state"] == "testing"}
    assert testing_keys == {("t1", "1.0"), ("t2", "1.0")}


async def test_explicit_draft_row_behaves_like_implicit_draft() -> None:
    m = _mod()
    store, _ = _store(known={"t1", "t2"})
    # t1: an EXPLICIT draft row arrives via the testing->draft undo
    await _reach(store, "t1", "1.0", "testing")
    undone = await store.set_state("t1", "1.0", "draft", changed_by="admin",
                                   note="undo start")
    assert undone.state == "draft"
    # ... and follows the same matrix as an implicit draft: a fresh flip is legal
    flipped = await store.set_state("t1", "1.0", "discontinued", changed_by="admin")
    assert flipped.state == "discontinued"
    # t2: explicit draft still cannot jump straight to approved (dry-run-first)
    await _reach(store, "t2", "1.0", "testing")
    await store.set_state("t2", "1.0", "draft", changed_by="admin")
    with pytest.raises(m.ApprovalTransitionError):
        await store.set_state("t2", "1.0", "approved", changed_by="admin")


# ==================================================================================
# E. Writes & validation (spec Edges: writes & validation)
# ==================================================================================


async def test_unknown_strategy_set_state_rejected_before_any_sql() -> None:
    m = _mod()
    store, db = _store(known={"t1"})
    with pytest.raises(m.UnknownStrategyApprovalError):
        await store.set_state("evil", "1.0", "testing", changed_by="admin")
    assert db.calls == []  # rejected BEFORE any SQL


async def test_unknown_strategy_in_seed_rejected_before_any_sql() -> None:
    m = _mod()
    store, db = _store(known={"t1"})
    with pytest.raises(m.UnknownStrategyApprovalError):
        await store.seed_legacy_approved([("evil", "1.0")], changed_by="seed:m4.8-init")
    assert db.calls == []


@pytest.mark.parametrize("bad_by", ["", "   "])
async def test_changed_by_empty_rejected_before_write(bad_by: str) -> None:
    store, db = _store(known={"t1"})
    with pytest.raises(ValidationError):
        await store.set_state("t1", "1.0", "testing", changed_by=bad_by)
    assert db.calls == []


async def test_note_201_chars_rejected_before_write_boundary_200_accepted() -> None:
    store, db = _store(known={"t1"})
    with pytest.raises(ValidationError):
        await store.set_state("t1", "1.0", "testing", changed_by="admin", note="n" * 201)
    assert db.calls == []
    ok = await store.set_state("t1", "1.0", "testing", changed_by="admin", note="n" * 200)
    assert ok.note == "n" * 200


@pytest.mark.parametrize("fuzz", ["approved ", "APPROVED", "unknown"])
async def test_new_state_fuzz_rejected_at_contract_boundary_never_reaches_sql(fuzz: str) -> None:
    store, db = _store(known={"t1"})
    with pytest.raises(ValidationError):
        await store.set_state("t1", "1.0", fuzz, changed_by="admin")
    assert db.calls == []


async def test_seed_twice_inserts_zero_and_never_downgrades_interim_flip() -> None:
    store, _ = _store(known={"t1", "t2"})
    entries = [("t1", "1.0"), ("t2", "1.0")]
    first = await store.seed_legacy_approved(entries, changed_by="seed:m4.8-init")
    assert first == 2
    await store.set_state("t1", "1.0", "rejected", changed_by="admin")  # interim flip
    second = await store.seed_legacy_approved(entries, changed_by="seed:m4.8-init")
    assert second == 0  # ON CONFLICT DO NOTHING
    assert (await store.state("t1", "1.0")) == "rejected"  # flip not clobbered
    assert (await store.state("t2", "1.0")) == "approved"


async def test_write_failure_mid_set_state_wrapped_row_unchanged() -> None:
    m = _mod()
    store, db = _store(known={"t1"})
    pre = await _reach(store, "t1", "1.0", "rejected")
    db.fail.add("execute")
    with pytest.raises(m.ApprovalStoreError):
        await store.set_state("t1", "1.0", "testing", changed_by="admin")
    db.fail.discard("execute")
    assert await store.get("t1", "1.0") == pre  # single-statement: no partial write


async def test_changed_at_system_stamped_not_caller_controllable() -> None:
    store, _ = _store(known={"t1"})
    # no changed_at parameter exists for a caller to forge/backdate (M1.5 law)
    assert "changed_at" not in inspect.signature(store.set_state).parameters
    first = await store.set_state("t1", "1.0", "testing", changed_by="admin")
    second = await store.set_state("t1", "1.0", "approved", changed_by="admin")
    for row in (first, second):
        assert row.changed_at.tzinfo is not None
        assert row.changed_at.utcoffset() == timedelta(0)  # UTC only
    # strictly increasing per store instance (monotonic clamp observable law; a
    # same-microsecond collision would be needed to hit the +1us branch itself)
    assert second.changed_at > first.changed_at


# ==================================================================================
# F. Visibility (spec Visibility: role-dependent, REV1)
# ==================================================================================


async def _one_row_per_state() -> tuple[Any, FakeApprovalsDB, dict[str, str]]:
    ids = {st: f"s_{st}" for st in STATES}
    store, db = _store(known=set(ids.values()))
    await store.set_state(ids["testing"], "1.0", "testing", changed_by="admin")
    await store.set_state(ids["approved"], "1.0", "testing", changed_by="admin")
    await store.set_state(ids["approved"], "1.0", "approved", changed_by="admin")
    await store.set_state(ids["rejected"], "1.0", "rejected", changed_by="admin")
    await store.set_state(ids["discontinued"], "1.0", "discontinued", changed_by="admin")
    # ids["draft"] intentionally has NO row — implicit draft (fail-closed)
    return store, db, ids


async def test_list_visible_trader_sees_approved_only() -> None:
    store, _, ids = await _one_row_per_state()
    rows = await store.list_visible("trader")
    assert [r.strategy_id for r in rows] == [ids["approved"]]
    assert all(r.state == "approved" for r in rows)


async def test_list_visible_admin_returns_all_explicit_rows() -> None:
    store, _, ids = await _one_row_per_state()
    rows = await store.list_visible("admin")
    assert {r.strategy_id for r in rows} == set(ids.values()) - {ids["draft"]}
    assert {r.state for r in rows} == set(STATES) - {"draft"}  # implicit draft has no row


async def test_admin_sees_every_state_via_registry_iteration_including_implicit_draft() -> None:
    store, _, ids = await _one_row_per_state()
    rows = await store.list_visible("admin")
    seen = {r.strategy_id: r.state for r in rows}
    # Correct #4: admin UIs iterate registry names x state() to surface drafts
    seen[ids["draft"]] = await store.state(ids["draft"], "1.0")
    assert set(seen.values()) == set(STATES)  # REV1: admin sees ALL 5 states
    assert seen[ids["draft"]] == "draft"


@pytest.mark.parametrize("bad", ["user", "root"])
async def test_list_visible_audience_unknown_role_rejected(bad: str) -> None:
    store, _ = _store(known={"t1"})
    with pytest.raises(ValidationError):
        await store.list_visible(bad)  # exact Literal: only trader and admin (REV1)


@pytest.mark.parametrize("bad", [None, 1])
async def test_list_visible_audience_non_string_rejected(bad: object) -> None:
    store, _ = _store(known={"t1"})
    with pytest.raises((ValidationError, TypeError)):
        await store.list_visible(bad)  # type: ignore[arg-type]


async def test_list_visible_empty_table_returns_empty_tuple() -> None:
    store, _ = _store(known={"t1"})
    assert await store.list_visible("trader") == ()
    assert await store.list_visible("admin") == ()


# ==================================================================================
# G. Runner gate (spec Runner gate: state x environment x authorization)
# ==================================================================================


def _cell_runs(state: str, env: str, audience: str) -> bool:
    """The spec runnability table, as code (spec Correct #2: exactly the table)."""
    if state == "draft":
        return False  # draft runs for NOBODY, ever, anywhere (REV2)
    if env == "prod":
        return state == "approved"  # approved is the only prod state
    return audience == "admin" or state == "approved"  # paper


_RUN_CELLS = [(st, env, aud) for st in STATES for env in ("paper", "prod")
              for aud in ("admin", "trader")]


@pytest.mark.parametrize(
    ("state", "env", "audience"),
    _RUN_CELLS,
    ids=[f"{st}-{env}-{aud}" for st, env, aud in _RUN_CELLS],
)
async def test_runnability_matrix(state: str, env: str, audience: str) -> None:
    engine = FakeEngine(_analytics())
    gate = FakeGate(state)
    runner = _runner(gate, engine)
    sig = await _run(runner, env=env, audience=audience)
    assert gate.calls == [(runner.params.strategy_id(NAME), runner.params.version(NAME))]
    if _cell_runs(state, env, audience):
        assert isinstance(sig, Signal)
        assert len(engine.requests) == 1  # RUN means the full path ran
        assert sig.shadow is (env == "prod")  # empty allowlist: prod => shadowed, kept
    else:
        assert sig is None
        assert engine.requests == []  # blocked BEFORE any feature I/O


async def test_default_audience_is_trader_testing_paper_blocked() -> None:
    engine = FakeEngine(_analytics())
    gate = FakeGate("testing")
    runner = _runner(gate, engine)
    sig = await _run(runner, env="paper")  # no audience kwarg -> trader default
    assert sig is None
    assert engine.requests == []


@pytest.mark.parametrize("bad", ["root", "user"])
async def test_unknown_audience_rejected_before_gate(bad: str) -> None:
    engine = FakeEngine(_analytics())
    gate = FakeGate("approved")
    runner = _runner(gate, engine)
    with pytest.raises(ValueError, match="audience"):
        await _run(runner, audience=bad)
    assert gate.calls == []  # _validate runs before the approval gate
    assert engine.requests == []


async def test_gate_store_error_returns_none_fail_closed_engine_untouched() -> None:
    m = _mod()
    engine = FakeEngine(_analytics())
    gate = FakeGate(error=m.ApprovalStoreError("simulated outage"))
    runner = _runner(gate, engine)
    sig = await _run(runner, env="paper", audience="admin")
    assert sig is None  # UNKNOWN store => REJECT (fail-closed)
    assert engine.requests == []
    assert len(gate.calls) == 1  # the gate WAS consulted; its answer was unreadable


async def test_approvals_none_legacy_behavior_unchanged_default_off() -> None:
    # spec: approvals=None -> byte-identical legacy behavior at the 5 existing sites.
    engine = FakeEngine(_analytics())
    runner = _runner_plain(engine)
    sig = await _run(runner, env="paper")
    assert isinstance(sig, Signal)
    assert len(engine.requests) == 1


async def test_gate_consulted_before_lifecycle_ordering() -> None:
    lifecycle = Lifecycle([NAME])
    lifecycle.disable(NAME)  # lifecycle would block anyway
    engine = FakeEngine(_analytics())
    gate = FakeGate("approved")
    runner = _runner(gate, engine, lifecycle=lifecycle)
    sig = await _run(runner, env="paper", audience="admin")
    assert sig is None  # lifecycle block wins the return value ...
    assert gate.calls != []  # ... but the approval gate ran FIRST (order: validate ->
    assert engine.requests == []  # approval -> lifecycle -> capability -> I/O)


async def test_gate_receives_registry_resolved_identity() -> None:
    engine = FakeEngine(_analytics())
    gate = FakeGate("approved")
    runner = _runner(gate, engine)
    sig = await _run(runner, env="paper", audience="admin")
    assert isinstance(sig, Signal)
    # sid/ver come from the PARAMETER registry, not the raw module name (Correct #5)
    assert gate.calls == [(runner.params.strategy_id(NAME), runner.params.version(NAME))]


async def test_approved_prod_still_passes_shadow_allowlist_last() -> None:
    runner_a = _runner(FakeGate("approved"), FakeEngine(_analytics()))
    sid = runner_a.params.strategy_id(NAME)
    ver = runner_a.params.version(NAME)
    sig_shadowed = await _run(runner_a, env="prod", audience="trader")
    assert isinstance(sig_shadowed, Signal)
    assert sig_shadowed.shadow is True  # gate approved, but allowlist is last (kept, not dropped)
    runner_b = _runner(
        FakeGate("approved"), FakeEngine(_analytics()),
        allowlist=frozenset({f"{sid}@{ver}"}),
    )
    sig_live = await _run(runner_b, env="prod", audience="trader")
    assert isinstance(sig_live, Signal)
    assert sig_live.shadow is False


# ==================================================================================
# H. Adversarial (spec Adversarial — beyond the edge list)
# ==================================================================================


async def test_sql_injection_strategy_id_rejected_table_intact() -> None:
    m = _mod()
    store, db = _store(known={"t1"})
    evil = "x'; DROP TABLE strategy_approvals; --"
    with pytest.raises(m.UnknownStrategyApprovalError):
        await store.set_state(evil, "1.0", "testing", changed_by="admin")
    assert db.calls == []  # rejected before SQL
    # reads stay parameterized no-ops — no foreign row, no SQL side effect
    assert await store.state(evil, "1.0") == "draft"
    assert await store.get(evil, "' OR '1'='1") is None
    # table intact + legit row unaffected
    await store.set_state("t1", "1.0", "testing", changed_by="admin")
    assert len(db.rows) == 1
    assert await store.state("t1", "1.0") == "testing"


async def test_sql_injection_version_param_read_no_foreign_row() -> None:
    store, db = _store(known={"t1"})
    await store.set_state("t1", "1.0", "testing", changed_by="admin")
    assert await store.state("t1", "' OR '1'='1") == "draft"  # literal key miss
    assert await store.get("t1", "' OR '1'='1") is None
    assert len(db.rows) == 1  # no row duplicated/leaked


@pytest.mark.parametrize("fuzz", [None, 1])
async def test_state_fuzz_none_or_int_never_reaches_sql(fuzz: object) -> None:
    store, db = _store(known={"t1"})
    with pytest.raises((ValidationError, TypeError)):
        await store.set_state("t1", "1.0", fuzz, changed_by="admin")  # type: ignore[arg-type]
    assert db.calls == []


async def test_concurrent_interleaved_writers_last_commit_wins_no_torn_row() -> None:
    m = _mod()
    db = FakeApprovalsDB()
    store_a, _ = _store(db, known={"t3"})
    store_b, _ = _store(db, known={"t3"})  # 2 connections, shared table
    results = await asyncio.gather(
        store_a.set_state("t3", "1.0", "rejected", changed_by="a"),
        store_b.set_state("t3", "1.0", "discontinued", changed_by="b"),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, BaseException):
            assert isinstance(r, m.ApprovalTransitionError)  # stale-read loser, typed
    assert len(results) == 2
    rows = list(db.rows.values())
    assert len(rows) == 1  # never duplicated
    # no torn row: state and changed_by always come from the SAME upsert
    assert (rows[0]["state"], rows[0]["changed_by"]) in {
        ("rejected", "a"), ("discontinued", "b"),
    }
    for col in ("strategy_id", "version", "changed_at"):
        assert col in rows[0] and rows[0][col] is not None
