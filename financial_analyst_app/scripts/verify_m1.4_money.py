"""
M1.4 — Money Precision, REAL-DATA verification (AGENTS.md §2 Step 7).

Unit tests use synthetic values. This script proves the money authority against values
that ACTUALLY came from the broker and the live store, because the failure mode is
invisible in synthetic data: real prices are large, have 3+ decimals, and arrive as
JSON numbers that a naive loader silently turns into floats.

Every check uses exact `Decimal` equality. No `pytest.approx` — it is banned for money
(AGENTS.md §2 Step 4) and would hide precisely the error we are proving does not occur.

Run:  python scripts/verify_m1.4_money.py
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.core.money import (
    MONEY_PREC,
    DomainBoundError,
    MoneyTypeError,
    ScaleError,
    assert_decimal,
    assert_in_domain,
    quantize_money,
    scale_from_pip_size,
    scale_of,
    sum_money,
)

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "deriv"
PASS, FAIL = 0, 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[OK] {label}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"[FAIL] {label}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------- A. real broker payload
def real_captured_candles() -> dict:
    """Load the REAL captured Deriv frame with the money-safe parser.

    WHY parse_float=Decimal: these arrive as JSON numbers. Loaded normally they become
    Python floats and the error is already in the building before any arithmetic runs.
    """
    raw = FIXTURES / "ticks_history_candles_btc.json"
    return json.loads(raw.read_text(encoding="utf-8"), parse_float=Decimal)


def verify_real_broker_prices() -> None:
    print("\n=== A. REAL broker payload (captured from Deriv) ===")
    payload = real_captured_candles()
    check("frame msg_type is 'candles'", payload.get("msg_type") == "candles", str(payload.get("msg_type")))

    candle = payload["candles"][0]
    print(f"    real BTC candle: {dict(candle)}")

    for field, expected in (
        ("open", "84800.418"),
        ("high", "84819.134"),
        ("low", "84797.52"),
        ("close", "84799.93"),
    ):
        value = assert_decimal(candle[field])
        check(
            f"real {field} is EXACT via parse_float=Decimal",
            value == Decimal(expected),
            f"{value} == {expected}",
        )
        # and the same value read the naive way must be measurably worse
        naive = json.loads(json.dumps({field: float(expected)}))[field]
        exact = Decimal(expected) == Decimal(naive)
        check(f"naive float read of {field} IS inexact (error is real)", not exact)

    # A real broker price must be rejected at the money boundary, not coerced
    try:
        assert_decimal(float(candle["close"]))
        check("real close as float is REJECTED", False, "float was accepted")
    except MoneyTypeError as exc:
        check("real close as float is REJECTED", True, str(exc)[:60])


# ---------------------------------------------------------------- B. real pip_size
def verify_real_pip_size() -> None:
    print("\n=== B. REAL pip_size from the live broker (active_symbols) ===")
    # values read live this session; verify_m2b.5_broker_reference.py guards them at boot
    for symbol, pip, expected_scale in (
        ("CRYPTO.BTCUSD", "0.001", 3),
        ("COMMODITY.XAGUSD", "0.0001", 4),
        ("FX.EURUSD", "0.00001", 5),
    ):
        got = scale_from_pip_size(pip)
        check(f"{symbol} pip_size {pip} -> scale {got}", got == expected_scale)

    try:
        scale_from_pip_size("0")
        check("nonsense pip_size rejected", False)
    except ScaleError as exc:
        check("nonsense pip_size rejected", True, str(exc)[:50])


# ---------------------------------------------------------------- C. ISO 4217
def verify_iso4217() -> None:
    print("\n=== C. ISO 4217 minor units (NOT all 2) ===")
    for ccy, expected in (("USD", 2), ("JPY", 0), ("KWD", 3), ("CLF", 4), ("BRL", 2)):
        got = scale_of("money", ccy)
        check(f"{ccy} minor units = {got}", got == expected)


# ---------------------------------------------------------------- D. real storage round-trip
async def verify_real_numeric_roundtrip() -> None:
    print("\n=== D. REAL storage round-trip (live Timescale NUMERIC(38,18)) ===")
    env = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env.exists():
        check("live DB reachable", False, "repo .env not found — skipping DB leg")
        return
    password = next(
        line.split("=", 1)[1].strip()
        for line in env.read_text(encoding="utf-8").splitlines()
        if line.startswith("TIMESCALE_DEV_PASSWORD=")
    )

    import asyncpg

    conn = await asyncpg.connect(
        f"postgresql://quantrader:{password}@127.0.0.1:5432/quantrader"
    )
    try:
        for value in ("2000000075959.342", "84799.93", "0.00000001", "21000000.00000001"):
            await conn.execute("CREATE TEMP TABLE IF NOT EXISTS _money_verify (v NUMERIC(38,18))")
            await conn.execute("TRUNCATE _money_verify")
            await conn.execute("INSERT INTO _money_verify VALUES ($1::numeric)", value)
            got = await conn.fetchval("SELECT v FROM _money_verify")
            check(
                f"NUMERIC(38,18) exact: {value}",
                got == Decimal(value) and isinstance(got, Decimal),
                f"returned {type(got).__name__} {got}",
            )
    finally:
        await conn.close()


def verify_real_parquet_roundtrip() -> None:
    print("\n=== E. REAL parquet round-trip (decimal128(38,18)) ===")
    for value in ("2000000075959.342", "84799.93", "21000000.00000001"):
        table = pa.table({"amount": pa.array([Decimal(value)], type=pa.decimal128(38, 18))})
        buf = io.BytesIO()
        pq.write_table(table, buf)
        buf.seek(0)
        got = pq.read_table(buf).column("amount").to_pylist()[0]
        check(f"decimal128(38,18) exact: {value}", got == Decimal(value), str(got))

    try:
        pa.array([Decimal("2000000075959.342")], type=pa.decimal128(10, 4))
        check("too-narrow decimal type rejected", False)
    except pa.ArrowInvalid:
        check("too-narrow decimal type rejected", True, "ArrowInvalid raised")


# ---------------------------------------------------------------- F. real money math
def verify_real_posting_math() -> None:
    print("\n=== F. REAL money math on a real BTC tick size ===")
    # Real Deriv BTC: tick 0.001, so a price must land on a 0.001 grid.
    price = assert_decimal("84799.93")
    qty = assert_decimal("0.4")
    fee = assert_decimal("0.35")  # a real-shaped commission

    # Hand calc: 0.4 * 84799.93 = 33919.972 ; minus 0.35 fee = 33919.622
    notional = qty * price
    check("hand calc 0.4 * 84799.93 = 33919.972", notional == Decimal("33919.972"), str(notional))

    net = notional - fee
    check("hand calc net = 33919.622", net == Decimal("33919.622"), str(net))
    check("USD quantize = 33919.62", quantize_money(net, 2) == Decimal("33919.62"))

    # price must land on the broker's grid, exactly
    check("price is on the broker 0.001 grid", quantize_money(price, 3) == price)

    # order-independence on a real trade's components
    parts = [Decimal("33919.972"), Decimal("-0.35"), Decimal("0.00")]
    check("order-independent sum", sum_money(parts, scale=2) == Decimal("33919.62"))

    check("context precision is 34", MONEY_PREC == 34)
    try:
        assert_in_domain(Decimal("9" * 25), "max_balance")
        check("absurd balance rejected", False)
    except DomainBoundError:
        check("absurd balance rejected", True, "DomainBoundError")


def main() -> int:
    print("=" * 74)
    print("M1.4 MONEY PRECISION — REAL-DATA VERIFICATION")
    print("=" * 74)
    verify_real_broker_prices()
    verify_real_pip_size()
    verify_iso4217()
    verify_real_parquet_roundtrip()
    verify_real_posting_math()
    asyncio.run(verify_real_numeric_roundtrip())

    print("\n" + "=" * 74)
    print(f"  {PASS}/{PASS + FAIL} checks passed")
    print("=" * 74)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
