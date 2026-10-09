import { useState } from "react";

import { MOCK_INSTRUMENTS, MOCK_TIMEFRAMES } from "./experiment_market";

export default function TradingTicket() {
  // Amendment A1 (m10.4.10): asset is chosen from the catalogue, not typed —
  // default keeps the previous hardcoded FX.EURUSD, so no behaviour change.
  const [instrument, setInstrument] = useState("FX.EURUSD");
  const [side, setSide] = useState("buy");
  const [orderType, setOrderType] = useState("market");
  // Explicit selection required — no silent default (m10.4.10 spec I2):
  // the ticket holds "" until the user picks one of the nine registry keys.
  const [timeframe, setTimeframe] = useState("");

  return (
    <form className="panel trading-ticket" onSubmit={(event) => event.preventDefault()}>
      <h2>Manual trade ticket</h2>
      <label htmlFor="trade-instrument">Instrument</label>
      <select id="trade-instrument" value={instrument} onChange={(event) => setInstrument(event.target.value)}>
        {MOCK_INSTRUMENTS.map((entry) => (
          <option key={entry.symbol} value={entry.symbol}>
            {entry.label}
          </option>
        ))}
      </select>
      <label htmlFor="trade-timeframe">Timeframe</label>
      <select id="trade-timeframe" value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
        <option value="">Select timeframe</option>
        {MOCK_TIMEFRAMES.map((entry) => (
          <option key={entry} value={entry}>
            {entry}
          </option>
        ))}
      </select>
      <label htmlFor="trade-side">Side</label>
      <select id="trade-side" value={side} onChange={(event) => setSide(event.target.value)}>
        <option value="buy">Buy</option>
        <option value="sell">Sell</option>
      </select>
      <label htmlFor="trade-order-type">Order type</label>
      <select id="trade-order-type" value={orderType} onChange={(event) => setOrderType(event.target.value)}>
        <option value="market">Market</option>
        <option value="limit">Limit</option>
        <option value="stop">Stop</option>
      </select>
      <label htmlFor="trade-quantity">Quantity</label>
      <input id="trade-quantity" inputMode="decimal" placeholder="0.00" />
      <label htmlFor="trade-entry">Entry</label>
      <input id="trade-entry" inputMode="decimal" placeholder="market" />
      <label htmlFor="trade-stop-loss">Stop loss</label>
      <input id="trade-stop-loss" inputMode="decimal" placeholder="optional" />
      <label htmlFor="trade-take-profit">Take profit</label>
      <input id="trade-take-profit" inputMode="decimal" placeholder="optional" />
      <div className="ticket-actions">
        <button type="button" disabled>
          Preview
        </button>
        <button type="submit" disabled>
          Confirm
        </button>
      </div>
      <p className="muted">Preview-only mock. No order is submitted.</p>
    </form>
  );
}
