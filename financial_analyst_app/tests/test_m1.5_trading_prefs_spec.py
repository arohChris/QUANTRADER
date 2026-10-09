"""
M1.5 — Trading Preferences + Strategy Subscriptions — spec tests (REV4)

Written AGAINST docs/specs/m1.5_trading_preferences.spec.md BEFORE implementation
(AGENTS.md §2 Step 3). Contracts exist (Step 2) and may be imported at module level;
the helpers (`next_run_at`, `run_decision`) and the repository (`TradingPrefsStore`)
do NOT exist yet — those tests import lazily so each one fails individually (honest
RED) until Step 5. Money math: none in this sub-module (spec: no prices/PnL).
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from shared.contracts import StrategyAdmin, StrategySubscription, TradingPreferences
from shared.contracts.trading_preferences import TimeframeKey
from shared.timeframes import INTERVAL_MINUTES

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


def _prefs(**over: Any) -> TradingPreferences:
    over.setdefault("user_id", "u1")
    over.setdefault("broker_id", "deriv")
    over.setdefault("activation_at", NOW)
    over.setdefault("updated_at", NOW)
    return TradingPreferences(**over)  # type: ignore[arg-type]


def _sub(**over: Any) -> StrategySubscription:
    over.setdefault("user_id", "u1")
    over.setdefault("broker_id", "deriv")
    over.setdefault("strategy_id", "trend_following")
    over.setdefault("updated_at", NOW)
    return StrategySubscription(**over)  # type: ignore[arg-type]


def _admin(**over: Any) -> StrategyAdmin:
    over.setdefault("strategy_id", "trend_following")
    over.setdefault("updated_at", NOW)
    return StrategyAdmin(**over)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------------
# FakePrefsDB — in-memory 3-table stand-in (m2.10 FakeConn style, but routes by SQL
# so scoping/parameterization are actually verified, not assumed)
# ----------------------------------------------------------------------------------


class FakePrefsDB:
    """Routes real SQL text to 3 dicts. ENFORCES the storage laws it claims to test:

    - SELECT WHERE column list must match the table's full PK (user+broker scope) —
      a missing broker_id in WHERE fails loudly, not silently.
    - writes must be UPSERTs (ON CONFLICT): DO NOTHING = no clobber, DO UPDATE = replace.
    - every (sql, params) recorded so tests can prove injection strings stay params.
    - fail={"fetch"|"execute"|"fetchval"} raises OSError("simulated outage").
    """

    TABLES = ("trading_preferences", "strategy_subscriptions", "strategy_admin")
    PKS: ClassVar[dict[str, tuple[str, ...]]] = {
        "trading_preferences": ("user_id", "broker_id"),
        "strategy_subscriptions": ("user_id", "broker_id", "strategy_id"),
        "strategy_admin": ("strategy_id",),
    }

    def __init__(self, fail: set[str] | None = None) -> None:
        self.rows: dict[str, dict[tuple[str, ...], dict[str, Any]]] = {t: {} for t in self.TABLES}
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.fail: set[str] = set(fail or ())

    def _table(self, sql: str) -> str:
        for name in self.TABLES:
            if name in sql:
                return name
        raise AssertionError(f"FakePrefsDB: no known table in SQL: {sql!r}")

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.calls.append((sql, args))
        if "fetch" in self.fail:
            raise OSError("simulated outage")
        table = self._table(sql)
        if not sql.lstrip().upper().startswith("SELECT"):
            raise AssertionError(f"fetch used for non-SELECT: {sql!r}")
        where = sql.split("WHERE", 1)[1] if "WHERE" in sql else ""
        where_cols = tuple(re.findall(r"(\w+)\s*=\s*\$\d+", where))
        pk = self.PKS[table]
        if where_cols != pk:
            raise AssertionError(
                f"read WHERE {where_cols} must cover full scope {pk} (storage law §4.3)"
            )
        key = tuple(str(a) for a in args)
        if len(key) != len(pk):
            raise AssertionError(f"read for {table} needs {len(pk)} params, got {len(args)}")
        row = self.rows[table].get(key)
        return [dict(row)] if row else []

    async def fetchval(self, sql: str, *args: object) -> Any:
        self.calls.append((sql, args))
        if "fetchval" in self.fail:
            raise OSError("simulated outage")
        if "COUNT" in sql.upper():
            return len(self.rows[self._table(sql)])
        got = await self.fetch(sql, *args)
        return next(iter(got[0].values()), None) if got else None

    async def execute(self, sql: str, *args: object) -> str:
        self.calls.append((sql, args))
        if "execute" in self.fail:
            raise OSError("simulated outage")
        table = self._table(sql)
        match = re.search(r"INSERT INTO \w+ \(([^)]*)\)", sql)
        if not match:
            raise AssertionError(f"writes must be INSERT..ON CONFLICT upserts: {sql!r}")
        cols = tuple(c.strip() for c in match.group(1).split(","))
        if len(cols) != len(args):
            raise AssertionError(f"column/param mismatch: {len(cols)} cols vs {len(args)} params")
        row = dict(zip(cols, args))
        key = tuple(str(row[k]) for k in self.PKS[table])
        if "DO NOTHING" in sql:
            if key in self.rows[table]:
                return "INSERT 0 0"  # idempotent ensure — existing row untouched
            self.rows[table][key] = row
            return "INSERT 0 1"
        if "DO UPDATE" in sql:
            self.rows[table][key] = row
            return "INSERT 0 1"
        raise AssertionError(f"write missing ON CONFLICT clause: {sql!r}")


def _store(
    db: FakePrefsDB | None = None, *, known: set[str] | None = None
) -> tuple[Any, FakePrefsDB]:
    from shared.core.trading_prefs_store import TradingPrefsStore

    db = FakePrefsDB() if db is None else db
    store = TradingPrefsStore(db, known_strategies=known)
    return store, db


def _next_run(activation: datetime, timeframe: str, now: datetime) -> datetime:
    from shared.core.analysis_schedule import next_run_at

    return next_run_at(activation, timeframe, now)


def _run_decision(**kwargs: Any) -> bool:
    from shared.core.trading_run_rule import run_decision

    return run_decision(**kwargs)


# ----------------------------------------------------------------------------------
# Contracts (Step 2 exists — GREEN expected at Step 3)
# ----------------------------------------------------------------------------------


def test_contracts_frozen_version_1_extra_forbid_and_correlation_ids() -> None:
    objs = [_prefs(), _sub(), _admin()]
    ids = [o.correlation_id for o in objs]
    assert len(set(ids)) == 3, "correlation_id must be unique per contract"
    for obj in objs:
        assert obj.version == "1.0"
        with pytest.raises(ValidationError):
            obj.updated_at = NOW  # type: ignore[misc] — frozen
        with pytest.raises(ValidationError):
            type(obj)(**obj.model_dump(), typo_field="nope")  # extra=forbid


def test_timeframe_calendar_bucket_rejected() -> None:
    with pytest.raises(ValidationError):
        _prefs(timeframe="1w")  # spec: calendar buckets rejected (registry law)


def test_timeframe_injection_string_rejected() -> None:
    with pytest.raises(ValidationError):
        _prefs(timeframe="1m; DROP TABLE x")


def test_timeframe_registry_parity_with_shared_timeframes() -> None:
    assert set(TimeframeKey.__args__) == set(INTERVAL_MINUTES), (
        "contract keys drifted from registry"
    )
    assert len(TimeframeKey.__args__) == 9, "registry is 9 keys (ACTION 35/36)"


def test_params_json_array_rejected() -> None:
    with pytest.raises(ValidationError):
        _sub(params=[1, 2])  # spec: params must be a JSON object


def test_params_json_string_rejected() -> None:
    with pytest.raises(ValidationError):
        _sub(params="nope")


def test_naive_activation_at_rejected() -> None:
    with pytest.raises(ValidationError):
        _prefs(activation_at=datetime(2026, 9, 30, 12, 0, 0))  # noqa: DTZ001 — deliberately naive input for the §3.8 rejection test


def test_activation_at_before_2000_rejected() -> None:
    with pytest.raises(ValidationError):
        _prefs(activation_at=datetime(1999, 1, 1, tzinfo=UTC))  # sane bound §3.13


def test_tenant_scope_fields_required() -> None:
    with pytest.raises(ValidationError):
        TradingPreferences(  # type: ignore[call-arg] — deliberately missing broker_id
            user_id="u1", activation_at=NOW, updated_at=NOW
        )  # broker_id missing — scope is structural
    with pytest.raises(ValidationError):
        StrategySubscription(  # type: ignore[call-arg] — deliberately missing user_id
            broker_id="deriv", strategy_id="trend_following", updated_at=NOW
        )  # user_id missing


def test_contract_defaults_match_lock_c_and_human_defaults() -> None:
    p, s, a = _prefs(), _sub(), _admin()
    assert p.timeframe == "1m", "default timeframe confirmed '1m' (flag ii)"
    assert p.auto_trade is False, "auto_trade default FALSE — nothing runs until clicked"
    assert s.user_disabled is False, "enabled-by-default (lock c)"
    assert s.params == {} and s.instruments == (), "empty instruments = trade nothing"
    assert a.admin_approved is True, "global approval default TRUE (available to all)"


# ----------------------------------------------------------------------------------
# Run rule — pure helper, RED until Step 5
# ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("admin", "auto", "disabled", "expected"),
    [
        (True, True, False, True),  # 1 of 8: only fully-open row runs
        (True, True, True, False),
        (True, False, False, False),  # default state: auto_trade OFF
        (True, False, True, False),
        (False, True, False, False),  # global admin gate wins over everything
        (False, True, True, False),
        (False, False, False, False),
        (False, False, True, False),
    ],
)
def test_run_rule_truth_table(admin: bool, auto: bool, disabled: bool, expected: bool) -> None:
    got = _run_decision(
        admin_approved=admin,
        auto_trade=auto,
        user_disabled=disabled,
        instruments=("EURUSD",),
    )
    assert got is expected


def test_run_denied_when_no_asset_selected() -> None:
    assert (
        _run_decision(admin_approved=True, auto_trade=True, user_disabled=False, instruments=())
        is False
    ), "empty instruments = trade nothing (flag iv)"


def test_run_allowed_with_multiple_selected_assets() -> None:
    assert (
        _run_decision(
            admin_approved=True,
            auto_trade=True,
            user_disabled=False,
            instruments=("EURUSD", "XAUUSD"),
        )
        is True
    )


def test_global_admin_false_denies_every_user_regardless_of_their_state() -> None:
    # admin approval is GLOBAL: two users with opposite personal states, same denial
    for user_state in ({"user_disabled": False}, {"user_disabled": True}):
        assert (
            _run_decision(
                admin_approved=False,
                auto_trade=True,
                instruments=("EURUSD",),
                **user_state,  # type: ignore[arg-type]
            )
            is False
        )


# ----------------------------------------------------------------------------------
# Schedule — pure helper, RED until Step 5
# ----------------------------------------------------------------------------------


def test_future_activation_returns_activation_itself() -> None:
    activation = NOW + timedelta(hours=2)
    assert _next_run(activation, "1h", NOW) == activation


@pytest.mark.parametrize(
    ("tf", "now", "expected"),
    [
        (
            "1m",
            datetime(2026, 9, 30, 6, 5, 0, tzinfo=UTC),
            datetime(2026, 9, 30, 6, 5, 0, tzinfo=UTC),
        ),
        (
            "1m",
            datetime(2026, 9, 30, 6, 5, 30, tzinfo=UTC),
            datetime(2026, 9, 30, 6, 6, 0, tzinfo=UTC),
        ),
        (
            "1h",
            datetime(2026, 9, 30, 8, 30, 0, tzinfo=UTC),
            datetime(2026, 9, 30, 9, 0, 0, tzinfo=UTC),
        ),
        (
            "1h",
            datetime(2026, 9, 30, 9, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 30, 9, 0, 0, tzinfo=UTC),
        ),
    ],
)
def test_past_activation_returns_smallest_instant_gte_now(
    tf: str, now: datetime, expected: datetime
) -> None:
    activation = datetime(2026, 9, 30, 6, 0, 0, tzinfo=UTC)
    assert _next_run(activation, tf, now) == expected


def test_run_instants_land_exactly_on_anchor_plus_n_tf() -> None:
    activation = datetime(2026, 9, 30, 6, 0, 0, tzinfo=UTC)
    for n in (0, 1, 2):  # spec: n = 0,1,2 asserted
        instant = activation + timedelta(hours=n)
        assert _next_run(activation, "1h", instant) == instant  # on-the-boundary fires
        assert _next_run(activation, "1h", instant + timedelta(minutes=1)) == instant + timedelta(
            hours=1
        )


def test_tf_change_keeps_anchor_recomputes_cadence() -> None:
    activation = datetime(2026, 9, 30, 6, 0, 0, tzinfo=UTC)
    now = datetime(2026, 9, 30, 12, 1, 30, tzinfo=UTC)
    assert _next_run(activation, "1m", now) == datetime(2026, 9, 30, 12, 2, 0, tzinfo=UTC)
    assert _next_run(activation, "1h", now) == datetime(2026, 9, 30, 13, 0, 0, tzinfo=UTC)


# ----------------------------------------------------------------------------------
# Repository — RED until Step 5 (lazy imports)
# ----------------------------------------------------------------------------------


async def test_no_preferences_row_returns_none() -> None:
    store, _ = _store()
    assert await store.read_preferences("u1", "deriv") is None


async def test_no_subscription_row_returns_none() -> None:
    store, _ = _store(known={"trend_following"})
    assert await store.read_subscription("u1", "deriv", "trend_following") is None


async def test_admin_absent_row_defaults_true() -> None:
    store, _ = _store(known={"trend_following"})
    assert (await store.admin_approved("trend_following")) is True


async def test_ensure_at_sign_in_with_broker_materializes_defaults() -> None:
    store, db = _store()
    prefs = await store.ensure_at_sign_in("u1", "deriv")
    assert prefs.timeframe == "1m" and prefs.auto_trade is False
    assert ("u1", "deriv") in db.rows["trading_preferences"]


async def test_ensure_at_sign_in_without_broker_writes_nothing() -> None:
    store, db = _store()
    assert await store.ensure_at_sign_in("u1") is None
    assert db.rows["trading_preferences"] == {}, "PK needs a broker — held until connect"


async def test_ensure_at_sign_in_idempotent_no_clobber() -> None:
    store, db = _store()
    await store.ensure_at_sign_in("u1", "deriv")
    await store.set_timeframe("u1", "deriv", "4h")
    await store.ensure_at_sign_in("u1", "deriv")  # second sign-in
    assert (await store.read_preferences("u1", "deriv")).timeframe == "4h"
    assert len(db.rows["trading_preferences"]) == 1


async def test_broker_connect_preserves_user_set_timeframe() -> None:
    store, _ = _store()
    await store.ensure_at_broker_connect("u1", "deriv")
    await store.set_timeframe("u1", "deriv", "4h")
    await store.ensure_at_broker_connect("u1", "deriv")  # reconnect — added, never clobbered
    assert (await store.read_preferences("u1", "deriv")).timeframe == "4h"


async def test_second_broker_row_created_from_template_first_untouched() -> None:
    store, db = _store()
    await store.ensure_at_broker_connect("u1", "deriv")
    await store.set_timeframe("u1", "deriv", "4h")
    prefs2 = await store.ensure_at_broker_connect("u1", "mt5")
    assert prefs2.timeframe == "1m", "new broker gets the template defaults"
    assert (await store.read_preferences("u1", "deriv")).timeframe == "4h"
    assert len(db.rows["trading_preferences"]) == 2


async def test_cross_tenant_read_never_returns_other_users_row() -> None:
    store, _ = _store()
    await store.ensure_at_broker_connect("u1", "deriv")
    await store.set_timeframe("u1", "deriv", "4h")
    await store.ensure_at_broker_connect("u2", "deriv")
    got = await store.read_preferences("u2", "deriv")
    assert got.timeframe == "1m", "u2 gets u2's row — never u1's 4h"


async def test_cross_broker_read_isolated_for_same_user() -> None:
    store, _ = _store()
    await store.ensure_at_broker_connect("u1", "deriv")
    await store.ensure_at_broker_connect("u1", "mt5")
    await store.set_timeframe("u1", "deriv", "4h")
    assert (await store.read_preferences("u1", "mt5")).timeframe == "1m"


async def test_set_timeframe_upserts_single_row_last_write_wins() -> None:
    store, db = _store()
    p1 = await store.set_timeframe("u1", "deriv", "15m")
    p2 = await store.set_timeframe("u1", "deriv", "4h")
    assert p2.timeframe == "4h"
    assert p1.updated_at <= p2.updated_at, "updated_at auto-advanced"
    assert len(db.rows["trading_preferences"]) == 1


async def test_set_timeframe_unknown_key_rejected() -> None:
    store, _ = _store()
    with pytest.raises(ValidationError):
        await store.set_timeframe("u1", "deriv", "3d")


async def test_subscription_round_trip() -> None:
    store, _ = _store(known={"trend_following"})
    await store.upsert_subscription(
        _sub(instruments=("EURUSD",), user_disabled=True, params={"ema_fast": 8})
    )
    got = await store.read_subscription("u1", "deriv", "trend_following")
    assert got.instruments == ("EURUSD",)
    assert got.user_disabled is True
    assert got.params == {"ema_fast": 8}


async def test_cross_tenant_subscription_isolation() -> None:
    store, _ = _store(known={"trend_following"})
    await store.upsert_subscription(_sub(user_disabled=True))
    assert await store.read_subscription("u2", "deriv", "trend_following") is None
    assert (await store.read_subscription("u1", "deriv", "trend_following")).user_disabled is True


async def test_unknown_strategy_write_rejected_typed_error() -> None:
    from shared.core.trading_prefs_store import UnknownStrategyStoreError

    store, _ = _store(known={"trend_following"})
    with pytest.raises(UnknownStrategyStoreError):
        await store.upsert_subscription(_sub(strategy_id="not_registered"))
    with pytest.raises(UnknownStrategyStoreError):
        await store.set_admin("not_registered", False)


async def test_db_error_on_read_raises_store_error() -> None:
    from shared.core.trading_prefs_store import StoreError

    store, _ = _store(db=FakePrefsDB(fail={"fetch"}))
    with pytest.raises(StoreError):
        await store.read_preferences("u1", "deriv")


async def test_repository_not_wired_raises_store_error() -> None:
    from shared.core.trading_prefs_store import StoreError, TradingPrefsStore

    store = TradingPrefsStore(None)
    with pytest.raises(StoreError):
        await store.read_preferences("u1", "deriv")
    with pytest.raises(StoreError):
        await store.admin_approved("trend_following")


async def test_admin_flip_denies_every_user() -> None:
    from shared.core.trading_run_rule import run_decision

    store, _ = _store(known={"trend_following"})
    assert (await store.admin_approved("trend_following")) is True
    await store.set_admin("trend_following", False)
    assert (await store.admin_approved("trend_following")) is False
    for user_disabled in (False, True):
        assert (
            run_decision(
                admin_approved=await store.admin_approved("trend_following"),
                auto_trade=True,
                user_disabled=user_disabled,
                instruments=("EURUSD",),
            )
            is False
        ), "one global flip denies every user"


# ----------------------------------------------------------------------------------
# Adversarial (beyond the edge list)
# ----------------------------------------------------------------------------------


async def test_user_id_injection_stays_a_parameter_and_drops_nothing() -> None:
    store, db = _store()
    evil = "'; DROP TABLE trading_preferences; --"
    await store.set_timeframe(evil, "deriv", "1m")
    assert any(evil in args for _, args in db.calls), "must travel as a bound parameter"
    assert all(evil not in sql for sql, _ in db.calls), "must never appear in SQL text"
    assert set(db.rows) == set(FakePrefsDB.TABLES), "all three tables intact"
    await store.set_timeframe("u1", "deriv", "4h")
    got = await store.read_preferences(evil, "deriv")
    assert got.timeframe == "1m", "injection id sees only its own row — nobody else's"


async def test_strategy_id_injection_rejected_by_registry_check() -> None:
    from shared.core.trading_prefs_store import UnknownStrategyStoreError

    store, db = _store(known={"trend_following"})
    with pytest.raises(UnknownStrategyStoreError):
        await store.upsert_subscription(
            _sub(strategy_id="x'; DROP TABLE strategy_subscriptions; --")
        )
    assert set(db.rows) == set(FakePrefsDB.TABLES), "tables intact after rejected write"


# ----------------------------------------------------------------------------------
# Top-10 amendments (spec REV5, 2026-09-30 — human "others lgtm")
# ----------------------------------------------------------------------------------


# ---- REV5 #2: write failure on EVERY operation -> StoreError, nothing claimed ----


async def test_write_failure_set_timeframe_raises_store_error_and_writes_nothing() -> None:
    from shared.core.trading_prefs_store import StoreError

    store, db = _store(db=FakePrefsDB(fail={"execute"}))
    with pytest.raises(StoreError):
        await store.set_timeframe("u1", "deriv", "4h")
    assert db.rows["trading_preferences"] == {}, "failed write must leave zero rows"


async def test_write_failure_upsert_subscription_raises_store_error() -> None:
    from shared.core.trading_prefs_store import StoreError

    store, db = _store(db=FakePrefsDB(fail={"execute"}))
    with pytest.raises(StoreError):
        await store.upsert_subscription(_sub())
    assert db.rows["strategy_subscriptions"] == {}


async def test_write_failure_set_admin_raises_store_error() -> None:
    from shared.core.trading_prefs_store import StoreError

    store, db = _store(db=FakePrefsDB(fail={"execute"}))
    with pytest.raises(StoreError):
        await store.set_admin("trend_following", False)
    assert db.rows["strategy_admin"] == {}


async def test_write_failure_ensure_sign_in_raises_store_error() -> None:
    from shared.core.trading_prefs_store import StoreError

    store, db = _store(db=FakePrefsDB(fail={"execute"}))
    with pytest.raises(StoreError):
        await store.ensure_at_sign_in("u1", "deriv")
    assert db.rows["trading_preferences"] == {}


async def test_write_failure_ensure_broker_connect_raises_store_error() -> None:
    from shared.core.trading_prefs_store import StoreError

    store, db = _store(db=FakePrefsDB(fail={"execute"}))
    with pytest.raises(StoreError):
        await store.ensure_at_broker_connect("u1", "deriv")
    assert db.rows["trading_preferences"] == {}


# ---- REV5 #3: cross-broker subscription isolation (full-PK law, explicit) ----


async def test_subscription_isolated_across_brokers_for_same_user() -> None:
    store, _ = _store(known={"trend_following"})
    await store.upsert_subscription(_sub(broker_id="deriv", user_disabled=True))
    assert await store.read_subscription("u1", "mt5", "trend_following") is None
    assert (await store.read_subscription("u1", "deriv", "trend_following")).user_disabled is True


# ---- REV5 #4: concurrency — one stable row, value of one writer, no corruption ----


async def test_concurrent_timeframe_writes_leave_single_stable_row() -> None:
    store, db = _store()
    await asyncio.gather(
        store.set_timeframe("u1", "deriv", "4h"),
        store.set_timeframe("u1", "deriv", "15m"),
    )
    assert len(db.rows["trading_preferences"]) == 1, "PK upsert = serialization point"
    assert (await store.read_preferences("u1", "deriv")).timeframe in {"4h", "15m"}


async def test_concurrent_subscription_writes_leave_single_stable_row() -> None:
    store, db = _store(known={"trend_following"})
    await asyncio.gather(
        store.upsert_subscription(_sub(params={"a": 1})),
        store.upsert_subscription(_sub(params={"b": 2})),
    )
    assert len(db.rows["strategy_subscriptions"]) == 1, "never a duplicate row"
    row = next(iter(db.rows["strategy_subscriptions"].values()))
    assert row["params"] in ('{"a": 1}', '{"b": 2}'), "value is one of the two writers"


# ---- REV5 #5: params REPLACE wholesale (admin-managed — users never write them) ----


async def test_params_replaced_wholesale_never_merged() -> None:
    store, _ = _store(known={"trend_following"})
    await store.upsert_subscription(_sub(params={"ema_fast": 8, "ema_slow": 21}))
    got = await store.upsert_subscription(_sub(params={"ema_fast": 10}))
    assert got.params == {"ema_fast": 10}, "REPLACE: ema_slow dropped, never merged"
    await store.upsert_subscription(_sub(params={"ema_fast": 10}, instruments=("EURUSD", "XAUUSD")))
    got2 = await store.upsert_subscription(_sub(params={"ema_fast": 10}, instruments=("XAUUSD",)))
    assert got2.instruments == ("XAUUSD",), "instruments replaced wholesale too — no accumulation"


# ---- REV5 #6: schedule edge additions ----


def test_activation_equal_now_fires_at_activation() -> None:
    assert _next_run(NOW, "1h", NOW) == NOW


def test_seconds_precision_anchor_respected() -> None:
    act = datetime(2026, 9, 30, 6, 0, 37, tzinfo=UTC)
    now = datetime(2026, 9, 30, 7, 0, 15, tzinfo=UTC)
    assert _next_run(act, "1h", now) == datetime(2026, 9, 30, 7, 0, 37, tzinfo=UTC)


def test_unknown_timeframe_raises_typed_error() -> None:
    with pytest.raises(ValueError, match="unknown timeframe"):
        _next_run(NOW, "3d", NOW)


def test_naive_inputs_rejected_by_schedule_helper() -> None:
    with pytest.raises(ValueError, match="activation_at must be"):
        _next_run(datetime(2026, 9, 30, 6, 0, 0), "1h", NOW)  # noqa: DTZ001 — naive is the case under test
    with pytest.raises(ValueError, match="now must be"):
        _next_run(NOW, "1h", datetime(2026, 9, 30, 12, 0, 0))  # noqa: DTZ001 — naive is the case under test


# ---- REV5 #7: full lifecycle chain ----


async def test_full_lifecycle_chain_preserves_configuration() -> None:
    store, db = _store(known={"trend_following"})
    assert await store.ensure_at_sign_in("u1") is None, "sign-in before any broker: no write"
    await store.ensure_at_broker_connect("u1", "deriv")
    await store.set_timeframe("u1", "deriv", "4h")
    await store.upsert_subscription(_sub(instruments=("EURUSD",), user_disabled=True))
    # sign-out = no store call (state is DB-persisted by law §3.9)
    assert await store.ensure_at_sign_in("u1") is None, "re-sign-in before broker: still no write"
    prefs = await store.ensure_at_broker_connect("u1", "deriv")  # reconnect
    sub = await store.read_subscription("u1", "deriv", "trend_following")
    assert prefs.timeframe == "4h", "user change survives sign-out + reconnect"
    assert sub.instruments == ("EURUSD",) and sub.user_disabled is True
    assert len(db.rows["trading_preferences"]) == 1, "chain never duplicates rows"


# ---- REV5 #8: admin table off-limits to every user-facing method ----


async def test_only_admin_methods_touch_strategy_admin_table() -> None:
    store, db = _store(known={"trend_following"})
    start = len(db.calls)
    await store.ensure_at_sign_in("u1", "deriv")
    await store.ensure_at_broker_connect("u1", "deriv")
    await store.set_timeframe("u1", "deriv", "4h")
    await store.upsert_subscription(_sub())
    await store.read_preferences("u1", "deriv")
    await store.read_subscription("u1", "deriv", "trend_following")
    user_sql = [sql for sql, _ in db.calls[start:]]
    assert user_sql, "user-facing ops must actually run"
    assert all("strategy_admin" not in sql for sql in user_sql), (
        "REV5 #8: user-facing methods may never emit SQL naming strategy_admin"
    )
    admin_start = len(db.calls)
    await store.admin_approved("trend_following")
    await store.set_admin("trend_following", False)
    admin_sql = [sql for sql, _ in db.calls[admin_start:]]
    assert admin_sql and all("strategy_admin" in sql for sql in admin_sql)


# ---- REV5 #9: updated_at strictly increasing; caller value ignored ----


async def test_updated_at_strictly_increases_across_consecutive_writes() -> None:
    store, _ = _store()
    stamps = [
        (await store.set_timeframe("u1", "deriv", tf)).updated_at for tf in ("1m", "5m", "15m")
    ]
    assert stamps[0] < stamps[1] < stamps[2], "strict — store clamp (REV5 #9), not clock luck"


async def test_caller_supplied_updated_at_is_ignored() -> None:
    store, _ = _store(known={"trend_following"})
    stale = datetime(2001, 1, 1, tzinfo=UTC)
    got = await store.upsert_subscription(_sub(updated_at=stale))
    assert got.updated_at > stale, "system-managed: caller can never backdate a row"


# ---- REV5 #10: injection breadth (bound-parameter law on more fields) ----


async def test_broker_id_injection_stays_a_parameter() -> None:
    store, db = _store(known={"trend_following"})
    evil_b = "deriv'; DROP TABLE trading_preferences; --"
    await store.ensure_at_sign_in("u1", evil_b)
    await store.set_timeframe("u1", evil_b, "1m")
    got = await store.read_preferences("u1", evil_b)
    assert got is not None and got.broker_id == evil_b, "sees only its own row"
    assert any(evil_b in args for _, args in db.calls), "travels as a bound parameter"
    assert all(evil_b not in sql for sql, _ in db.calls), "never in SQL text"
    assert set(db.rows) == set(FakePrefsDB.TABLES), "tables intact"


async def test_instrument_id_injection_round_trips_as_data() -> None:
    store, db = _store(known={"trend_following"})
    evil_i = "EURUSD'; DROP TABLE strategy_subscriptions; --"
    await store.upsert_subscription(_sub(instruments=(evil_i,)))
    got = await store.read_subscription("u1", "deriv", "trend_following")
    assert got.instruments == (evil_i,), "payload is data, not SQL"
    assert all(evil_i not in sql for sql, _ in db.calls)
    assert set(db.rows) == set(FakePrefsDB.TABLES), "tables intact"


async def test_params_payload_injection_round_trips_as_json() -> None:
    store, db = _store(known={"trend_following"})
    evil_k = "x'; DROP TABLE strategy_admin; --"
    await store.upsert_subscription(_sub(params={evil_k: 8}))
    got = await store.read_subscription("u1", "deriv", "trend_following")
    assert got.params == {evil_k: 8}, "params key is JSON data, never SQL"
    assert all(evil_k not in sql for sql, _ in db.calls)
    assert set(db.rows) == set(FakePrefsDB.TABLES), "tables intact"
