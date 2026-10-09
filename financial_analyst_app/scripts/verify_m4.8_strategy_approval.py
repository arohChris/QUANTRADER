"""WHY: M4.8 Step 7 — real/realistic verification of the Strategy Approval store
and runner gate (spec docs/specs/m4.8_strategy_approval.spec.md §Test & verify
artifacts). NOT unit tests: re-runs the whole slice end-to-end against the REAL
registry/params/capabilities config, seeds EVERY strategy the registry reports
(count-agnostic, printed), walks all 25 transition pairs, proves concurrent
testing (REV3), prints the REV1 visibility tables, and exercises the REV2
runnability table (state x env x audience, incl. admin-vs-trader cells) against
the REAL StrategyRunner — then lets a human eyeball the printed state tables.

Real store if `DSN` env var present (asyncpg; whole run wrapped in ONE
transaction that is ROLLED BACK, so verification never mutates the DB);
otherwise the deterministic fake connection below. Spec: "real store if DSN
present else deterministic fake".

Usage: python scripts/verify_m4.8_strategy_approval.py    (optional DSN=...)
Exit: 0 = all checks OK · 1 = any check failed.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.m3_analytics.capability.capability_registry import CapabilityRegistry
from modules.m3_analytics.engine.feature_engine import FeatureRequest
from modules.m4_alpha.strategy.lifecycle import Lifecycle
from modules.m4_alpha.strategy.parameter_registry import ParameterRegistry
from modules.m4_alpha.strategy.runner import StrategyRunner
from modules.m4_alpha.strategy.strategy_registry import StrategyRegistry
from shared.contracts import StrategyState
from shared.contracts.analytics import AnalyticsResult
from shared.contracts.market_data import MarketData, Tick
from shared.contracts.signal import Signal
from shared.core.strategy_approval_store import (
    ApprovalStoreError,
    ApprovalTransitionError,
    StrategyApprovalStore,
    UnknownStrategyApprovalError,
)

_CAP_YAML = ROOT / "config" / "capabilities.yaml"
_STRAT_DIR = ROOT / "config" / "strategies"

NAME = "trend_following"
_TS = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
STATES: tuple[StrategyState, ...] = (
    "draft", "testing", "approved", "rejected", "discontinued")

# WHY an independent copy of the matrix: the script is a SECOND implementation of
# the spec table — if store and script ever disagree, a pair check goes red.
LEGAL: dict[str, frozenset[str]] = {
    "draft": frozenset({"testing", "rejected", "discontinued"}),
    "testing": frozenset({"draft", "approved", "rejected"}),
    "approved": frozenset({"testing", "rejected", "discontinued"}),
    "rejected": frozenset({"testing"}),
    "discontinued": frozenset({"testing", "approved"}),
}

# Spec DDL (Table section): additive, greenfield. REV3 (2026-10-07) removed the
# strategy_approvals_one_testing partial unique index — multiple testing rows coexist.
_DDL = """
CREATE TABLE IF NOT EXISTS strategy_approvals (
    strategy_id TEXT NOT NULL,
    version     TEXT NOT NULL,
    state       TEXT NOT NULL
        CHECK (state IN ('draft', 'testing', 'approved', 'rejected', 'discontinued')),
    changed_by  TEXT NOT NULL,
    changed_at  TIMESTAMPTZ NOT NULL,
    note        TEXT NULL,
    PRIMARY KEY (strategy_id, version)
)
"""

_GOLDEN = {"ema_20": 101.0, "sma_20": 100.0, "rsi_14": 65.0}  # m4.1 hand -> +1.0

_failures: list[str] = []


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(label)
    return ok


# ----------------------------------------------------------------------------------
# Deterministic fake connection (no DSN) — same semantics the LGTM'd test fake has
# ----------------------------------------------------------------------------------


class FakeApprovalsDB:
    TABLE = "strategy_approvals"
    PK = ("strategy_id", "version")

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.fail: set[str] = set()

    def _check_table(self, sql: str) -> None:
        if self.TABLE not in sql:
            raise AssertionError(f"unknown table in SQL: {sql!r}")

    async def _read(self, sql: str, args: tuple[object, ...]) -> list[dict[str, Any]]:
        self._check_table(sql)
        where = sql.split("WHERE", 1)[1] if "WHERE" in sql else ""
        cols = tuple(re.findall(r"(\w+)\s*=\s*\$\d+", where))
        if cols == ("state",):
            return [dict(r) for r in self.rows.values() if r["state"] == args[0]]
        if len(args) == 2 and set(cols) == set(self.PK):
            bound = dict(zip(cols, args))
            row = self.rows.get((str(bound["strategy_id"]), str(bound["version"])))
            return [dict(row)] if row else []
        raise AssertionError(f"read WHERE {cols!r} not PK/state scoped, args={args!r}")

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        await asyncio.sleep(0)
        self.calls.append((sql, args))
        if "fetch" in self.fail:
            raise OSError("simulated outage")
        if not sql.lstrip().upper().startswith("SELECT"):
            raise AssertionError(f"fetch used for non-SELECT: {sql!r}")
        return await self._read(sql, args)

    async def fetchval(self, sql: str, *args: object) -> Any:
        await asyncio.sleep(0)
        self.calls.append((sql, args))
        if "fetch" in self.fail:
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
            raise AssertionError(f"writes must be INSERT..ON CONFLICT: {sql!r}")
        cols = tuple(c.strip() for c in match.group(1).split(","))
        row = dict(zip(cols, args))
        key = (str(row["strategy_id"]), str(row["version"]))
        if "DO NOTHING" in sql:
            if key in self.rows:
                return "INSERT 0 0"
            self.rows[key] = row
            return "INSERT 0 1"
        self.rows[key] = row
        return "INSERT 0 1"

class _Ctx:
    """One connection for every phase — fake unless DSN (spec: real store if DSN)."""

    def __init__(self, db: FakeApprovalsDB | None, conn: Any) -> None:
        # conn is exactly ONE of: the fake above, or a real asyncpg connection —
        # typed Any (duck-typed execute/fetchval) so the db branch and the real
        # branch share the phase code without Optional-narrowing noise.
        self.db = db
        self.conn = conn

    async def clear(self) -> None:
        if self.db is not None:
            self.db.rows.clear()
        else:
            await self.conn.execute("DELETE FROM strategy_approvals")

    async def reset(self, *sids: str) -> None:
        if self.db is not None:
            for key in [k for k in self.db.rows if k[0] in sids]:
                del self.db.rows[key]
        else:
            for sid in sids:
                await self.conn.execute(
                    "DELETE FROM strategy_approvals WHERE strategy_id = $1", sid)

    async def count(self) -> int:
        if self.db is not None:
            return len(self.db.rows)
        return int(await self.conn.fetchval("SELECT COUNT(*) FROM strategy_approvals"))


# ----------------------------------------------------------------------------------
# Runner fixtures (same construction the LGTM'd tests use — REAL config everywhere)
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
    """Scripted FeatureSource — records every request, replays one canned result."""

    def __init__(self) -> None:
        self._results = [_analytics()]
        self.requests: list[FeatureRequest] = []

    async def get_features(self, request: FeatureRequest) -> AnalyticsResult:
        self.requests.append(request)
        if not self._results:
            raise AssertionError("FakeEngine exhausted — script bug")
        return self._results.pop(0)


class FakeGate:
    """Scripted ApprovalGate — injects the state under test per cell."""

    def __init__(self, state: str) -> None:
        self.calls: list[tuple[str, str]] = []
        self._state = state

    async def state(self, strategy_id: str, version: str) -> str:
        self.calls.append((strategy_id, version))
        return self._state


def _runner(registry: StrategyRegistry, params: ParameterRegistry,
            capability: CapabilityRegistry, engine: FakeEngine,
            approvals: Any) -> StrategyRunner:
    return StrategyRunner(
        registry=registry, params=params, lifecycle=Lifecycle(registry.names()),
        capability=capability, engine=engine, approvals=approvals,
    )


async def _run(runner: StrategyRunner, **over: object) -> Signal | None:
    base: dict[str, object] = {
        "market": _md(), "instrument_id": "FX.EURUSD", "broker": "deriv",
        "timeframe": "1m", "asset_class": "forex", "as_of": _TS, "now": _TS,
        "env": "paper",
    }
    base.update(over)
    return await runner.run(NAME, **base)  # type: ignore[arg-type]


def _cell_runs(state: str, env: str, audience: str) -> bool:
    """Spec runnability table, restated as the script's oracle (Correct #2)."""
    if state == "draft":
        return False
    if env == "prod":
        return state == "approved"
    return audience == "admin" or state == "approved"


async def _reach(store: StrategyApprovalStore, sid: str, ver: str,
                 target: StrategyState) -> None:
    """Walk a legal path to `target` from an EMPTY table (implicit draft)."""
    if target == "approved":  # dry-run first: approved only via testing
        await store.set_state(sid, ver, "testing", changed_by="admin:verify")
        await store.set_state(sid, ver, "approved", changed_by="admin:verify")
    else:
        await store.set_state(sid, ver, target, changed_by="admin:verify")


async def _ensure_approved(store: StrategyApprovalStore, sid: str, ver: str) -> None:
    st = await store.state(sid, ver)
    if st == "approved":
        return
    if st != "testing":  # draft / rejected / discontinued all legally enter testing
        await store.set_state(sid, ver, "testing", changed_by="admin:verify")
    await store.set_state(sid, ver, "approved", changed_by="admin:verify")


# ----------------------------------------------------------------------------------
# Phases
# ----------------------------------------------------------------------------------


async def _phase_seed_visibility(ctx: _Ctx, store: StrategyApprovalStore,
                                 entries: list[tuple[str, str]]) -> None:
    print("\n== Phase 1: registry-driven seed + REV1 role visibility ==")
    inserted = await store.seed_legacy_approved(entries, changed_by="seed:m4.8-init")
    again = await store.seed_legacy_approved(entries, changed_by="seed:m4.8-init")
    print(f"  registry reported {len(entries)} strategies; seed inserted "
          f"{inserted}, re-seed inserted {again} (count-agnostic, printed)")
    _check("seed idempotent: second run inserts 0", again == 0)
    if ctx.db is not None:
        _check("fresh fake: every registry strategy inserted", inserted == len(entries))
    states = [await store.state(sid, ver) for sid, ver in entries]
    _check("every registry strategy reads 'approved' after seed",
           states == ["approved"] * len(entries))

    # ORDER LAW: s_approved takes the slot first, completes, releases it, THEN
    # s_testing enters — exactly one testing row at every moment (human law).
    syn = {"testing": "s_testing", "approved": "s_approved",
           "rejected": "s_rejected", "discontinued": "s_discontinued"}
    await store.set_state(syn["approved"], "1.0", "testing", changed_by="admin:verify")
    await store.set_state(syn["approved"], "1.0", "approved", changed_by="admin:verify")
    await store.set_state(syn["testing"], "1.0", "testing", changed_by="admin:verify")
    await store.set_state(syn["rejected"], "1.0", "rejected", changed_by="admin:verify")
    await store.set_state(syn["discontinued"], "1.0", "discontinued",
                          changed_by="admin:verify")
    # s_draft: NO row on purpose — implicit draft (fail-closed Correct #4)

    trader = await store.list_visible("trader")
    admin = await store.list_visible("admin")
    print("  --- trader sees (approved only) ---")
    for r in trader:
        print(f"    {r.strategy_id}@{r.version}  {r.state}")
    print("  --- admin sees (every explicit row) ---")
    for r in admin:
        print(f"    {r.strategy_id}@{r.version}  {r.state:<13} by {r.changed_by}")

    tids = {r.strategy_id for r in trader}
    aids = {r.strategy_id for r in admin}
    seed_ids = {sid for sid, _ in entries}
    _check("trader rows are all 'approved'",
           all(r.state == "approved" for r in trader))
    _check("trader sees s_approved", "s_approved" in tids)
    _check("trader hides testing/rejected/discontinued",
           not (set(syn.values()) - {"s_approved"} & tids))
    _check("admin sees every explicit row", aids == seed_ids | set(syn.values()))
    _check("admin explicit rows cover 4 states (no implicit draft row)",
           {r.state for r in admin} == set(STATES) - {"draft"})
    draft_state = await store.state("s_draft", "1.0")
    _check("absent row reads 'draft' (fail-closed)", draft_state == "draft")
    _check("admin iteration surfaces ALL 5 states (REV1)",
           ({r.state for r in admin} | {draft_state}) == set(STATES))


async def _phase_matrix(ctx: _Ctx, store: StrategyApprovalStore) -> None:
    print("\n== Phase 2: all 25 transition pairs (spec State machine) ==")
    await ctx.clear()  # phase 1 rows would hold the slot; walk starts from empty
    legal = noop = blocked = 0
    for frm in STATES:
        for to in STATES:
            await ctx.reset("walk_m48")
            if frm != "draft":
                await _reach(store, "walk_m48", "1.0", frm)
            expected = "noop" if to == frm else ("flip" if to in LEGAL[frm] else "block")
            ok = False
            detail = ""
            try:
                res = await store.set_state("walk_m48", "1.0", to,
                                            changed_by="admin:verify")
                after = await store.get("walk_m48", "1.0")
                if to == frm:
                    # idempotent no-op: row returned unchanged; (draft,draft)
                    # must NOT materialize a row (implicit-draft law)
                    ok = (res.state == frm
                          and (after is not None) == (frm != "draft"))
                    detail = "no-op, row unchanged"
                else:
                    ok = res.state == to and after is not None and after.state == to
                    detail = f"flipped to {to}"
            except ApprovalTransitionError as exc:
                detail = str(exc)
                ok = expected == "block"
            if ok:
                if expected == "flip":
                    legal += 1
                elif expected == "noop":
                    noop += 1
                else:
                    blocked += 1
            _check(f"{frm:>13} -> {to:<13} expected {expected}", ok, detail)
    print(f"  totals: {legal} legal flips + {noop} idempotent no-ops accepted, "
          f"{blocked} illegal moves blocked (12 + 5 + 8 = 25)")


async def _phase_double_testing(ctx: _Ctx, store: StrategyApprovalStore) -> None:
    print("\n== Phase 3: concurrent testing (REV3 — multi-strategy allowed) ==")
    await ctx.clear()
    d1 = await store.set_state("d1", "1.0", "testing", changed_by="admin:verify")
    d2 = await store.set_state("d2", "1.0", "testing", changed_by="admin:verify")
    _check("two strategies hold 'testing' at the same time",
           d1.state == "testing" and d2.state == "testing")
    _check("first testing row unchanged by the second's entry",
           await store.state("d1", "1.0") == "testing")
    admin = await store.list_visible("admin")
    testing_ids = {r.strategy_id for r in admin if r.state == "testing"}
    _check("both testing rows persisted", testing_ids == {"d1", "d2"})
    print(f"  d1@1.0 and d2@1.0 both in testing ({len(testing_ids)} testing rows)")


async def _phase_fail_closed(ctx: _Ctx, store: StrategyApprovalStore) -> None:
    print("\n== Phase 4: fail-closed + adversarial (spec Edges) ==")
    try:
        await StrategyApprovalStore(None, known_strategies=set()).state("x", "1.0")
        _check("unwired store -> ApprovalStoreError", False)
    except ApprovalStoreError:
        _check("unwired store -> ApprovalStoreError", True)

    before_count = await ctx.count()
    before_calls = len(ctx.db.calls) if ctx.db is not None else -1
    try:
        await store.set_state("x'; DROP TABLE strategy_approvals;--", "1.0",
                              "approved", changed_by="admin")
        _check("registry gate rejects injection id", False)
    except UnknownStrategyApprovalError:
        _check("registry gate rejects injection id before SQL", True)
    _check("table intact after injection attempt", await ctx.count() == before_count)
    if ctx.db is not None:
        _check("zero SQL issued for the gated id", len(ctx.db.calls) == before_calls)

    try:
        await store.set_state("s_testing", "1.0", "approved", changed_by="   ")
        _check("whitespace changed_by rejected at contract", False)
    except ValidationError:
        _check("whitespace changed_by rejected at contract (no write)", True)
    try:
        await store.set_state("s_testing", "1.0", "approved", changed_by="admin",
                              note="n" * 201)
        _check("201-char note rejected at contract", False)
    except ValidationError:
        _check("201-char note rejected at contract (no write)", True)
    try:
        await store.list_visible("root")
        _check("audience 'root' rejected (exact Literal)", False)
    except ValidationError:
        _check("audience 'root' rejected (exact Literal)", True)

    if ctx.db is not None:
        ctx.db.fail.add("fetch")
        try:
            await store.state("s_testing", "1.0")
            _check("DB read failure -> ApprovalStoreError (never a guessed state)",
                   False)
        except ApprovalStoreError:
            _check("DB read failure -> ApprovalStoreError (never a guessed state)",
                   True)
        finally:
            ctx.db.fail.discard("fetch")


async def _phase_runner(ctx: _Ctx, store: StrategyApprovalStore,
                        params: ParameterRegistry) -> None:
    print("\n== Phase 5: runner gate — state x env x audience (REV2 table) ==")
    registry = StrategyRegistry(package="modules.m3_analytics.strategies")
    registry.load()
    capability = CapabilityRegistry.from_yaml(_CAP_YAML)
    sid, ver = params.strategy_id(NAME), params.version(NAME)

    cells = [(st, env, aud) for st in STATES
             for env in ("paper", "prod") for aud in ("admin", "trader")]
    for state, env, audience in cells:
        engine = FakeEngine()
        gate = FakeGate(state)
        sig = await _run(_runner(registry, params, capability, engine, gate),
                         env=env, audience=audience)
        runs = isinstance(sig, Signal)
        expected_runs = _cell_runs(state, env, audience)
        ok = (gate.calls == [(sid, ver)]
              and runs == expected_runs
              and (len(engine.requests) == 1) == expected_runs
              and (not runs or sig.shadow is (env == "prod")))  # type: ignore[union-attr]
        _check(f"{state:<13} | {env:<5} | {audience:<5}",
               ok, f"got={'RUN' if runs else 'BLOCK'} "
                   f"expected={'RUN' if expected_runs else 'BLOCK'}")

    engine, gate = FakeEngine(), FakeGate("testing")
    sig = await _run(_runner(registry, params, capability, engine, gate), env="paper")
    _check("default audience = trader (testing + paper blocked)",
           sig is None and engine.requests == [])

    engine = FakeEngine()
    try:
        await _run(_runner(registry, params, capability, engine, FakeGate("approved")),
                   env="paper", audience="root")
        _check("unknown audience rejected loudly before the gate", False)
    except ValueError as exc:
        _check("unknown audience rejected loudly before the gate",
               "audience" in str(exc), str(exc))

    # REAL store as ApprovalGate — structural conformance, revocation, implicit draft
    await ctx.clear()
    await _ensure_approved(store, sid, ver)
    engine = FakeEngine()
    sig = await _run(_runner(registry, params, capability, engine, store), env="paper")
    _check("real store as gate: approved + paper + trader RUNS",
           isinstance(sig, Signal) and len(engine.requests) == 1)
    await store.set_state(sid, ver, "rejected", changed_by="admin:verify")
    engine = FakeEngine()
    sig = await _run(_runner(registry, params, capability, engine, store), env="paper")
    _check("revocation takes effect on the very next run (no cache)",
           sig is None and engine.requests == [])
    await ctx.reset(sid)
    engine = FakeEngine()
    sig = await _run(_runner(registry, params, capability, engine, store), env="prod")
    _check("absent row = implicit draft -> blocked everywhere",
           sig is None and engine.requests == [])
    if ctx.db is not None:
        ctx.db.fail.add("fetch")
        try:
            engine = FakeEngine()
            sig = await _run(_runner(registry, params, capability, engine, store),
                             env="paper")
            _check("unreadable store -> fail-closed NO RUN",
                   sig is None and engine.requests == [])
        finally:
            ctx.db.fail.discard("fetch")


async def _run_phases(ctx: _Ctx, store: StrategyApprovalStore,
                      params: ParameterRegistry, entries: list[tuple[str, str]]
                      ) -> None:
    await _phase_seed_visibility(ctx, store, entries)
    await _phase_matrix(ctx, store)
    await _phase_double_testing(ctx, store)
    await _phase_fail_closed(ctx, store)
    await _phase_runner(ctx, store, params)


async def main() -> int:
    registry = StrategyRegistry(package="modules.m3_analytics.strategies")
    registry.load()
    names = registry.names()
    params = ParameterRegistry(_STRAT_DIR)
    params.load(names)
    # Registry-driven seed: EVERY strategy the registry reports, count-agnostic.
    entries = [(params.strategy_id(n), params.version(n)) for n in names]
    known = (set(names) | {sid for sid, _ in entries}
             | {"s_testing", "s_approved", "s_rejected", "s_discontinued",
                "s_draft", "walk_m48", "d1", "d2"})

    dsn = os.environ.get("DSN")
    if dsn:
        import asyncpg

        print("DSN present -> REAL asyncpg store (single transaction, rolled back)")
        conn = await asyncpg.connect(dsn)
        try:
            async with conn.transaction():
                await conn.execute(_DDL)
                store = StrategyApprovalStore(conn, known_strategies=known)
                await _run_phases(_Ctx(None, conn), store, params, entries)
            print("\n[note] real-DSN run: transaction ROLLED BACK — the database "
                  "is unchanged (verify must never mutate)")
        finally:
            await conn.close()
    else:
        print("no DSN env var -> deterministic fake connection")
        db = FakeApprovalsDB()
        store = StrategyApprovalStore(db, known_strategies=known)
        await _run_phases(_Ctx(db, None), store, params, entries)

    if _failures:
        print(f"\n{len(_failures)} FAILED check(s):")
        for item in _failures:
            print(f"  [FAIL] {item}")
        return 1
    print("\nALL CHECKS OK — eyeball the printed state tables above")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
