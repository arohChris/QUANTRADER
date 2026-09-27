"""
M4.6 — AlphaEngine: the per-candle loop the bible names (04_M4_Alpha.md §3
"M4 AlphaEngine loops ... registry.values()"). First REAL candles -> Signal path.
WHY this layer owns ONLY: boundary validation, quality gate, per-strategy
  isolation, emission — every business gate (capability/lifecycle/params/shadow)
  stays in m4.1's runner; duplicating any of them here would create a second,
  drifting copy of the same law.
WHY caller-driven on_candle, not a bus: no event bus exists yet (recorded
  discrepancy 3 — the future wiring slice adds it; faking one would be bloat).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from shared.contracts.market_data import Candle, MarketData
from shared.contracts.signal import Signal

logger = logging.getLogger(__name__)

# UNKNOWN quality values fall through to skip — §3.3 unknown -> safer, never run.
_RUNNABLE_QUALITY = frozenset({"COMPLETE", "LOW_ACTIVITY"})
_ENVS = frozenset({"prod", "paper"})
_ASSET_BY_PREFIX: dict[str, Literal["forex", "crypto", "commodity"]] = {
    "FX.": "forex",
    "CRYPTO.": "crypto",
    "COMMODITY.": "commodity",
}


class SignalSink(Protocol):
    """Async out-port — collecting list in tests, JSONL in verify, M4.3 later."""

    async def emit(self, signal: Signal) -> None: ...


@dataclass(frozen=True)
class StrategyFailure:
    strategy: str
    error_type: str
    message: str


@dataclass(frozen=True)
class CandleOutcome:
    signals: tuple[Signal, ...]
    failures: tuple[StrategyFailure, ...]
    skip_reason: str | None = None


class AlphaEngine:
    """Stateless across candles — concurrent on_candle calls need no lock."""

    def __init__(self, *, names: tuple[str, ...], runner: Any,
                 sink: SignalSink) -> None:
        # WHY duck-typed runner (like m4.1 FeatureSource): real StrategyRunner
        # and the tests' FakeRunner share run()'s kwargs without a second port.
        if (not isinstance(names, tuple)
                or any(not isinstance(n, str) or not n for n in names)
                or len(set(names)) != len(names)):
            raise ValueError(
                f"names must be a tuple of unique non-empty strs, got {names!r}")
        self._names = names
        self._runner = runner
        self._sink = sink

    async def on_candle(self, market: MarketData, *, env: str, now: datetime,
                        user_id: str | None = None,
                        strategy_params: dict[str, float] | None = None,
                        ) -> CandleOutcome:
        """Phase 0: validate loud BEFORE any I/O (unconditional, even names=())."""
        payload = market.payload
        if market.kind != "candle" or not isinstance(payload, Candle):
            raise ValueError(
                f"expected kind='candle' with Candle payload, got kind={market.kind!r} "
                f"payload={type(payload).__name__}")
        if not market.instrument_id:
            raise ValueError(
                f"instrument_id must be a non-empty str, got {market.instrument_id!r}")
        if not market.broker:
            raise ValueError(f"broker must be a non-empty str, got {market.broker!r}")
        _require_utc(now, "now")
        _require_utc(payload.interval_start, "interval_start")
        _require_utc(payload.interval_end, "interval_end")
        if payload.interval_end <= payload.interval_start:
            raise ValueError(
                f"interval_end must be after interval_start, got "
                f"{payload.interval_start!r}..{payload.interval_end!r}")
        if payload.interval_end > now:
            # THE no-look-ahead boundary at this layer: as_of can never be future.
            raise ValueError(
                f"formative candle refused: interval_end {payload.interval_end} "
                f"> now {now}")
        if env not in _ENVS:
            raise ValueError(f"env must be one of {sorted(_ENVS)}, got {env!r}")
        asset_class = _asset_class(market.instrument_id)

        # Phase 1: market state (not a bug) -> return with zero runner calls.
        if payload.quality_status not in _RUNNABLE_QUALITY:
            reason = f"quality={payload.quality_status}"
            logger.info("alpha skip reason=%s instrument=%s",
                        reason, market.instrument_id)
            return CandleOutcome((), (), reason)

        # Phase 2: run all names; ONE bad family never blocks the others.
        signals: list[Signal] = []
        failures: list[StrategyFailure] = []
        for name in self._names:
            try:
                sig = await self._runner.run(
                    name, market=market, instrument_id=market.instrument_id,
                    broker=market.broker, timeframe=payload.interval,
                    asset_class=asset_class, as_of=payload.interval_end,
                    now=now, env=env, user_id=user_id,
                    strategy_params=strategy_params)
            except Exception as exc:  # noqa: BLE001 — isolation is the contract (E8)
                failures.append(
                    StrategyFailure(name, type(exc).__name__, str(exc)))
                logger.warning("alpha strategy failed strategy=%s error=%s msg=%s",
                               name, type(exc).__name__, str(exc))
                continue
            if sig is not None:
                signals.append(sig)

        # Phase 3: emit AFTER all runs — sink errors propagate loud (caller owns
        # retry/idempotency; partial-emit caveat documented in spec Out).
        for sig in signals:
            await self._sink.emit(sig)
        logger.debug("alpha candle done signals=%d failures=%d",
                     len(signals), len(failures))
        return CandleOutcome(tuple(signals), tuple(failures), None)


def _asset_class(instrument_id: str) -> Literal["forex", "crypto", "commodity"]:
    """Derive from canonical id prefix — deterministic, zero config reads."""
    for prefix, cls in _ASSET_BY_PREFIX.items():
        if instrument_id.startswith(prefix):
            return cls
    raise ValueError(
        f"asset_class not derivable from instrument_id {instrument_id!r} — "
        "expected FX./CRYPTO./COMMODITY. prefix")


def _require_utc(value: datetime, field: str) -> None:
    """Naive or non-UTC rejected loud — DST bugs live here (m4.1 rule reused)."""
    if (not isinstance(value, datetime) or value.tzinfo is None
            or value.utcoffset() != timedelta(0)):
        raise ValueError(f"{field} must be tz-aware UTC, got {value!r}")
