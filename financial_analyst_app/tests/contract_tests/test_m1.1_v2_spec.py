"""
M1.1 contracts v2.0 — spec tests. WRITTEN AGAINST docs/CONTRACT_V2_PROPOSAL.md.

Step 3 of AGENTS.md 2: tests written against the proposal BEFORE implementation.
These are RED until shared/contracts/* is bumped to v2.

DESIGN DECISION, settled by measurement not guesswork (pydantic 2.13.4):

  lax (default)          float 0.4  -> Decimal('0.4')   ACCEPTED SILENTLY. Useless.
  model_config strict    str  "0.4" -> REJECTED          too strict: we need str at the
                                                   config/wire boundary.
  Field(strict=True)    same as above                same problem.
  BeforeValidator ->    float/int/bytes REJECTED,
  money.assert_decimal   str + Decimal accepted exact  <- CHOSEN.

WHY a shared annotated type rather than a validator per field: the contract and
shared/core/money.py must not be able to drift. One authority, one behaviour.

LAW: exact Decimal asserts only. `pytest.approx` is BANNED for money (AGENTS.md 2 Step 4)
and would hide the very error this bump exists to prevent.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.contracts.execution import ExecutionResult, Fill
from shared.contracts.order import Order, OrderIntent
from shared.contracts.portfolio import PortfolioState, Position
from shared.contracts.risk import RiskDecision

TA = uuid4()


def _order(**over):
    base = {
        "order_id": uuid4(), "client_order_id": "cid-1", "instrument_id": "FX.EURUSD",
        "broker": "deriv", "side": "buy", "qty": Decimal("0.4"), "trading_account_id": TA,
    }
    return Order(**{**base, **over})


# ==================================================== 1. money fields are Decimal
def test_order_qty_is_decimal_and_exact():
    o = _order(qty=Decimal("0.4"))
    assert isinstance(o.qty, Decimal)
    assert o.qty == Decimal("0.4")


def test_fill_price_qty_commission_are_decimal():
    f = Fill(fill_id="f1", price=Decimal("84799.93"), qty=Decimal("0.4"),
             commission=Decimal("0.35"), filled_at=datetime.now(UTC))
    assert (f.price, f.qty, f.commission) == (
        Decimal("84799.93"), Decimal("0.4"), Decimal("0.35"),
    )


def test_portfolio_money_fields_are_decimal():
    p = PortfolioState(
        trading_account_id=TA, cash={"USD": Decimal("1000.00")},
        equity=Decimal("1000.00"), margin_used=Decimal("400.00"),
        margin_available=Decimal("600.00"), leverage=Decimal(500),
        exposure={"FX.EURUSD": Decimal("400.00")},
    )
    assert p.equity == Decimal("1000.00")
    assert p.cash["USD"] == Decimal("1000.00")
    assert p.leverage == Decimal(500)


def test_risk_approved_qty_is_decimal():
    r = RiskDecision(decision_id=uuid4(), approved=True, approved_qty=Decimal("0.4"),
                     trading_account_id=TA, balance=Decimal("1000.00"),
                     required_margin=Decimal("87.94"))
    assert isinstance(r.approved_qty, Decimal)
    assert r.approved_qty == Decimal("0.4")


# ==================================================== 2. float is REJECTED (adversarial)
@pytest.mark.parametrize("bad", [0.4, 1.0, 0.0])
def test_order_rejects_float_qty(bad: float):
    """Lax pydantic would ACCEPT this. The whole point is that it does not."""
    with pytest.raises(ValidationError):
        _order(qty=bad)


def test_fill_rejects_float_price():
    with pytest.raises(ValidationError):
        Fill(fill_id="f1", price=84799.93, qty=Decimal("0.4"),
             filled_at=datetime.now(UTC))


def test_float_qty_rejected_is_not_silently_exact():
    """Guard the regression this bump prevents: the SAME value via float and via str
    must not both be accepted, or 'Decimal everywhere' is only cosmetic."""
    with pytest.raises(ValidationError):
        _order(qty=0.4)
    assert _order(qty="0.4").qty == Decimal("0.4")


def test_lossy_float_is_rejected_not_preserved():
    """0.1+0.2 is 0.30000000000000004. Lax mode would faithfully PRESERVE that error."""
    with pytest.raises(ValidationError):
        _order(qty=0.1 + 0.2)


# ==================================================== 3. tenant identity is required
def test_portfolio_cannot_be_built_without_tenant():
    """D36: user_id=None made a cross-tenant-leak path reachable."""
    with pytest.raises(ValidationError) as exc:
        PortfolioState(equity=Decimal("1000.00"))
    assert exc.value.errors()[0]["type"] == "missing"


def test_order_cannot_be_built_without_tenant():
    with pytest.raises(ValidationError):
        Order(order_id=uuid4(), client_order_id="c", instrument_id="FX.EURUSD",
              broker="deriv", side="buy", qty=Decimal("0.4"))


def test_tenant_is_carried_not_optional():
    assert _order().trading_account_id is not None


# ==================================================== 4. BaseContract on Position/Fill
def test_position_carries_correlation_id_and_version():
    """D37: a Position could not be traced to a trade, breaking the 3.12 audit chain."""
    p = Position(instrument_id="FX.EURUSD", qty=Decimal("0.4"), avg_entry=Decimal("1.08430"))
    assert p.correlation_id is not None
    assert p.trace_id is not None
    assert p.version == "2.0"
    assert p.timestamp.tzinfo is not None


def test_fill_carries_correlation_id_and_version():
    f = Fill(fill_id="f1", price=Decimal("84799.93"), qty=Decimal("0.4"),
             filled_at=datetime.now(UTC))
    assert f.correlation_id is not None
    assert f.version == "2.0"


def test_position_correlation_id_survives_a_json_roundtrip():
    """The audit chain must survive serialisation, not just exist in memory."""
    p = Position(instrument_id="FX.EURUSD", qty=Decimal("0.4"), avg_entry=Decimal("1.08430"))
    back = Position.model_validate_json(p.model_dump_json())
    assert back.correlation_id == p.correlation_id
    assert back.avg_entry == Decimal("1.08430")


# ==================================================== 5. Fill.timestamp is aware UTC
def test_fill_broker_time_must_be_aware_utc():
    """D38: it was str|None. A string invites timestamp[:10], which breaks on TZ edges."""
    with pytest.raises(ValidationError):
        # NAIVE on purpose — the whole point is that it must be refused (D38)
        Fill(fill_id="f1", price=Decimal("1.0"), qty=Decimal(1),
             filled_at=datetime(2026, 9, 27, 12, 0, 0))  # noqa: DTZ001  (deliberate naive)


def test_fill_broker_time_accepts_epoch_int_from_the_broker():
    """Deriv sends epoch integers, not strings. The conversion must be explicit."""
    f = Fill(fill_id="f1", price=Decimal("84799.93"), qty=Decimal("0.4"),
             filled_at=1790496960)
    assert f.filled_at == datetime.fromtimestamp(1790496960, tz=UTC)


def test_fill_rejects_a_plain_string_timestamp():
    with pytest.raises(ValidationError):
        Fill(fill_id="f1", price=Decimal("1.0"), qty=Decimal(1), filled_at="2026-09-27")


# ==================================================== 6. RiskDecision is auditable
def test_risk_decision_requires_the_money_it_evaluated():
    """D35: no balance, no required_margin, no tenant -> not reconstructable."""
    with pytest.raises(ValidationError) as exc:
        RiskDecision(decision_id=uuid4(), approved=True, approved_qty=Decimal("0.4"))
    missing = {e["loc"][0] for e in exc.value.errors() if e["type"] == "missing"}
    assert {"trading_account_id", "balance", "required_margin"} <= missing


def test_risk_decision_rejects_float_balance():
    with pytest.raises(ValidationError):
        RiskDecision(decision_id=uuid4(), approved=True, approved_qty=Decimal("0.4"),
                     trading_account_id=TA, balance=1000.0, required_margin=Decimal("87.94"))


# ==================================================== 7. wire format is a STRING
def test_money_serialises_as_a_json_string_not_a_number():
    """A JSON number would become an IEEE-754 double in every consumer."""
    payload = _order(qty=Decimal("0.4")).model_dump_json()
    assert '"qty":"0.4"' in payload.replace(" ", "")


def test_json_roundtrip_is_exact_and_returns_a_string():
    o = _order(qty=Decimal("84799.93"), price=Decimal("84799.933"))
    back = Order.model_validate_json(o.model_dump_json())
    assert back.qty == Decimal("84799.93")
    assert back.price == Decimal("84799.933")
    assert json.loads(o.model_dump_json())["qty"] == "84799.93"


# ==================================================== 8. hand calcs (law 2 Step 3)
def test_hand_calc_pnl_matches_to_the_cent():
    # Hand calc: 0.4 lots * 1.08430 = 0.433720 ; commission 0.01 -> 0.423720
    # USD 2dp HALF_UP -> 0.42
    notional = Decimal("0.4") * Decimal("1.08430")
    assert notional == Decimal("0.433720")
    net = notional - Decimal("0.01")
    assert net == Decimal("0.423720")
    from shared.core.money import quantize_money

    assert quantize_money(net, 2) == Decimal("0.42")


def test_hand_calc_required_margin_matches_broker_profile():
    # Hand calc: 0.4 lots * 100000 * 1.08430 = 43372.00 notional
    #           / leverage 500 = 86.744 ; + 1.20 fees = 87.944 -> 87.94 HALF_UP
    from shared.core.money import quantize_money

    notional = Decimal("0.4") * Decimal(100000) * Decimal("1.08430")
    assert notional == Decimal("43372.0000")
    margin = notional / Decimal(500)
    assert quantize_money(margin + Decimal("1.20"), 2) == Decimal("87.94")


def test_cross_check_against_a_fraction_oracle():
    """AGENTS.md 3.5: a second independent implementation."""
    oracle = Fraction(2, 5) * Fraction(108430, 100000) - Fraction(1, 100)
    ours = Decimal("0.4") * Decimal("1.08430") - Decimal("0.01")
    assert Decimal(oracle.numerator) / Decimal(oracle.denominator) == ours


# ==================================================== 9. instrument_id naming
def test_instrument_id_replaces_symbol():
    o = _order()
    assert o.instrument_id == "FX.EURUSD"
    assert not hasattr(o, "symbol")


def test_order_intent_uses_instrument_id():
    oi = OrderIntent(signal_id=uuid4(), instrument_id="COMMODITY.XAUUSD",
                     broker="deriv", side="sell", qty=Decimal("0.1"),
                     order_type="limit", trading_account_id=TA)
    assert oi.instrument_id == "COMMODITY.XAUUSD"


# ==================================================== 10. adversarial money inputs
@pytest.mark.parametrize("bad", [b"0.4", "NaN", "Infinity", "-Infinity", "", "1e400"])
def test_order_rejects_non_money_literals(bad):
    with pytest.raises(ValidationError):
        _order(qty=bad)


def test_order_rejects_none_qty():
    with pytest.raises(ValidationError):
        _order(qty=None)


def test_version_is_two_zero():
    """The bump must be visible in the payload, or 'no silent drift' is a fiction."""
    assert _order().version == "2.0"


def test_execution_result_money_is_decimal():
    e = ExecutionResult(
        order_id=uuid4(), state="filled",
        fills=[Fill(fill_id="f1", price=Decimal("84799.93"), qty=Decimal("0.4"),
                    filled_at=datetime.now(UTC))],
        avg_fill_price=Decimal("84799.93"), slippage_bps=Decimal("0.35"),
    )
    assert e.avg_fill_price == Decimal("84799.93")
    assert e.slippage_bps == Decimal("0.35")
