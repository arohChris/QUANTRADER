// WHY: mock trader data for the Portfolio, Orders and Settings surfaces.
// Every field name is copied from the FROZEN contracts so the surface can be wired to
// the real endpoints without a redesign. The VALUES are mock.
//
//   Position / PortfolioState -> financial_analyst_app/shared/contracts/portfolio.py
//   Order / OrderIntent       -> shared/contracts/order.py
//   Fill / ExecutionResult    -> shared/contracts/execution.py
//   TradePlan                 -> shared/contracts/trade_plan.py
//   RiskDecision              -> shared/contracts/risk.py
//   BaseContract lineage      -> shared/contracts/base.py
//   account two-gate fields   -> docs/specs/m1.3_account_mode_two_gate.spec.md
//   risk limit names          -> shared/core/config.py
//
// Rules honoured here (not optional):
//   * No `side` on a position: the sign of `qty` is the side (portfolio.py:14).
//   * No `filled_qty` on an order: partial fills live in ExecutionResult.fills (execution.py:34).
//   * No per-position stop/target: SL and TP are order/trade-plan fields (order.py:53-54).
//   * No created_at/updated_at: the contract carries one `timestamp` (base.py:62).
//   * Money, margin, exposure and risk percentages are backend-owned (frontend_spec.md:61);
//     the surfaces display them as read-only and never recompute them.

export type OrderState =
  | "pending"
  | "submitted"
  | "acked"
  | "partially_filled"
  | "filled"
  | "cancelled"
  | "rejected"
  | "unknown";

export const ORDER_STATES: OrderState[] = [
  "pending",
  "submitted",
  "acked",
  "partially_filled",
  "filled",
  "cancelled",
  "rejected",
  "unknown",
];

// Legal edges only (modules/06_M6_Execution.md:52-62): pending -> submitted -> acked ->
// partially_filled -> filled, pending -> rejected, any -> unknown, unknown -> filled|rejected.
export const ORDER_TRANSITIONS: Record<string, string[]> = {
  pending: ["submitted", "rejected", "unknown"],
  submitted: ["acked", "rejected", "unknown"],
  acked: ["partially_filled", "filled", "cancelled"],
  partially_filled: ["filled", "cancelled", "unknown"],
  filled: [],
  cancelled: [],
  rejected: [],
  unknown: ["filled", "rejected"],
};

export type Position = {
  symbol: string;
  qty: number; // lots, positive long, negative short (portfolio.py:14)
  avg_entry: number;
  unrealized_pnl: number | null;
  realized_pnl: number | null;
  last_trade_plan_id: string | null;
};

export type PortfolioState = {
  version: string;
  correlation_id: string;
  trace_id: string;
  timestamp: string;
  user_id: string | null;
  broker: string | null;
  positions: Record<string, Position>;
  cash: Record<string, number>;
  equity: number;
  margin_used: number;
  margin_available: number;
  leverage: number;
  exposure: Record<string, number>;
  producer: string;
};

export type Fill = {
  fill_id: string;
  price: number;
  qty: number;
  side: "buy" | "sell" | null;
  timestamp: string | null;
  commission: number | null;
};

export type ExecutionResult = {
  order_id: string;
  trade_plan_id: string | null;
  broker_order_id: string | null;
  fills: Fill[];
  avg_fill_price: number | null;
  state: "filled" | "partially_filled" | "rejected" | "unknown";
  latency_ms: number | null;
  slippage_bps: number | null;
};

export type MockOrder = {
  order_id: string;
  client_order_id: string; // idempotency key, uuid5(trade_plan_id) (order.py:44)
  intent_id: string | null;
  trade_plan_id: string | null;
  symbol: string;
  broker: string;
  side: "buy" | "sell";
  qty: number;
  order_type: "market" | "limit" | "stop";
  price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  state: OrderState;
  broker_capability_checked: boolean;
  user_id: string | null;
  version: string;
  correlation_id: string;
  trace_id: string;
  timestamp: string;
  execution: ExecutionResult | null;
};

export type RiskDecision = {
  decision_id: string;
  signal_id: string | null;
  trade_plan_id: string | null;
  approved: boolean;
  approved_qty: number | null;
  rejected_reason: string | null;
  sizing_method: "vol_target" | "fixed_fractional" | "risk_budget";
  limits_checked: Record<string, boolean>;
  latency_budget_ms: number | null;
};

// Two independent gates (shared/core/account_mode.py:3-4). live_armed is "the hard gate is
// satisfied now", not "is live". bot_will_trade is the documented predicate.
export type AccountStatus = {
  mode: "demo" | "live";
  auto: "OFF" | "ON";
  source: "default" | "env" | "api";
  auto_source: "default" | "api";
  live_armed: boolean;
  bot_will_trade: boolean;
};

// Automatic -> Your Override -> Reset to Default (System_Development_Manual.md:108-130).
export type RiskLimits = {
  max_exposure_per_symbol: number;
  max_leverage: number;
  min_free_margin: number;
  max_drawdown: number;
};

export type LimitOverride = {
  key: keyof RiskLimits;
  automatic: number;
  override: number | null;
  boundary: number | null;
};

const REF = "REF-EXPERIMENT-001";

export const MOCK_ACCOUNT: AccountStatus = {
  mode: "demo",
  auto: "OFF",
  source: "default",
  auto_source: "default",
  live_armed: false,
  bot_will_trade: false,
};

export const MOCK_KILL: { active: boolean; scope: string; reason: string | null } = {
  active: false,
  scope: "global",
  reason: null,
};

export const MOCK_LIMITS: RiskLimits = {
  max_exposure_per_symbol: 0.1,
  max_leverage: 10,
  min_free_margin: 100,
  max_drawdown: 0.2,
};

export const MOCK_OVERRIDES: LimitOverride[] = [
  { key: "max_exposure_per_symbol", automatic: 0.1, override: 0.05, boundary: 0.5 },
  { key: "max_leverage", automatic: 10, override: 5, boundary: 50 },
  { key: "min_free_margin", automatic: 100, override: null, boundary: null },
  { key: "max_drawdown", automatic: 0.2, override: null, boundary: null },
];

// Account slots hold references only — env and vault NAMES, never values (shared/core/config.py:55).
export const MOCK_ACCOUNT_SLOTS = [
  { broker: "deriv", mode: "demo", enabled: true, account_id_env: "DERIV_DEMO_ACCOUNT_ID" },
  { broker: "mt5", mode: "demo", enabled: false, account_id_env: "MT5_DEMO_ACCOUNT_ID" },
];

export const MOCK_PORTFOLIO: PortfolioState = {
  version: "2.1.0",
  correlation_id: "corr-9f21b7",
  trace_id: "trace-3ac514",
  timestamp: "2026-09-26T12:41:07Z",
  user_id: "42",
  broker: "deriv",
  positions: {
    "FX.EURUSD": {
      symbol: "FX.EURUSD",
      qty: 0.4,
      avg_entry: 1.0842,
      unrealized_pnl: 18.4,
      realized_pnl: 42.1,
      last_trade_plan_id: "tp-6b21f0",
    },
    "CRYPTO.BTCUSD": {
      symbol: "CRYPTO.BTCUSD",
      qty: -0.05,
      avg_entry: 64180.5,
      unrealized_pnl: -126.4,
      realized_pnl: 0,
      last_trade_plan_id: "tp-1d90aa",
    },
    "COMMODITY.XAUUSD": {
      symbol: "COMMODITY.XAUUSD",
      qty: 0,
      avg_entry: 2318.4,
      unrealized_pnl: null,
      realized_pnl: 88.9,
      last_trade_plan_id: "tp-77c3e5",
    },
  },
  cash: { USD: 1000, EUR: 500 },
  equity: 1380.1,
  margin_used: 264.9,
  margin_available: 735.1,
  leverage: 10,
  exposure: { "FX.EURUSD": 43.37, "CRYPTO.BTCUSD": 3209.03 },
  producer: "M5.portfolio_risk",
};

export const MOCK_ORDERS: MockOrder[] = [
  {
    order_id: "ord-2f81c4",
    client_order_id: "a3f1c9d2-6b41-5c8e-9f20-1d7b4e0a55c1",
    intent_id: "intent-5c1a02",
    trade_plan_id: "tp-6b21f0",
    symbol: "FX.EURUSD",
    broker: "deriv",
    side: "buy",
    qty: 0.4,
    order_type: "limit",
    price: 1.0842,
    stop_loss: 1.0818,
    take_profit: 1.0891,
    state: "filled",
    broker_capability_checked: true,
    user_id: "42",
    version: "2.1.0",
    correlation_id: "corr-9f21b7",
    trace_id: "trace-3ac514",
    timestamp: "2026-09-26T11:58:22Z",
    execution: {
      order_id: "ord-2f81c4",
      trade_plan_id: "tp-6b21f0",
      broker_order_id: "DX-88213374",
      fills: [
        { fill_id: "fill-01", price: 1.0842, qty: 0.2, side: "buy", timestamp: "2026-09-26T11:58:24Z", commission: -1.2 },
        { fill_id: "fill-02", price: 1.0843, qty: 0.2, side: "buy", timestamp: "2026-09-26T11:58:26Z", commission: -1.2 },
      ],
      avg_fill_price: 1.08425,
      state: "filled",
      latency_ms: 214,
      slippage_bps: 0.9,
    },
  },
  {
    order_id: "ord-77b0d9",
    client_order_id: "c7e2b48a-1f93-4a6d-8b55-93d2c1e70f44",
    intent_id: "intent-5c1a03",
    trade_plan_id: "tp-1d90aa",
    symbol: "CRYPTO.BTCUSD",
    broker: "deriv",
    side: "sell",
    qty: 0.05,
    order_type: "market",
    price: null,
    stop_loss: 65200,
    take_profit: 61800,
    state: "partially_filled",
    broker_capability_checked: true,
    user_id: "42",
    version: "2.1.0",
    correlation_id: "corr-1b77e0",
    trace_id: "trace-90cd21",
    timestamp: "2026-09-26T12:22:41Z",
    execution: {
      order_id: "ord-77b0d9",
      trade_plan_id: "tp-1d90aa",
      broker_order_id: "DX-88214009",
      fills: [{ fill_id: "fill-03", price: 64180.5, qty: 0.02, side: "sell", timestamp: "2026-09-26T12:22:43Z", commission: -4.1 }],
      avg_fill_price: 64180.5,
      state: "partially_filled",
      latency_ms: 388,
      slippage_bps: 1.4,
    },
  },
  {
    order_id: "ord-9e4412",
    client_order_id: "b1905f7e-6c2a-4d18-9f73-2ad4be6013cc",
    intent_id: null,
    trade_plan_id: null,
    symbol: "FX.GBPUSD",
    broker: "deriv",
    side: "buy",
    qty: 0.2,
    order_type: "limit",
    price: 1.2705,
    stop_loss: null,
    take_profit: null,
    state: "unknown",
    broker_capability_checked: true,
    user_id: "42",
    version: "2.1.0",
    correlation_id: "corr-55aa31",
    trace_id: "trace-7f1d02",
    timestamp: "2026-09-26T12:35:10Z",
    execution: {
      order_id: "ord-9e4412",
      trade_plan_id: null,
      broker_order_id: null,
      fills: [],
      avg_fill_price: null,
      state: "unknown",
      latency_ms: null,
      slippage_bps: null,
    },
  },
  {
    order_id: "ord-31c8ba",
    client_order_id: "d40b6a11-9e73-4c5f-8a26-7f1c3d9b0e55",
    intent_id: "intent-5c1a01",
    trade_plan_id: "tp-04af19",
    symbol: "COMMODITY.XAUUSD",
    broker: "deriv",
    side: "sell",
    qty: 0.1,
    order_type: "stop",
    price: 2310.0,
    stop_loss: 2326.5,
    take_profit: 2288.0,
    state: "rejected",
    broker_capability_checked: false,
    user_id: "42",
    version: "2.1.0",
    correlation_id: "corr-0ab7d2",
    trace_id: "trace-44ce19",
    timestamp: "2026-09-26T10:12:03Z",
    execution: null,
  },
];

export const MOCK_RISK_DECISION: RiskDecision = {
  decision_id: "rd-51b7c0",
  signal_id: "sig-9d2e71",
  trade_plan_id: "tp-6b21f0",
  approved: true,
  approved_qty: 0.4,
  rejected_reason: null,
  sizing_method: "vol_target",
  limits_checked: {
    max_exposure_per_symbol: true,
    max_leverage: true,
    min_free_margin: true,
    max_drawdown: true,
  },
  latency_budget_ms: 10,
};

// M10.4.11 trade summary (dashboard header). Bucket semantics are ours; the SCOPING
// fields are contract-shaped: user_id/broker (portfolio.py), Order.state buckets
// (order.py: pending/submitted/acked/partially_filled = active, filled = closed,
// rejected = rejected). The VALUES are precomputed mock aggregates — the frontend
// never derives them (frontend_spec.md money law); real figures wire to M6/M7 later.
// Hand check: the day record reconciles to MOCK_ORDERS above (2 active incl. the
// unknown probe, 1 filled closed, 1 rejected); larger periods are mock growth from
// the same account. Period keys are lowercase words (spec Amendment A1) so they
// never collide with the chart-timeframe literals banned app-wide by M10.4.10.
export type TradeSummaryPeriod = "day" | "week" | "month" | "year" | "all";

export type TradeSummary = {
  period: TradeSummaryPeriod;
  user_id: string | null;
  broker: string | null;
  active: number;
  closed: number;
  rejected: number;
  total_pnl: number | null; // backend-owned money, precomputed, signed USD
};

export const TRADE_SUMMARY_PERIODS: TradeSummaryPeriod[] = ["day", "week", "month", "year", "all"];

export const MOCK_TRADE_SUMMARY: Record<TradeSummaryPeriod, TradeSummary> = {
  day: { period: "day", user_id: "42", broker: "deriv", active: 2, closed: 1, rejected: 1, total_pnl: 42.1 },
  week: { period: "week", user_id: "42", broker: "deriv", active: 3, closed: 7, rejected: 2, total_pnl: 218.45 },
  month: { period: "month", user_id: "42", broker: "deriv", active: 4, closed: 24, rejected: 5, total_pnl: -86.2 },
  year: { period: "year", user_id: "42", broker: "deriv", active: 5, closed: 168, rejected: 17, total_pnl: 1240.55 },
  all: { period: "all", user_id: "42", broker: "deriv", active: 5, closed: 402, rejected: 39, total_pnl: 2964.3 },
};
