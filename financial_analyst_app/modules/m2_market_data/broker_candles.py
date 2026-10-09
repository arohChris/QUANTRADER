"""M2.13 — broker 1-minute candle ingest. THE BROKER IS THE OHLC AUTHORITY.

Spec: docs/specs/m2.13_broker_candle_ingest.spec.md (REV2)
Consumers: modules.m2_market_data/broker_feeds/deriv/stream.py (route_frame), and any live
           pipeline that wants venue OHLC instead of its own tick digestion.
Contract: shared.contracts.market_data.Candle 2.0.0 (human §5 sign-off 2026-09-29).

WHY THIS EXISTS (measured, not asserted)
-----------------------------------------
Our live tick->1m digestion disagreed with the venue's own candle on **3 of 202** live-built
bars. Both producers wrote the same `candles_1m` table, so a backtest trained on Deriv's
series while live trading ran on ours, and nothing reconciled them. A walk-forward that
validates a market you do not trade is worse than no walk-forward, so the venue became the
source of OHLC and this module is the seam.

WHAT THIS MODULE IS NOT (spec "Architectural boundary" — a hard boundary, not a preference)
----------------------------------------------------------------------------------------
It is a PRODUCER. It emits one canonical `Candle` and forgets. It has **no** storage policy,
**no** Valkey client, **no** `asyncpg` pool, **no** `LakeWriter`, **no** read-back and **no**
repair — the sinks (Valkey hot state, Timescale durable history) are PEER downstream
consumers, and the wiring between them is not this slice's job.

That is why every function here is a plain `def` and NOT `async def`: this module performs
no I/O whatsoever, and an `async def` would advertise work that does not happen. It is also
why it is trivially testable with no network, no database and no cache — which is the
observable proof of the boundary rather than a claim about it.

FINALITY — three states, not one (see the spec section of the same name)
-----------------------------------------------------------------------
1. FORMING    the venue is still revising this minute and re-sends `open_time` ~60x. M2.13
              emits NOTHING for it. A downstream hot consumer is free to show it moving.
2. CANONICAL  a frame arrives carrying a LATER `open_time`, or `close()` is called. The
              minute is finished and is emitted EXACTLY ONCE.
3. FINAL      already written. Never silently rewritten. A contradicting frame is counted in
              `late_frame_count` and dropped, because `ON CONFLICT DO NOTHING` would discard
              the rewrite anyway, and accepting it would make the producer believe it
              corrected a bar it did not.

WHY `open_time` IS THE KEY AND `epoch` IS NOT
----------------------------------------------
Measured on the live socket 2026-09-29: a frame arrives with `open_time 1790708820` and
`epoch 1790708822` — the epoch is the LAST TICK time, two seconds into the following
minute. Keying the bucket on `epoch` would file the bar under the wrong minute. `open_time`
is the bucket start; `epoch` is advisory and its absence is NOT fatal.

MONEY-PATH AND NUMERIC NOTES
----------------------------
* OHLC arrive as JSON **strings** on the live feed (`"close": "83654.167"`) and as JSON
  **numbers** on the history feed. `json.loads(parse_float=Decimal)` covers only the second
  case, so both are converted explicitly and a string that is not a clean finite decimal is
  DROPPED rather than coerced. `1e400`, `NaN`, `Infinity`, `""` and `"1,2"` all fail here.
* Prices are stored VERBATIM from the wire, with no rounding to `tick_size` anywhere. The
  venue publishes on its own tick grid; second-guessing it is exactly how the historical
  `tick_size` 0.01-vs-0.001 defect truncated live prices to 2dp.
* `Decimal`, never `float`, anywhere a value is compared or accumulated. Comparison is
  `Decimal("83589.140") == Decimal("83589.14")` -> True; the naive string compare is False
  and would hold every broker bar for review forever.
* `spread = ask - bid`, POSITIVE, matching `normalize.py:114-115` and `candles.py:146`.
  `min_spread` is therefore the TIGHTEST quote and `max_spread` the widest, and a crossed
  quote (`ask < bid`) yields a NEGATIVE result, so the validity test is `ask >= bid`.
* All-or-none spread, preserving `candles.py:169-175`: if ANY tick in the minute lacks a
  side, all three stats are `None`. A partial average would be a statistic over a sample we
  did not measure. A minute with no ticks has spread `None` while its OHLC are still
  written — those came from the venue, and our silence says nothing about their validity.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from shared.contracts.drop import Drop, DropReason
from shared.contracts.market_data import Candle

__all__ = ["BrokerCandleIngestor", "ParsedBar", "parse_ohlc_frame"]

GRANULARITY_S = 60
#: `open_time` beyond this is refused. `2**53` is a second a few hundred thousand years out;
#: `datetime.fromtimestamp` would raise OverflowError, and an uncaught OverflowError is a
#: crash, not a Drop (AGENTS.md 3.13 - external input is schema-validated before use).
MAX_OPEN_TIME = 4_102_444_800  # 2100-01-01T00:00:00Z
#: A frame may not be stamped more than this far into the future. 5s absorbs ordinary clock
#: skew between our host and the venue without admitting a genuinely bogus timestamp.
FUTURE_TOLERANCE_S = 5
#: Sanity bound on a price. `Decimal("1e400")` is perfectly FINITE to Decimal — it is not NaN
#: and not Infinity — so a finiteness check alone would happily store it. Quote-per-base
#: prices are ~1 for FX and ~1e5 for BTC; 1e12 is six orders of magnitude above anything real
#: and exists purely so an absurd exponent becomes a typed Drop instead of a stored number.
MAX_PRICE = Decimal("1e12")


def _to_decimal(value: Any) -> Decimal | None:
    """Exact Decimal from a JSON string OR number, or None if it is not a usable price.

    WHY not `float` anywhere on this path: the whole point of this slice is that the venue's
    value is reproduced exactly. Routing through binary float reintroduces the error the
    Decimal comparison exists to avoid, and `Decimal(str(1.1))` recovers the literal only by
    coincidence. Non-finite input is refused here rather than propagating into arithmetic,
    where a NaN would silently poison every downstream comparison.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, int):
        candidate = Decimal(value)
    elif isinstance(value, float):
        # A JSON number arrives as float through json.loads; repr() of a float is the
        # shortest string that round-trips, so Decimal(repr(x)) is exact for what was sent.
        candidate = Decimal(repr(value))
    elif isinstance(value, str):
        try:
            candidate = Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
    else:
        return None
    if not candidate.is_finite():
        return None
    return candidate


def _to_epoch(value: Any) -> int | None:
    """Coerce an epoch from int or numeric string. None on anything else.

    A string is accepted because the live feed has been observed sending `open_time` as a
    string; it is refused silently rather than raised, since the caller drops on None.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except (ValueError, TypeError):
            return None
    return None


@dataclass(frozen=True, slots=True)
class ParsedBar:
    """A venue bar, still as Decimals and an int epoch. Not yet a `Candle`.

    WHY a separate type rather than returning a `Candle` straight from the parser: parsing is
    a pure function of a frame and has no notion of a buffer, a finality rule, or a spread
    sample. Keeping the two apart is what lets I-1 (live vs history, both transports, exact
    equality) be asserted on the parser without constructing an ingestor.
    """

    open_time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    epoch: int | None
    granularity: int | None
    symbol: str | None

    def __getitem__(self, key: str) -> Any:
        """Subscriptable, so a parsed bar can be compared field-by-field across transports.

        WHY: the invariant that justifies this whole slice is that the SAME bar fetched from
        the live feed and from the history endpoint is exactly equal. Asserting that reads
        naturally as `a["close"] == b["close"]`, and it keeps the test honest about the
        wire SHAPE (strings vs numbers) rather than about a dataclass API.
        """
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc


def _drop(reason: DropReason, detail: str, provider: str) -> Drop:
    return Drop(reason=reason, provider=provider, detail=detail, raw_payload="")


def _prices(row: dict[str, Any], provider: str) -> tuple[dict[str, Decimal] | None, Drop | None]:
    """Extract the four prices as non-optional Decimals, or say exactly why we cannot.

    Returning a `dict[str, Decimal]` (rather than four `Decimal | None`s) is deliberate: it
    makes "all four parsed" a TYPE-level fact instead of something every caller has to
    re-prove, and it is what stops a None ever reaching a `>` comparison.
    """
    parsed: dict[str, Decimal] = {}
    bad: list[str] = []
    for name in ("open", "high", "low", "close"):
        if name not in row:
            return None, _drop(DropReason.missing_field, f"missing {name}", provider)
        value = _to_decimal(row.get(name))
        if value is None:
            bad.append(name)
        else:
            parsed[name] = value
    if bad:
        return None, _drop(DropReason.invalid_price, f"non-numeric {bad}", provider)
    if parsed["low"] > parsed["high"]:
        return None, _drop(
            DropReason.invalid_price,
            f"low>high {parsed['low']}>{parsed['high']}",
            provider,
        )
    non_positive = sorted(n for n, v in parsed.items() if v <= 0)
    if non_positive:
        return None, _drop(DropReason.negative_price, f"price <= 0 {non_positive}", provider)
    absurd = sorted(n for n, v in parsed.items() if v > MAX_PRICE)
    if absurd:
        return None, _drop(DropReason.invalid_price, f"price > {MAX_PRICE} {absurd}", provider)
    return parsed, None


def _parse_candle_dict(
    row: Any, provider: str
) -> tuple[ParsedBar | None, Drop | None]:
    """One object from a `candles` history response."""
    if not isinstance(row, dict):
        return None, _drop(DropReason.missing_field, f"candle row is {type(row).__name__}", provider)
    prices, drop = _prices(row, provider)
    if drop is not None:
        return None, drop
    assert prices is not None
    # A history row's bucket key is `epoch`; `open_time` is not part of that payload.
    open_time = _to_epoch(row.get("epoch", row.get("open_time")))
    if open_time is None:
        return None, _drop(DropReason.invalid_timestamp, "candle missing epoch", provider)
    granularity = row.get("granularity")
    return (
        ParsedBar(
            open_time=open_time,
            open=prices["open"],
            high=prices["high"],
            low=prices["low"],
            close=prices["close"],
            epoch=_to_epoch(row.get("epoch")),
            granularity=granularity if isinstance(granularity, int) else None,
            symbol=row.get("symbol") if isinstance(row.get("symbol"), str) else None,
        ),
        None,
    )


def parse_ohlc_frame(raw: str | bytes, provider: str = "deriv") -> ParsedBar | Drop:
    """Parse ONE venue bar out of a frame. Pure: no buffer, no clock, no I/O.

    Accepts both observed wire shapes:
      * live  `{"msg_type":"ohlc", "open_time":…, "open":"83654.088", …}`  (STRINGS)
      * hist  `{"candles":[{"epoch":…, "open":83654.088, …}]}`                (NUMBERS)

    A history response with several bars yields its FIRST, which is the bar the caller
    asked for by anchoring the request window on it. The clip itself belongs to the caller,
    which owns the requested window; the parser is not allowed to guess it.
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            return _drop(DropReason.invalid_json, f"undecodable bytes: {exc}", provider)
    if not isinstance(raw, str):
        return _drop(DropReason.invalid_json, f"frame is {type(raw).__name__}", provider)
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
        return _drop(DropReason.invalid_json, f"malformed JSON: {exc}", provider)
    if not isinstance(msg, dict):
        return _drop(DropReason.missing_field, f"frame is {type(msg).__name__}", provider)

    candles = msg.get("candles")
    if isinstance(candles, list):
        if not candles:
            return _drop(DropReason.missing_field, "empty candles list", provider)
        return _first(_parse_candle_dict(candles[0], provider))

    body = msg.get("ohlc", msg)
    if not isinstance(body, dict):
        return _drop(DropReason.missing_field, f"ohlc is {type(body).__name__}", provider)

    granularity = body.get("granularity")
    if isinstance(granularity, int) and granularity != GRANULARITY_S:
        # A 5m bar in a 1m table is a silent corruption nobody notices for months.
        return _drop(
            DropReason.invalid_timestamp,
            f"granularity {granularity} != {GRANULARITY_S}",
            provider,
        )

    for field_name in ("open_time",):
        if field_name not in body:
            return _drop(DropReason.missing_field, f"ohlc missing {field_name}", provider)

    open_time = _to_epoch(body.get("open_time"))
    if open_time is None:
        return _drop(DropReason.invalid_timestamp, "ohlc open_time unusable", provider)

    prices, drop = _prices(body, provider)
    if drop is not None:
        return drop
    assert prices is not None

    return ParsedBar(
        open_time=open_time,
        open=prices["open"],
        high=prices["high"],
        low=prices["low"],
        close=prices["close"],
        epoch=_to_epoch(body.get("epoch")),
        granularity=granularity if isinstance(granularity, int) else None,
        symbol=body.get("symbol") if isinstance(body.get("symbol"), str) else None,
    )


def _first(pair: tuple[ParsedBar | None, Drop | None]) -> ParsedBar | Drop:
    parsed, drop = pair
    if drop is not None:
        return drop
    assert parsed is not None
    return parsed


@dataclass(slots=True)
class _Open:
    """The single forming minute. Exactly one exists; that is the collapse rule."""

    bar: ParsedBar
    spreads: list[Decimal | None] = field(default_factory=list)
    invalid_spread: bool = False


class BrokerCandleIngestor:
    """Bucket venue bars by `open_time` and emit each finished minute exactly once.

    Construct one per `(instrument_id, broker)`. It holds a single open minute, two counters
    and nothing else — there is no store to consult, which is precisely what makes the
    finality rule implementable.
    """

    def __init__(self, instrument_id: str, broker: str = "deriv") -> None:
        self._instrument_id = instrument_id
        self._broker = broker
        self._open: _Open | None = None
        self._emitted_starts: set[int] = set()
        self.late_frame_count = 0
        self.drop_count = 0

    # -- spread ---------------------------------------------------------------
    def on_spread_sample(
        self, bid: Decimal | None, ask: Decimal | None, at: datetime
    ) -> None:
        """Feed ONE tick's bid/ask into the currently-open minute.

        WHY the venue's candles cannot supply this: the `candles` payload is
        `additionalProperties:false` with only `close/epoch/high/low/open` — there is no
        bid and no ask anywhere in it, so `ask - bid` is not derivable from a broker bar.
        Spread therefore comes from the live TICK feed, which does carry both sides
        (`InternalTick.bid` / `.ask`, both required). Neither source fabricates anything.

        A sample that arrives for a minute that is not currently open is IGNORED rather than
        opening a bucket: spread is an attribute of a bar, and inventing a bar to hold it
        would be exactly the fabrication this module exists to remove.
        """
        if self._open is None:
            return
        minute = int(at.timestamp()) // 60 * 60
        if minute != self._open.bar.open_time:
            return
        if bid is None or ask is None:
            # All-or-none: record the hole, do not average around it.
            self._open.invalid_spread = True
            self._open.spreads.append(None)
            return
        if ask < bid:
            # Crossed quote. Under ask - bid this is a NEGATIVE spread, i.e. the thing that
            # must never be stored, so the sample is marked invalid rather than kept.
            self._open.invalid_spread = True
            self._open.spreads.append(None)
            return
        self._open.spreads.append(ask - bid)

    # -- frame handling -------------------------------------------------------
    def on_frame(self, raw: str | bytes, *, received_at: datetime) -> Candle | Drop | None:
        """Consume one frame.

        Returns the FINISHED previous minute when this frame crosses into a later one, a
        `Drop` for bad data, or `None` when the frame was buffered, ignored, or counted.
        """
        parsed = parse_ohlc_frame(raw, self._broker)
        if isinstance(parsed, Drop):
            self.drop_count += 1
            return parsed

        if parsed.open_time > MAX_OPEN_TIME:
            self.drop_count += 1
            return _drop(
                DropReason.invalid_timestamp,
                f"open_time {parsed.open_time} out of range",
                self._broker,
            )
        if parsed.open_time <= 0:
            # epoch 0 is minute-aligned and in the past, so the range and alignment checks
            # both wave it through — but it means 1970-01-01, and a bar from the epoch is
            # never a bar. Dropped explicitly rather than stored as a 56-year-old minute.
            self.drop_count += 1
            return _drop(
                DropReason.invalid_timestamp,
                f"open_time {parsed.open_time} is not a real instant",
                self._broker,
            )
        if parsed.open_time % GRANULARITY_S:
            self.drop_count += 1
            return _drop(
                DropReason.invalid_timestamp,
                f"open_time {parsed.open_time} not minute-aligned",
                self._broker,
            )
        if int(received_at.timestamp()) + FUTURE_TOLERANCE_S < parsed.open_time:
            self.drop_count += 1
            return _drop(
                DropReason.future_timestamp,
                f"open_time {parsed.open_time} ahead of received_at",
                self._broker,
            )

        current = self._open
        # WHY the emitted check comes FIRST: a frame for an already-final minute is LATE
        # regardless of what is currently buffered, and that is the more specific truth.
        # Checked second, a re-sent old minute would be reported as an out-of-order frame
        # against the newer open minute, and the late-frame counter would read ~0 forever
        # while real late frames went uncounted.
        if parsed.open_time in self._emitted_starts:
            # Already final. Accepting it would claim a correction that ON CONFLICT
            # DO NOTHING would silently discard.
            self.late_frame_count += 1
            return None

        if current is not None and parsed.open_time < current.bar.open_time:
            # Out of order, and not already emitted. The live bucket is untouched: a late
            # frame must never evict it or open a second bucket behind it.
            self.drop_count += 1
            return _drop(
                DropReason.invalid_timestamp,
                f"open_time {parsed.open_time} older than open {current.bar.open_time}",
                self._broker,
            )

        if current is None or parsed.open_time > current.bar.open_time:
            finished = self._finalize(current) if current is not None else None
            self._open = _Open(bar=parsed)
            return finished

        # Same minute, still forming: the venue re-sends it ~60x. Replace the OHLC with the
        # newer values, keep the spread samples already gathered for this minute.
        current.bar = parsed
        return None

    def close(self) -> Candle | None:
        """Finalise the buffered minute. Nothing buffered -> None, not an error."""
        if self._open is None:
            return None
        return self._finalize(self._open)

    # -- internals ------------------------------------------------------------
    def _finalize(self, state: _Open) -> Candle | None:
        self._open = None
        bar = state.bar
        self._emitted_starts.add(bar.open_time)
        start = datetime.fromtimestamp(bar.open_time, tz=UTC)

        average = tightest = widest = None
        if state.spreads and not state.invalid_spread:
            valid = [s for s in state.spreads if s is not None]
            if len(valid) == len(state.spreads) and valid:
                total = sum(valid, Decimal(0))
                average = float(total / len(valid))
                tightest = float(min(valid))
                widest = float(max(valid))

        return Candle(
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            average_spread=average,
            min_spread=tightest,
            max_spread=widest,
            interval="1m",
            interval_start=start,
            interval_end=start + timedelta(seconds=GRANULARITY_S),
            # BROKER, not COMPLETE: COMPLETE once meant "our accumulation found no hole"
            # and was PROVEN wrong (a bar with tick_count=0 still got labelled COMPLETE).
            # The value now states WHERE the OHLC came from.
            quality_status="BROKER",
            aggregation_version="deriv_broker_1m",
            expected_duration_ms=GRANULARITY_S * 1000,
        )

    @property
    def instrument_id(self) -> str:
        return self._instrument_id
