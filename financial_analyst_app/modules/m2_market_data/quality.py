"""
M2.6 — quality — exactly-once dedup + quality classifier (pure, no watermark).
WHY: M2.1 frame-dedups; M2.3 bounces hard quality; this is the second line on
canonical MarketData (P12): identity first (`source_event_id + instrument_id`,
else fingerprint), then fail-safe DataQuality (never clear gapped, never promote
suspect). Never allocates watermark — M2.2 WatermarkRegistry owns allocation; M2.7 observes gaps only (FROZEN).

Spec: docs/specs/m2.6_data_quality.spec.md (LGTM + 4 minors 2026-09-23).
Consumes: shared.contracts.{market_data,drop} only — no clock/M2b/registry.
"""
from __future__ import annotations

from decimal import Decimal

from shared.contracts.drop import Drop, DropReason
from shared.contracts.market_data import DataQuality, MarketData, Tick

_DEFAULT_SPIKE = Decimal("0.05")


class QualityGate:
    """Stateful M2.6 gate — one instance per pipeline (caller owns lifetime).

    seen: identity keys (or optional shared dedup_store inject).
    prev_map: last accepted tick facts per (instrument_id, broker) for local spike.
    """

    def __init__(
        self,
        *,
        spike_ratio: Decimal = _DEFAULT_SPIKE,
        dedup_store: set | None = None,
        max_watermark_span: int = 1_000_000,
    ) -> None:
        # WHY fail-loud: absurd ratio would silently disable or flag everything.
        if not (Decimal(0) < spike_ratio < Decimal(1)):
            raise ValueError(f"spike_ratio must be in (0, 1), got {spike_ratio}")
        if isinstance(max_watermark_span, bool) or not isinstance(max_watermark_span, int):
            raise ValueError(  # noqa: TRY004 — one bad-input class for the whole gate
                f"max_watermark_span must be int, got {type(max_watermark_span).__name__}"
            )
        if max_watermark_span < 1:
            raise ValueError(f"max_watermark_span must be >= 1, got {max_watermark_span}")
        self._spike_ratio = spike_ratio
        self._seen: set = dedup_store if dedup_store is not None else set()
        self._prev_map: dict[tuple[str, str], tuple[float, float | None, float | None]] = {}
        # WHY watermark tracking lives HERE (corrected 2026-09-27): the frozen MarketData
        # contract documents `watermark` as "M2 processing_sequence per instrument_id+broker
        # - gap detection", yet nothing read it. Gap status was therefore taken solely from
        # the broker's own "gapped" marker, so a dropped socket or a lost tick produced a
        # candle stamped COMPLETE with minutes missing inside it, and M3 computed straight
        # across the hole. A gap is a QUALITY fact, so it is detected where quality is
        # decided — not by the candle builder, which cannot distinguish "no ticks" from
        # "no trading" and must stay a pure function of the stream it is handed.
        self._wm_map: dict[tuple[str, str], int] = {}
        self._max_watermark_span = max_watermark_span
        self.watermark_gaps: list[tuple[str, str, int, int]] = []

    def _apply_watermark_gap(self, md: MarketData) -> MarketData:
        """Downgrade quality to `gapped` when this tick's watermark jumps forward.

        WHY never promote: a healthy tick arriving after a hole must stay gapped so the
        candle that spans the hole is stamped GAP_AFFECTED and M3 skips it. AGENTS.md 3.3
        requires the safe assumption, and a silently repaired bar is exactly the failure
        this whole mechanism exists to prevent.
        """
        wm = md.watermark
        if isinstance(wm, bool) or not isinstance(wm, int) or wm < 1:
            return md
        key = (md.instrument_id or md.symbol, md.broker or md.provider or "")
        last = self._wm_map.get(key)
        if last is not None and wm > last + 1:
            span = wm - last - 1
            if span <= self._max_watermark_span:
                self.watermark_gaps.append((key[0], key[1], last + 1, wm - 1))
                if md.quality not in (DataQuality.gapped,):
                    return md.model_copy(update={"quality": DataQuality.gapped})
        if last is None or wm > last:
            self._wm_map[key] = wm
        return md

    def on_md(self, md: MarketData) -> MarketData | Drop:
        # Order: defensive payload/timestamp → dedup identity → watermark-gap → classifier → prev update.
        if md.kind != "tick" or not isinstance(md.payload, Tick):
            return self._drop(md, DropReason.missing_field, "non-tick payload")
        ts = md.exchange_timestamp
        if ts is None or ts.tzinfo is None:
            return self._drop(md, DropReason.invalid_timestamp, "naive/None exchange_timestamp")

        provider = md.provider or md.broker  # same fallback as Drop.provider (minor 3)
        key = self._dedup_key(md, provider)
        if key is not None:
            if key in self._seen:
                # Identity before quality — duplicate wins over any rewrite.
                return self._drop(md, DropReason.duplicate_tick, "duplicate identity")
            self._seen.add(key)

        md = self._apply_watermark_gap(md)
        out = self._classify(md)
        tick = out.payload if isinstance(out, MarketData) else md.payload
        assert isinstance(tick, Tick)
        broker = md.broker or md.provider  # minor 4 — no false spike across brokers
        instrument = md.instrument_id or md.symbol
        self._prev_map[(instrument, broker or "")] = (tick.price, tick.bid, tick.ask)
        return out

    def _dedup_key(self, md: MarketData, provider: str) -> tuple | None:
        # Empty sei is not a unique key → fingerprint path (spec Adversarial).
        #
        # CORRECTED 2026-09-27: the source_event_id branch omitted the timestamp. On Deriv
        # `tick.id` is the SUBSCRIPTION id, constant for the life of a stream, so every
        # tick after the first produced the same key and was dropped as a duplicate — near
        # total data loss. This stayed invisible only because the gate was never called by
        # the pipeline; wiring it in exposed it on the first run. M2.1's own ingest dedup
        # already pairs them as (source_event_id, epoch) — this now matches.
        ts = md.exchange_timestamp
        if md.source_event_id:
            if ts is None:
                return None  # no instant to pair with the id → cannot judge identity
            return ("sei", md.instrument_id or md.symbol, md.source_event_id, ts.isoformat())
        tick = md.payload
        assert isinstance(tick, Tick)
        if ts is None:
            return None  # defensive: no KeyError → classifier still ran via caller
        return (
            "fp",
            md.instrument_id or md.symbol,
            provider,
            ts.isoformat(),
            Decimal(str(tick.price)),
            None if tick.bid is None else Decimal(str(tick.bid)),
            None if tick.ask is None else Decimal(str(tick.ask)),
        )

    def _classify(self, md: MarketData) -> MarketData | Drop:
        q = md.quality
        # 1–3: pass-through fail-safe — never clear gapped, never promote suspect.
        if q in (
            DataQuality.gapped,
            DataQuality.suspect,
            DataQuality.degraded,
            DataQuality.delayed,
        ):
            return md

        # 4: local spike only if currently pristine and we have a prev for this series.
        tick = md.payload
        assert isinstance(tick, Tick)
        broker = md.broker or md.provider
        instrument = md.instrument_id or md.symbol
        prev = self._prev_map.get((instrument, broker or ""))
        if prev is not None:
            prev_price, prev_bid, prev_ask = prev
            if prev_bid is not None and prev_ask is not None:
                prev_mid = (prev_bid + prev_ask) / 2.0
            else:
                prev_mid = prev_price
            if tick.bid is not None and tick.ask is not None:
                mid = (tick.bid + tick.ask) / 2.0
            else:
                mid = tick.price
            # Spec: Δprice on Tick.price; denominator prev_mid; skip if prev_mid == 0.
            if prev_mid > 0:
                delta = abs(tick.price - prev_price)
                ratio = Decimal(str(delta)) / Decimal(str(prev_mid))
                if ratio > self._spike_ratio:
                    return md.model_copy(update={"quality": DataQuality.suspect})
            # mid unused when prev uses price-only path — still defined for clarity
            _ = mid

        # 5: else pristine (already is)
        return md

    @staticmethod
    def _drop(md: MarketData, reason: DropReason, detail: str) -> Drop:
        return Drop(
            reason=reason,
            provider=md.provider or md.broker or "",
            detail=detail,
            raw_payload="",
        )
