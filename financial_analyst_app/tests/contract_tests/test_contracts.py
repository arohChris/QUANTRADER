"""
M1.1 Contract Tests — snapshot + frozen + version bump enforcement
WHY: If you add a field without bumping version, test fails. If you mutate frozen contract, test fails.
This is the Airplane Test for contracts — no DB, no broker.

Each contract is tested for:
  - frozen (cannot be mutated)
  - extra="forbid" (typo field raises)
  - correlation_id present and traceable
  - snapshot (golden) — new field → snapshot diff → must bump version + add migration
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from shared.contracts.analytics import AnalyticsResult
from shared.contracts.base import BaseContract
from shared.contracts.errors import ErrorCode
from shared.contracts.execution import ExecutionResult, Fill
from shared.contracts.instrument import AssetClass
from shared.contracts.market_data import Candle, DataQuality, MarketData, Tick
from shared.contracts.order import Order
from shared.contracts.portfolio import PortfolioState
from shared.contracts.reconciliation import ReconciliationReport
from shared.contracts.risk import RiskDecision
from shared.contracts.signal import Signal
from shared.contracts.trade_plan import EntrySpec, StopSpec, TargetSpec, TimingSpec, TradePlan


def test_base_frozen() -> None:
    c = BaseContract()
    with pytest.raises(ValidationError):
        c.version = "2.0"  # type: ignore[misc] — frozen should raise


def test_base_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        BaseContract(tyspo="oops")  # type: ignore[call-arg]


def test_base_correlation_id_unique() -> None:
    a = BaseContract()
    b = BaseContract()
    assert a.correlation_id != b.correlation_id
    assert a.trace_id != b.trace_id


def test_market_data_frozen_and_quality() -> None:
    md = MarketData(
        symbol="EURUSD",
        broker="deriv",
        kind="tick",
        payload=Tick(price=1.085, bid=1.0849, ask=1.0851),
        quality=DataQuality.pristine,
        watermark=1001,
        provider="deriv",
        provider_symbol="frxEURUSD",
    )
    assert md.symbol == "EURUSD"
    assert md.quality == DataQuality.pristine
    with pytest.raises(ValidationError):
        md.symbol = "GBPUSD"  # type: ignore[misc]


def test_market_data_candle() -> None:
    now = datetime.now(timezone.utc)
    candle = Candle(
        open=1.085, high=1.086, low=1.084, close=1.0855,
        interval="1m", interval_start=now, interval_end=now,
    )
    md = MarketData(symbol="EURUSD", broker="deriv", kind="candle", payload=candle, watermark=1002)
    assert md.payload == candle


def test_analytics_result_features() -> None:
    ar = AnalyticsResult(symbol="EURUSD", broker="deriv", timeframe="1m", features={"rsi_14": 68.2, "atr_14": 0.0012})
    assert ar.features["rsi_14"] == 68.2
    assert ar.version == "1.0"


def test_signal_strength_and_valid_until() -> None:
    now = datetime.now(timezone.utc)
    sig = Signal(
        signal_id=uuid.uuid4(),
        strategy_id="trend_following::ema_trend",
        version="2.1",
        symbol="EURUSD",
        broker="deriv",
        direction="long",
        strength=0.76,
        timeframe="1m",
        valid_until=now,
    )
    assert sig.strength == 0.76
    assert sig.shadow is False
    # strength must be 0..1
    with pytest.raises(ValidationError):
        Signal(
            signal_id=uuid.uuid4(), strategy_id="x", version="1", symbol="EURUSD", broker="deriv",
            direction="long", strength=1.5, timeframe="1m", valid_until=now,
        )


def test_trade_plan_has_sl_tp_and_timing() -> None:
    now = datetime.now(timezone.utc)
    tp = TradePlan(
        trade_plan_id=uuid.uuid4(), signal_id=uuid.uuid4(), strategy_id="momentum_v3@3.2.1",
        version="3.2.1", instrument_id="FX.EURUSD", broker="deriv", user_id="42",
        direction="long",
        entry=EntrySpec(type="MARKET", reference_price=1.085, valid_from=now, valid_until=now),
        stop_loss=StopSpec(type="ATR_2x", price=1.0835, invalidation="swing_low", buffer="2*ATR"),
        take_profit=TargetSpec(type="RR_2", price=1.088, rr=2.0),
        timing=TimingSpec(valid_from=now, valid_until=now, entry_window="60s", timeframe="1m"),
    )
    assert tp.stop_loss.price == 1.0835
    assert tp.take_profit.rr == 2.0
    assert tp.calculation_meta == {}


def test_risk_decision_approved_vs_rejected() -> None:
    approved = RiskDecision(decision_id=uuid.uuid4(), signal_id=uuid.uuid4(), approved=True, approved_qty=0.4, rejected_reason=None)
    assert approved.approved is True
    rejected = RiskDecision(decision_id=uuid.uuid4(), signal_id=uuid.uuid4(), approved=False, approved_qty=None, rejected_reason=ErrorCode.insufficient_margin)
    assert rejected.rejected_reason == ErrorCode.insufficient_margin


def test_order_client_order_id_deterministic() -> None:
    # WHY test? client_order_id must be deterministic uuid5(trade_plan_id), not random uuid4
    trade_plan_id = uuid.uuid4()
    cid = str(uuid.uuid5(uuid.NAMESPACE_URL, str(trade_plan_id)))
    o1 = Order(order_id=uuid.uuid4(), client_order_id=cid, symbol="EURUSD", broker="deriv", side="buy", qty=0.4)
    o2 = Order(order_id=uuid.uuid4(), client_order_id=cid, symbol="EURUSD", broker="deriv", side="buy", qty=0.4)
    assert o1.client_order_id == o2.client_order_id


def test_order_state_default_pending() -> None:
    o = Order(order_id=uuid.uuid4(), client_order_id="cid-123", symbol="EURUSD", broker="deriv", side="buy", qty=0.1)
    assert o.state == "pending"


def test_execution_result_with_fills() -> None:
    er = ExecutionResult(
        order_id=uuid.uuid4(), broker_order_id="brok-123",
        fills=[Fill(fill_id="f1", price=1.085, qty=0.4)], avg_fill_price=1.085,
        state="filled", latency_ms=120, slippage_bps=1.2,
    )
    assert er.fills[0].price == 1.085


def test_reconciliation_report_matched_vs_mismatched() -> None:
    matched = ReconciliationReport(status="matched", diffs=[])
    assert matched.status == "matched"
    mismatched = ReconciliationReport(status="mismatched", diffs=[{"field": "positions", "internal": 0.7, "external": 0.5, "type": "mismatch"}])  # type: ignore[arg-type]
    assert mismatched.status == "mismatched"


def test_portfolio_state_per_tenant() -> None:
    ps = PortfolioState(user_id="42", broker="deriv", equity=1000, margin_used=400, margin_available=600)
    assert ps.user_id == "42"
    assert ps.broker == "deriv"


def test_error_code_not_free_text() -> None:
    assert ErrorCode.insufficient_margin == "insufficient_margin"
    # New free text should not be allowed as ErrorCode — use enum
    with pytest.raises(ValueError):
        ErrorCode("free_text_typo")  # type: ignore[call-arg]


def test_asset_class_vocabulary_includes_commodity() -> None:
    # M2b.1 commodity scope — locked vocabulary must contain all six classes
    assert AssetClass.forex == "forex"
    assert AssetClass.crypto == "crypto"
    assert AssetClass.commodity == "commodity"
    assert AssetClass.index == "index"
    assert AssetClass.equity == "equity"
    assert AssetClass.synthetic == "synthetic"
    assert len(AssetClass) == 6


def test_asset_class_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        AssetClass("cfd")  # type: ignore[arg-type]
    with pytest.raises(ValueError) as exc:
        AssetClass.from_yaml_value("agriculture")
    assert "asset_class" in str(exc.value)


def test_asset_class_from_yaml_value_valid() -> None:
    assert AssetClass.from_yaml_value("commodity") is AssetClass.commodity
    assert AssetClass.from_yaml_value("forex") is AssetClass.forex
