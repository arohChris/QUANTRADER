import { useState } from "react";

export default function TradingTicket() {
  const [side, setSide] = useState("buy");
  const [orderType, setOrderType] = useState("market");

  return (
    <form className="panel trading-ticket" onSubmit={(event) => event.preventDefault()}>
      <h2>Manual trade ticket</h2>
      <label htmlFor="trade-instrument">Instrument</label>
      <input id="trade-instrument" defaultValue="FX.EURUSD" />
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
