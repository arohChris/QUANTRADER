#!/usr/bin/env python3
"""
M1.5 — verify trading preferences store against the REAL local Postgres.

WHY: the 68-test suite runs on FakePrefsDB (a SQL router). This script proves the
same SQL and round-trips on a real server: DDL syntax, timestamptz/jsonb/text[]
binding, ON CONFLICT DO NOTHING vs DO UPDATE semantics, full-PK scoped reads,
registry-gated writes, fail-closed StoreError, and the run gate end-to-end.
REV5 (2026-09-30) additions run here instead of pytest (CI has no PG service):
two-connection race, params/instruments REPLACE, strict updated_at clamp,
caller-stamp ignored, strategy_admin untouched by user ops, payload injection,
dead-connection failure -> StoreError.

Usage: python scripts/verify_m1.5_trading_prefs.py
Auth:  local DB password from QUANTRADER2/.env (TIMESCALE_DEV_PASSWORD) — never printed.
Exit:  0 = every check passed (human eyeballs the checklist below).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

APP_DIR = Path(__file__).resolve().parents[1]
REPO = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

from shared.contracts import StrategySubscription
from shared.core.analysis_schedule import next_run_at
from shared.core.trading_prefs_store import (
    StoreError,
    TradingPrefsStore,
    UnknownStrategyStoreError,
)
from shared.core.trading_run_rule import run_decision

USER = "m1.5_verify_user"
BROKER = "deriv"
OTHER_BROKER = "mt5"
STRATEGY = "trend_following"
EVIL = "'; DROP TABLE trading_preferences; --"

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))


def _password() -> str:
    env = REPO / ".env"
    if not env.exists():
        raise SystemExit(f"missing {env}")
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("TIMESCALE_DEV_PASSWORD="):
            # WHY raw split+strip only (m2.10 pattern): the secret itself may contain
            # quote characters — stripping them changes the password (InvalidPassword).
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"TIMESCALE_DEV_PASSWORD missing from {env}")


_DDL = [
    """
    CREATE TABLE IF NOT EXISTS trading_preferences (
        user_id       text        NOT NULL,
        broker_id     text        NOT NULL,
        timeframe     text        NOT NULL,
        activation_at timestamptz NOT NULL,
        auto_trade    boolean     NOT NULL,
        updated_at    timestamptz NOT NULL,
        PRIMARY KEY (user_id, broker_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_subscriptions (
        user_id       text        NOT NULL,
        broker_id     text        NOT NULL,
        strategy_id   text        NOT NULL,
        user_disabled boolean     NOT NULL,
        params        jsonb       NOT NULL,
        instruments   text[]      NOT NULL,
        updated_at    timestamptz NOT NULL,
        PRIMARY KEY (user_id, broker_id, strategy_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_admin (
        strategy_id   text        NOT NULL,
        admin_approved boolean    NOT NULL,
        updated_at    timestamptz NOT NULL,
        PRIMARY KEY (strategy_id)
    )
    """,
]


async def _run(conn: asyncpg.Connection, conn2: asyncpg.Connection) -> None:
    store = TradingPrefsStore(conn, known_strategies={STRATEGY})
    store2 = TradingPrefsStore(conn2, known_strategies={STRATEGY})

    # ---- lifecycle: sign-in holds until a broker exists (full PK law) ----
    before = await store.ensure_at_sign_in(USER)
    check("ensure sign-in w/o broker writes nothing", before is None)
    n = await conn.fetchval("SELECT COUNT(*) FROM trading_preferences WHERE user_id = $1", USER)
    check("no half-scoped row (user without broker)", n == 0, f"rows={n}")

    prefs = await store.ensure_at_sign_in(USER, BROKER)
    check(
        "sign-in materializes defaults",
        prefs is not None and prefs.timeframe == "1m" and prefs.auto_trade is False,
    )

    p = await store.set_timeframe(USER, BROKER, "4h")
    check(
        "set_timeframe 4h persists",
        p is not None and p.timeframe == "4h",
        f"updated_at={p.updated_at.isoformat() if p else 'n/a'}",
    )

    again = await store.ensure_at_sign_in(USER, BROKER)
    check("re-sign-in DO NOTHING keeps 4h", again is not None and again.timeframe == "4h")

    p2 = await store.ensure_at_broker_connect(USER, BROKER)
    check("reconnect keeps user change", p2 is not None and p2.timeframe == "4h")

    p3 = await store.ensure_at_broker_connect(USER, OTHER_BROKER)
    check("second broker row = template 1m", p3 is not None and p3.timeframe == "1m")
    kept = await store.read_preferences(USER, BROKER)
    check(
        "cross-broker isolation on real PG",
        kept is not None and kept.timeframe == "4h",
    )

    # ---- subscriptions: replace semantics + isolation ----
    sub = StrategySubscription(
        user_id=USER,
        broker_id=BROKER,
        strategy_id=STRATEGY,
        user_disabled=True,
        params={"ema_fast": 8},
        instruments=("EURUSD",),
        updated_at=datetime.now(UTC),
    )
    got = await store.upsert_subscription(sub)
    check(
        "subscription jsonb/text[] round-trip",
        got.params == {"ema_fast": 8} and got.instruments == ("EURUSD",),
    )
    check("subscription user_disabled persisted", got.user_disabled is True)
    check(
        "cross-tenant subscription isolation",
        await store.read_subscription("m1.5_verify_other", BROKER, STRATEGY) is None,
    )

    # ---- REV5 #5: params/instruments REPLACE wholesale on real jsonb/text[] ----
    await store.upsert_subscription(
        StrategySubscription(
            user_id=USER,
            broker_id=BROKER,
            strategy_id=STRATEGY,
            user_disabled=True,
            params={"ema_fast": 8, "ema_slow": 21},
            instruments=("EURUSD", "XAUUSD"),
            updated_at=datetime.now(UTC),
        )
    )
    replaced = await store.upsert_subscription(
        StrategySubscription(
            user_id=USER,
            broker_id=BROKER,
            strategy_id=STRATEGY,
            user_disabled=False,
            params={"ema_fast": 10},
            instruments=("XAUUSD",),
            updated_at=datetime.now(UTC),
        )
    )
    check(
        "params REPLACE on real jsonb (never merged)",
        replaced.params == {"ema_fast": 10},
        f"params={replaced.params}",
    )
    check(
        "instruments REPLACE on real text[] (never accumulated)",
        replaced.instruments == ("XAUUSD",),
        f"instruments={list(replaced.instruments)}",
    )

    # ---- REV5 #4: two real connections racing -> one stable row, last commit wins ----
    await asyncio.gather(
        store.set_timeframe(USER, BROKER, "15m"),
        store2.set_timeframe(USER, BROKER, "1h"),
    )
    n_rows = await conn.fetchval(
        "SELECT COUNT(*) FROM trading_preferences WHERE user_id = $1 AND broker_id = $2",
        USER,
        BROKER,
    )
    racy = await store.read_preferences(USER, BROKER)
    check("race: exactly one stable row (PK = serialization point)", n_rows == 1, f"rows={n_rows}")
    check(
        "race: value is one of the two writers",
        racy is not None and racy.timeframe in {"15m", "1h"},
        f"timeframe={racy.timeframe if racy else 'n/a'}",
    )

    # ---- REV5 #9: strict updated_at on real timestamptz; caller stamp ignored ----
    stamps = [
        (await store.set_timeframe(USER, BROKER, tf)).updated_at for tf in ("5m", "15m", "30m")
    ]
    check(
        "updated_at strictly increases (store clamp, not clock luck)",
        stamps[0] < stamps[1] < stamps[2],
        f"{stamps[0].isoformat()} < {stamps[1].isoformat()} < {stamps[2].isoformat()}",
    )
    stale = datetime(2001, 1, 1, tzinfo=UTC)
    stamped = await store.upsert_subscription(
        StrategySubscription(
            user_id=USER,
            broker_id=BROKER,
            strategy_id=STRATEGY,
            user_disabled=False,
            params={"ema_fast": 10},
            instruments=("XAUUSD",),
            updated_at=stale,
        )
    )
    check(
        "caller-supplied updated_at ignored on real PG",
        stamped.updated_at > stale,
        f"stored={stale.isoformat()} -> {stamped.updated_at.isoformat()}",
    )

    # ---- run gate end-to-end (real row values) ----
    admin_before = await store.admin_approved(STRATEGY)
    await store.set_admin(STRATEGY, False)
    denied = await store.admin_approved(STRATEGY)
    outcomes = [
        run_decision(
            admin_approved=denied,
            auto_trade=True,
            user_disabled=d,
            instruments=("EURUSD",),
        )
        for d in (False, True)
    ]
    check("admin flip denies every user", denied is False and outcomes == [False, False])
    check(
        "default row is inert until auto_trade",
        run_decision(
            admin_approved=True, auto_trade=False, user_disabled=False, instruments=("EURUSD",)
        )
        is False,
    )
    check(
        "fully-open row runs",
        run_decision(
            admin_approved=True, auto_trade=True, user_disabled=False, instruments=("EURUSD",)
        )
        is True,
    )
    await store.set_admin(STRATEGY, admin_before)  # restore platform state

    # ---- REV5 #8: user-facing writes never touch strategy_admin (real SQL) ----
    admin_ts = await conn.fetchval(
        "SELECT updated_at FROM strategy_admin WHERE strategy_id = $1", STRATEGY
    )
    await store.set_timeframe(USER, BROKER, "1h")
    await store.upsert_subscription(
        StrategySubscription(
            user_id=USER,
            broker_id=BROKER,
            strategy_id=STRATEGY,
            user_disabled=False,
            params={"ema_fast": 10},
            instruments=("XAUUSD",),
            updated_at=datetime.now(UTC),
        )
    )
    admin_ts2 = await conn.fetchval(
        "SELECT updated_at FROM strategy_admin WHERE strategy_id = $1", STRATEGY
    )
    check(
        "user-facing writes never touch strategy_admin",
        admin_ts is not None and admin_ts == admin_ts2,
        f"admin updated_at unchanged ({admin_ts.isoformat() if admin_ts else 'n/a'})",
    )

    # ---- REV5 #10: payload injection breadth on real PG ----
    evil_i = "EURUSD'; DROP TABLE strategy_subscriptions; --"
    evil_k = "x'; DROP TABLE strategy_admin; --"
    payload = await store.upsert_subscription(
        StrategySubscription(
            user_id=USER,
            broker_id=BROKER,
            strategy_id=STRATEGY,
            user_disabled=False,
            params={evil_k: 8},
            instruments=(evil_i,),
            updated_at=datetime.now(UTC),
        )
    )
    check(
        "instrument+params payloads round-trip as DATA, not SQL",
        payload.instruments == (evil_i,) and payload.params == {evil_k: 8},
        f"instrument len={len(evil_i)}, params key len={len(evil_k)}",
    )

    # ---- schedule (pure math, anchored cadence) ----
    act = datetime(2026, 9, 30, 6, 0, 0, tzinfo=UTC)
    check(
        "boundary fires", next_run_at(act, "1h", datetime(2026, 9, 30, 6, 0, 0, tzinfo=UTC)) == act
    )
    check(
        "just-after rounds up",
        next_run_at(act, "1m", datetime(2026, 9, 30, 6, 0, 30, tzinfo=UTC))
        == datetime(2026, 9, 30, 6, 1, 0, tzinfo=UTC),
    )
    span_start = datetime(2026, 1, 1, tzinfo=UTC)
    now = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
    nxt = next_run_at(span_start, "24h", now)
    ok_span = span_start <= nxt and nxt >= now and nxt - now <= timedelta(hours=24)
    check("9-month span computed instantly, lands >= now", ok_span, f"next={nxt.isoformat()}")

    # ---- fail-closed + gates ----
    unwired = TradingPrefsStore(None)
    try:
        await unwired.read_preferences(USER, BROKER)
        check("unwired store -> StoreError", False)
    except StoreError:
        check("unwired store -> StoreError", True)

    try:
        await store.set_admin("not_a_registered_strategy", False)
        check("unknown strategy -> UnknownStrategyStoreError", False)
    except UnknownStrategyStoreError:
        check("unknown strategy -> UnknownStrategyStoreError", True)

    # ---- adversarial: injection stays a parameter on real PG ----
    await store.set_timeframe(EVIL, BROKER, "1m")
    evil_row = await store.read_preferences(EVIL, BROKER)
    counts = await conn.fetch(
        "SELECT (SELECT COUNT(*) FROM trading_preferences) AS p,"
        " (SELECT COUNT(*) FROM strategy_subscriptions) AS s,"
        " (SELECT COUNT(*) FROM strategy_admin) AS a"
    )
    c = counts[0]
    check(
        "injection id stores only its own row", evil_row is not None and evil_row.timeframe == "1m"
    )
    check(
        "all three tables intact after injection id",
        c["p"] >= 1 and c["s"] >= 1 and c["a"] >= 1,
        f"p={c['p']} s={c['s']} a={c['a']}",
    )


async def _connect(pw: str) -> asyncpg.Connection:
    # WHY kwargs, not a URL DSN: the dev password contains URL-unsafe chars and
    # an f-string DSN silently misparses them (InvalidPasswordError). Same host,
    # same user — no URL parser in between (C7: password never printed/logged).
    return await asyncpg.connect(
        host="127.0.0.1",
        port=5432,
        user="quantrader",
        database="quantrader",
        password=pw,
        timeout=10,
    )


async def main() -> int:
    pw = _password()
    print("target: postgresql://quantrader:***@127.0.0.1:5432/quantrader (password never printed)")
    try:
        conn = await _connect(pw)
    except (OSError, TimeoutError, ValueError, asyncpg.PostgresError) as exc:
        print(
            f"[FAIL] cannot reach local Postgres: {exc.__class__.__name__} (password not printed)"
        )
        return 1
    conn2: asyncpg.Connection | None = None
    try:
        conn2 = await _connect(pw)  # second real connection — genuine concurrency (REV5 #4)
        for stmt in _DDL:
            await conn.execute(stmt)
        # pre-clean (previous crashed runs); scoped by verify identities only
        await conn.execute("DELETE FROM trading_preferences WHERE user_id IN ($1, $2)", USER, EVIL)
        await conn.execute(
            "DELETE FROM strategy_subscriptions WHERE user_id IN ($1, $2)", USER, EVIL
        )
        await _run(conn, conn2)

        # ---- REV5 #2: write on a REAL dead connection must fail closed ----
        conn3 = await _connect(pw)
        await conn3.close()
        dead = TradingPrefsStore(conn3, known_strategies={STRATEGY})
        try:
            await dead.set_timeframe(USER, BROKER, "1m")
            check("dead connection write -> StoreError", False, "no exception raised")
        except StoreError:
            check("dead connection write -> StoreError", True)
        except Exception as exc:  # noqa: BLE001 — WRONG type escaping the wrapper is the fail
            check("dead connection write -> StoreError", False, type(exc).__name__)
    finally:
        try:
            await conn.execute("DELETE FROM trading_preferences WHERE user_id = $1", USER)
            await conn.execute("DELETE FROM trading_preferences WHERE user_id = $1", EVIL)
            await conn.execute("DELETE FROM strategy_subscriptions WHERE user_id = $1", USER)
        except (
            OSError,
            TimeoutError,
            asyncpg.PostgresError,
        ) as exc:  # cleanup best-effort; rows are clearly marked
            print(f"[WARN] cleanup incomplete: {exc.__class__.__name__}")
        await conn.close()
        if conn2 is not None:
            await conn2.close()

    failed = [c for c in CHECKS if not c[1]]
    for name, ok, detail in CHECKS:
        print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    print(f"\nverify_m1.5_trading_prefs: {len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
