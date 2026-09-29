"""
M2b.7 Universe screen - derive the tradable universe from LIVE broker data.

WHY this file exists: "25 assets" appears in FIVE spec/doc places
(m2b.4_trading_calendar.spec.md:147,166; m2.6_gap_calendar.spec.md:157,167;
BUILD_STATUS.md:457) and sized three design decisions, but NO list of 25 ever existed
anywhere - not in config, not in git history, not in the bible. Only 5 instruments were
ever configured. So the documents asserted a universe the code never had.

The human's stated criterion is "most traded and other things". This script derives the
"most traded" half from an AUTHORITATIVE LIVE SOURCE - `active_symbols.trade_count` -
so the universe is computed from real broker data on a given date rather than asserted
by hand. Re-runnable, and the date is printed so a later reader can tell WHICH
point-in-time snapshot produced a given universe.

SCOPE (human, 2026-09-29): forex + commodity + crypto. Synthetic/equity (R_*, 1HZ*,
BOOM*, CRASH*, stpR*) is excluded by the M2b.1 AssetClass 2.0.0 cut.

LOOK-AHEAD WARNING, RECORDED NOT HIDDEN (AGENTS.md 3.4):
  Ranking on `trade_count` is a measure of CURRENT activity - a point-in-time
  characteristic, not a past return. That is the safe half of a screen. Ranking on
  realised return or Sharpe would be the unsafe half and would bake survivorship bias
  into every backtest, inflating G0's expectancy. This script deliberately uses only
  activity/structure. The universe MUST still be frozen per walk-forward fold from data
  up to the training window; see BUILD_STATUS.md.

Auth: NOT REQUIRED. `active_symbols` is a public market-data call, so this file
deliberately contains no token handling and therefore no secret to leak.

Run:  python scripts/verify_m2b.7_universe_screen.py [--top 25] [--out FILE]
Exit: 0 = ranking produced. 1 = fewer in-scope symbols than requested (loud, not silent).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import websockets

URL = "wss://api.derivws.com/trading/v1/options/ws/public"

#: Deriv prefixes every forex/metals/crypto underlier with these. Everything else on the
#: platform (R_*, 1HZ*, BOOM*, CRASH*, JD*, WLDA*, OTC_*, stpR*) is synthetic/equity and
#: is OUT OF SCOPE per AssetClass 2.0.0.
IN_SCOPE_PREFIXES = ("frx", "cry")

#: Our canonical asset class, from the broker's own `submarket`. `frx` covers BOTH
#: forex majors/minors and metals, so the native prefix alone cannot classify an
#: instrument - this mapping is what turns `frxXAUUSD` into COMMODITY.XAUUSD.
SUBMARKET_CLASS = {
    "major_pairs": "forex",
    "minor_pairs": "forex",
    "metals": "commodity",
    "non_stable_coin": "crypto",
    "stable_coin": "crypto",
}


def _classify(row: dict[str, Any]) -> str:
    return SUBMARKET_CLASS.get(str(row.get("submarket") or ""), "unknown")


async def fetch_active_symbols() -> list[dict[str, Any]]:
    """One public call. WHY exactly {"active_symbols": "brief"} and nothing more: that is
    the request verify_m2b.5_broker_reference.py proves works. Adding a "helpful"
    product_type made an earlier revision TIME OUT - an unverified extra field is a
    change to a contract, which is how the M2.7 fetcher's six errors happened."""
    async with websockets.connect(URL, open_timeout=25) as ws:
        await ws.send(json.dumps({"active_symbols": "brief"}))
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if "error" in frame:
                raise RuntimeError(f"broker error: {frame['error']}")
            if frame.get("msg_type") == "active_symbols":
                return list(frame.get("active_symbols") or [])


def screen(symbols: list[dict[str, Any]], top: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for s in symbols:
        native = str(s.get("underlying_symbol") or "")
        if not native.startswith(IN_SCOPE_PREFIXES):
            continue
        rows.append(
            {
                "native": native,
                "asset_class": _classify(s),
                "submarket": s.get("submarket"),
                "trade_count": s.get("trade_count"),
                "pip_size": s.get("pip_size"),
                "exchange_is_open": s.get("exchange_is_open"),
                "is_trading_suspended": s.get("is_trading_suspended"),
                "screened_at": datetime.now(UTC).date().isoformat(),
            }
        )
    # WHY trade_count None sorts LAST, not first: an unknown volume must never be
    # mistaken for the most liquid instrument in the universe.
    rows.sort(key=lambda r: (r["trade_count"] is None, -(r["trade_count"] or 0)))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--out", type=Path, default=None, help="write the selection as JSON")
    args = ap.parse_args()

    print("=" * 78)
    print("M2b.7 UNIVERSE SCREEN - live Deriv active_symbols (no credentials)")
    print("=" * 78)
    print(f"screened_at : {datetime.now(UTC).isoformat()}")
    print("criterion   : trade_count (live activity) - a POINT-IN-TIME characteristic")
    print("scope       : forex + commodity + crypto; synthetics/equity excluded\n")

    rows = screen(asyncio.run(fetch_active_symbols()), args.top)

    print(f"in-scope symbols: {len(rows)}")
    by_class: dict[str, int] = {}
    for r in rows:
        by_class[r["asset_class"]] = by_class.get(r["asset_class"], 0) + 1
    print(f"  by class: {by_class}\n")

    header = f"{'#':>3}  {'native':<12} {'class':<10} {'trade_count':>12}  {'pip':>9}  open"
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows, 1):
        mark = "  <= selected" if i <= args.top else ""
        print(
            f"{i:>3}  {r['native']:<12} {r['asset_class']:<10} "
            f"{r['trade_count']:>12,}  {r['pip_size']:>9}  {r['exchange_is_open']!s:<5}{mark}"
        )

    selected = rows[: args.top]
    excluded = rows[args.top :]

    print()
    print(f"SELECTED {len(selected)} - total trade_count "
          f"{sum(r['trade_count'] or 0 for r in selected):,}")
    sel_class: dict[str, int] = {}
    for r in selected:
        sel_class[r["asset_class"]] = sel_class.get(r["asset_class"], 0) + 1
    print(f"  by class: {sel_class}")
    if excluded:
        print(f"EXCLUDED {len(excluded)} (least active): "
              f"{', '.join(r['native'] for r in excluded)}")
        cut_sel = selected[-1]["trade_count"] if selected else 0
        cut_exc = excluded[0]["trade_count"] if excluded else 0
        print(f"  boundary: selected ends at {cut_sel:,}, excluded starts at {cut_exc:,}")
        # HONESTY: a near-equal boundary means `top` is a TARGET, not a natural break.
        if cut_exc and cut_sel / max(cut_exc, 1) < 1.5:
            print("  ** the boundary is NOT a natural break (ratio < 1.5). "
                  f"{args.top} is a chosen target, not a data-determined cut. **")

    if args.out:
        args.out.write_text(
            json.dumps(
                {
                    "screened_at": datetime.now(UTC).isoformat(),
                    "criterion": "trade_count",
                    "top": args.top,
                    "in_scope_count": len(rows),
                    "selected": selected,
                    "excluded": excluded,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\nwritten: {args.out}")

    if len(rows) < args.top:
        print(f"\n[FAIL] only {len(rows)} in-scope symbols, cannot select {args.top}")
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
