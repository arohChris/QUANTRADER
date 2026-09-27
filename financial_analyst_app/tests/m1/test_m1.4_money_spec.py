"""
M1.4 — Money Precision spec tests. WRITTEN AGAINST THE SPEC, NOT AGAINST CODE.

Spec: docs/specs/m1.4_money_precision.spec.md (v1.0 DRAFT)

INTENDED STATE: this file FAILS TO COLLECT right now, because
`shared/core/money.py` does not exist. That is the correct test-first state
per AGENTS.md §2 Step 3 ("tests written against the spec, before
implementation exists"). Every other test in the repo must stay green.
Do NOT "fix" this by creating money.py before the spec is approved.

LAW COMPLIANCE (AGENTS.md):
  §2 Step 3  one test per spec edge + adversarial + hand-calc Decimals
  §2 Step 4  no `assert no_exception`; every test asserts a VALUE or an ErrorCode
  §2 Step 3  money asserts are exact `Decimal("x.xx")` — NEVER pytest.approx
  §3.1       no float anywhere in the money path
  §3.5       highest-risk formula cross-checked against a 2nd implementation
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction

import pytest

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

# ---------------------------------------------------------------- helpers

USD = "USD"


def _permutations(seq: list[Decimal]) -> list[list[Decimal]]:
    """Deterministic distinct orderings — proves order-independence, not luck."""
    from itertools import islice, permutations

    return [list(p) for p in islice(permutations(seq), 5)]


# ================================================================ §11.1
# Representability: the spec's headline claim. Exactness proven with
# Decimal equality — pytest.approx is BANNED for money (AGENTS.md §2 Step 4).


def test_balance_2_trillion_342_is_exact():
    """The figure that proved float is unfit: 2e12 + 3dp.

    IMPORTANT, and the reason this test was rewritten: an earlier version asserted
    `Decimal(str(2000000075959.342)) != value` and FAILED. Python's float repr uses
    the shortest string that round-trips, so `str()` prints '2000000075959.342' and
    HIDES the binary error completely. Nobody reading printed output can detect it.

    The error is only visible on DIRECT conversion, which is why money must be
    prevented from ever becoming a float rather than checked after the fact.
    """
    exact = assert_decimal("2000000075959.342")
    assert exact == Decimal("2000000075959.342")

    lossy = Decimal(2000000075959.342)  # noqa: RUF032  (DELIBERATE float - that is the point)
    assert lossy != exact
    assert lossy == Decimal("2000000075959.342041015625")

    # and the trap that hid it: string round-trip of the float looks perfect
    assert str(2000000075959.342) == "2000000075959.342"
    assert Decimal(str(2000000075959.342)) == exact  # would have looked "fine"


def test_satoshi_8dp_boundary_is_exact():
    v = assert_decimal("21000000.00000001")
    assert v == Decimal("21000000.00000001")


def test_max_balance_plus_smallest_fee_does_not_overflow():
    """Hand calc: 2000000075959.34 (max balance) + 0.01 fee = 2000000075959.35
    16 significant digits — within the 34-digit working context with 18 to spare."""
    total = assert_decimal("2000000075959.34") + assert_decimal("0.01")
    assert total == Decimal("2000000075959.35")


def test_context_precision_is_34_not_the_default_28():
    """Spec §4.1 — precision is set explicitly, never inherited."""
    assert MONEY_PREC == 34
    assert MONEY_PREC != 28  # the default we refuse to depend on


# ================================================================ §5 / §11.4
# Rounding: ROUND_HALF_UP, applied ONCE, on both signs.


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.005", "0.01"),  # float round() gives 0.01 too — but see 2.675
        ("0.015", "0.02"),  # float round() gives 0.01 — WRONG
        ("2.675", "2.68"),  # float round() gives 2.67 — WRONG
        ("-0.005", "-0.01"),  # negative must round away from zero, not toward
        ("-0.015", "-0.02"),
    ],
)
def test_half_up_rounding(raw: str, expected: str):
    assert quantize_money(assert_decimal(raw), 2) == Decimal(expected)


def test_rounding_applied_once_not_per_step():
    """Hand calc: 0.4 lots * 1.08430 = 0.433720, then fee 0.01 -> 0.423720,
    quantize ONCE at 2dp = 0.42. Rounding per-step would give 0.43 then 0.42
    from a different base and lose a cent on some paths."""
    raw = Decimal("0.4") * Decimal("1.08430") - Decimal("0.01")
    assert raw == Decimal("0.423720")
    assert quantize_money(raw, 2) == Decimal("0.42")


def test_negative_zero_is_normalised():
    """Spec §9 — no negative zero may enter a ledger."""
    assert quantize_money(Decimal("-0.000"), 2) == Decimal(0)
    assert not quantize_money(Decimal("-0.000"), 2).is_signed()


def test_zero_qty_is_allowed_and_distinct_from_missing():
    """Spec §9 — qty 0 means flat, not absent. None means absent."""
    assert quantize_money(Decimal(0), 2) == Decimal("0.00")
    with pytest.raises(MoneyTypeError):
        assert_decimal(None)


# ================================================================ §11.2 / §11.3
# Accumulation + order-independence. IEEE-754 addition is NOT associative.


def test_100k_tiny_additions_sum_exactly():
    """float gives 0.009999999999994874 here and `== 0.01` is False."""
    total = sum_money([Decimal("0.0000001")] * 100_000)
    assert total == Decimal("0.01")


def test_sum_is_order_independent():
    """Spec §11.3 — the same 1,000 amounts in 5 orders must be byte-identical."""
    amounts = [Decimal(f"{0.01 * (i % 97) + 0.0001 * (i % 13):.6f}") for i in range(1000)]
    totals = {sum_money(order) for order in _permutations(amounts)}
    assert len(totals) == 1, f"sum is order-dependent: {len(totals)} distinct totals"


def test_sum_matches_hand_calc():
    """Hand calc: 12.34 + 56.78 + 90.12 = 159.24 exactly."""
    assert sum_money([Decimal("12.34"), Decimal("56.78"), Decimal("90.12")]) == Decimal("159.24")


# ================================================================ §11.5
# Round-trips. Every hop must be byte-exact or money is lost at a boundary.


def test_str_roundtrip_is_exact():
    v = assert_decimal("2000000075959.342")
    assert assert_decimal(str(v)) == v


def test_json_emits_a_string_not_a_number():
    """Spec §6 — money must not become an IEEE-754 double in a consumer."""
    payload = json.dumps({"qty": str(assert_decimal("0.4"))})
    assert payload == '{"qty": "0.4"}'
    assert json.loads(payload)["qty"] == "0.4"  # arrives as str, must be re-parsed


def test_parquet_decimal128_roundtrip_is_exact():
    pyarrow = pytest.importorskip("pyarrow", reason="pyarrow is a hard dep — absence is a bug")
    import io

    table = pyarrow.table({"amount": pyarrow.array([Decimal("2000000075959.342")],
                                                   type=pyarrow.decimal128(38, 18))})
    buf = io.BytesIO()
    import pyarrow.parquet as pq

    pq.write_table(table, buf)
    buf.seek(0)
    assert pq.read_table(buf).column("amount").to_pylist()[0] == Decimal("2000000075959.342")


def test_parquet_rejects_a_value_wider_than_the_declared_type():
    """Sizing matters: decimal128(p,s) rejects only when total digits exceed p.
    MEASURED: decimal128(19,4) does NOT reject 2e12 (13 int + 3 dec = 16 <= 19) and
    stores it exactly. An earlier version of this test wrongly assumed (19,4) was too
    small — it is not. The rail fires at 25 digits, and on a too-NARROW integer part."""
    pyarrow = pytest.importorskip("pyarrow", reason="pyarrow is a hard dep — absence is a bug")
    # 19,4 is wide enough: stored exactly, no error
    assert pyarrow.array(
        [Decimal("2000000075959.342")], type=pyarrow.decimal128(19, 4)
    ).to_pylist()[0] == Decimal("2000000075959.3420")
    # 10,4 gives only 6 integer digits — 2e12 cannot fit
    with pytest.raises(pyarrow.ArrowInvalid):
        pyarrow.array([Decimal("2000000075959.342")], type=pyarrow.decimal128(10, 4))


# ================================================================ §11.6
# Context defence. A hostile foreign context must not change our money.


def test_hostile_localcontext_does_not_change_money_results():
    """Spec §2 — a stray localcontext(prec=6) turned 2000000075959.352 into 2.00000E+12.

    SCOPE NOTE, deliberately narrow: this proves the arithmetic performed INSIDE
    money.py is immune. A bare `Decimal + Decimal` written in application code
    still uses whatever ambient context is active — so application code must call
    these helpers rather than adding Decimals by hand. That boundary is real and
    is why every money operation has a named function here.
    """
    with localcontext() as hostile:
        hostile.prec = 6
        result = sum_money([assert_decimal("2000000075959.342"), assert_decimal("0.01")], scale=2)
    assert result == Decimal("2000000075959.35")


def test_41_digit_value_is_rejected_not_silently_rounded():
    """Spec §9 — beyond the declared domain, must ERROR. The default context of 28
    would have silently truncated the tail, which is the failure being prevented."""
    too_big = "1" + "23456789" * 4 + ".55"
    with pytest.raises(DomainBoundError):
        sum_money([assert_decimal(too_big), assert_decimal("0.01")], scale=2)


# ================================================================ §9 / §11.9
# Type guard. This is what makes the whole discipline self-enforcing.


@pytest.mark.parametrize("bad", [0.4, 1.0, 0.0])
def test_float_is_rejected_at_the_money_boundary(bad: float):
    """Spec §11.9 — adversarial, not in the edge table. Must raise, never coerce."""
    with pytest.raises(MoneyTypeError):
        assert_decimal(bad)


def test_decimal_of_a_float_would_have_been_wrong_so_the_guard_matters():
    """Demonstrates the exact corruption the guard prevents."""
    assert Decimal(0.1) != Decimal("0.1")  # noqa: RUF032  (DELIBERATE float - that is the point)
    with pytest.raises(MoneyTypeError):
        assert_decimal(0.1)


def test_int_is_rejected_because_an_int_is_a_count_not_an_amount():
    with pytest.raises(MoneyTypeError):
        assert_decimal(100)


def test_str_and_decimal_are_accepted():
    assert assert_decimal("0.4") == Decimal("0.4")
    assert assert_decimal(Decimal("0.4")) == Decimal("0.4")


def test_money_arithmetic_with_a_float_raises_typeerror():
    """Spec §3 — the self-enforcement property that justifies the whole change.
    The type error is the ASSERTION here, so the checker must stay quiet."""
    with pytest.raises(TypeError):
        _ = Decimal("0.4") * 0.2  # type: ignore[operator]


# ================================================================ §9 scale registry


def test_usd_money_scale_is_2():
    assert scale_of("money", USD) == 2


def test_jpy_money_scale_is_0_not_assumed_2():
    """Spec §9 — a 0dp currency must be honoured, not rounded to 2."""
    assert scale_of("money", "JPY") == 0


def test_iso4217_minor_units_are_pinned_not_assumed():
    """Currency minor units come from ISO 4217, and they are NOT all 2.
    These are the cases that break a 'assume 2dp' implementation, so they are pinned
    by test rather than carried from memory."""
    assert scale_of("money", "USD") == 2
    assert scale_of("money", "JPY") == 0  # zero minor units
    assert scale_of("money", "KRW") == 0
    assert scale_of("money", "KWD") == 3  # three minor units
    assert scale_of("money", "BHD") == 3
    assert scale_of("money", "CLF") == 4  # four minor units


def test_price_scale_is_derived_from_broker_pip_size_not_a_table():
    """CORRECTION (2026-09-27): money.py originally carried PRICE_SCALES={"BTC": 8}.

    That is the SAME defect this project already found once — our config said BTC
    tick 0.01 while the broker said 0.001, and XAG 0.01 vs 0.0001 (100x error). A
    hand-written price table would reintroduce it, and it violated spec 4.4. The
    venue is the authority on its own tick quantum, so scale is DERIVED from pip_size.
    These are the live values read from Deriv's active_symbols this session."""
    assert scale_from_pip_size("0.001") == 3  # CRYPTO.BTCUSD, corrected
    assert scale_from_pip_size("0.0001") == 4  # COMMODITY.XAGUSD, corrected
    assert scale_from_pip_size("0.00001") == 5  # FX.EURUSD
    assert scale_from_pip_size("0.01") == 2
    assert scale_from_pip_size("1") == 0


def test_price_scale_rejects_a_nonsense_pip_size():
    with pytest.raises(ScaleError):
        scale_from_pip_size("0")
    with pytest.raises(ScaleError):
        scale_from_pip_size("-0.001")


def test_unknown_currency_raises_rather_than_assuming_2dp():
    with pytest.raises(ScaleError):
        scale_of("money", "XYZ")


def test_btc_is_not_silently_treated_as_2dp():
    """Spec §10 — BTC is not a 2dp currency; truncating it destroys the value."""
    with pytest.raises(ScaleError):
        assert scale_of("money", "BTC") == 2


def test_intermediate_precision_is_rounded_once_not_rejected():
    """CORRECTED RULE (spec §9 amended 2026-09-27).

    The earlier wording — 'aggregation over mixed scales is rejected' — was WRONG and
    these tests found it. Summing a 3-dp balance into a 2-dp currency is exactly the
    'compute wide, round once' case the spec mandates, so refusing it would reject
    correct behaviour. Two distinct rules are being asserted here:

      1. SUMMING never rejects an input for carrying extra precision.
      2. Only PERSISTING into a fixed-scale column refuses it, via
         quantize_money(..., reject_excess_scale=True).
    """
    # 3-dp inputs into a 2-dp output: accepted, rounded exactly once
    assert sum_money([assert_decimal("12.345"), assert_decimal("0.006")], scale=2) == Decimal("12.35")
    # a 2e12 balance carrying 3 dp is representable and sums exactly at our precision
    assert sum_money([assert_decimal("2000000075959.342"), assert_decimal("0.01")], scale=2) == (
        Decimal("2000000075959.35")
    )
    # but the 8-dp value may NOT be written into a 2-dp column
    with pytest.raises(ScaleError):
        quantize_money(assert_decimal("0.00000001"), 2, reject_excess_scale=True)


# ================================================================ §9 domain bounds


def test_over_max_balance_is_rejected():
    with pytest.raises(DomainBoundError):
        assert_in_domain(Decimal(999999999999999999999), "max_balance")


def test_over_max_qty_is_rejected():
    with pytest.raises(DomainBoundError):
        assert_in_domain(Decimal("1e12"), "max_qty")


def test_max_balance_boundary_is_accepted():
    """Hand calc: exactly 2000000075959.34 is inside the declared bound."""
    assert assert_in_domain(Decimal("2000000075959.34"), "max_balance") == Decimal("2000000075959.34")


# ================================================================ §10 adversarial
# Deliberately NOT in the §9 edge table, per spec.


@pytest.mark.parametrize("adversarial", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_non_finite_and_overflow_literals_are_rejected(adversarial: str):
    with pytest.raises(MoneyTypeError):
        assert_decimal(adversarial)


def test_whitespace_is_normalised():
    assert assert_decimal("  12.50  ") == Decimal("12.50")


def test_underscore_literal_is_rejected():
    with pytest.raises(MoneyTypeError):
        assert_decimal("1_000.50")


def test_too_much_scale_is_rejected_not_truncated():
    """Spec §9 — silent rounding is how money goes missing. 1e-9 must NOT become 0.00."""
    with pytest.raises(ScaleError):
        quantize_money(assert_decimal("1e-9"), 2, reject_excess_scale=True)


def test_bytes_are_rejected():
    with pytest.raises(MoneyTypeError):
        assert_decimal(b"12.50")


def test_empty_string_raises_a_distinct_error():
    with pytest.raises(MoneyTypeError):
        assert_decimal("")


def test_40_digit_value_is_rejected_not_silently_rounded():
    """Spec §9 — beyond the declared domain, must error rather than truncate."""
    with pytest.raises(DomainBoundError):
        assert_in_domain(Decimal("1" * 40), "max_balance")


# ================================================================ §11.7
# Second independent implementation (AGENTS.md §3.5).


def test_posting_math_matches_a_fraction_oracle():
    """Cross-check the Decimal implementation against exact rationals (AGENTS.md §3.5).
    Compared UNQUANTIZED, so this is a real independent check rather than both
    sides passing through the same rounding.
    Hand calc: qty 2/5 @ 10843/10000, fee 1/100
             = 2/5*10843/10000 - 1/100 = 10843/25000 - 250/25000 = 10593/25000
             = 0.42372"""
    qty, price, fee = Fraction(2, 5), Fraction(10843, 10000), Fraction(1, 100)
    oracle = qty * price - fee
    assert oracle == Fraction(10593, 25000)

    ours = assert_decimal("0.4") * assert_decimal("1.08430") - assert_decimal("0.01")
    assert Decimal(oracle.numerator) / Decimal(oracle.denominator) == ours
    assert ours == Decimal("0.42372")
    # and the storable value is rounded exactly once
    assert quantize_money(ours, 2) == Decimal("0.42")


def test_fraction_oracle_agrees_exactly_for_a_hundred_fills():
    """Hand calc: 100 fills of 0.01 fee = 1.00; oracle and ours must agree exactly."""
    oracle = sum(Fraction(1, 100) for _ in range(100))
    ours = sum_money([Decimal("0.01")] * 100)
    assert ours == Decimal(oracle.numerator) / Decimal(oracle.denominator) == Decimal("1.00")


def test_invalid_operation_is_never_swallowed():
    """A quantize that cannot be represented must raise, never return a wrong value."""
    with pytest.raises(InvalidOperation), localcontext() as ctx:
        ctx.prec = 4
        Decimal("2000000075959.342").quantize(Decimal("0.01"))
