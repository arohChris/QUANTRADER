"""
M2.9 HOT Cache — transient latest-tick + forming-candle store (Valkey via redis-py).

WHY this module exists:
  The live pipeline needs low-latency access to "right now" market state
  (latest MarketData, per-timeframe forming candle) without touching parquet.
  Canonical (A33): M2.4 produces canonical market-state events; the DURABLE
  finalized 1-minute candle store (WARM/Timescale) is the canonical persisted
  historical dataset. M2.9 is only a transient acceleration layer.

LAW (spec docs/specs/m2.9_hot.spec.md + human rules 2026-09-25):
  - Cache is NEVER the truth: every I/O failure fails open (sentinel + warning).
    cache_available != market_state_valid — ping() reports cache health only;
    Valkey must never become a hidden dependency for trading.
  - Monotonic (A33): NEVER replace newer cached state with older — set_latest
    gates on watermark (equal -> overwrite, idempotent), set_forming gates on
    interval_start (older bucket rejected). Naive/aware temporal mismatch =
    INVALID temporal state -> REJECT write + warning, NEVER treated as
    permission to overwrite (self-heals at TTL expiry).
  - HARD ARCHITECTURAL INVARIANT (M2.1/M2.9) [A33 r2 — law, not a note]:
    For each provider+instrument HOT key, writes are serialized through one
    ordered writer. No independent worker may concurrently perform
    set_latest/set_forming for the same key. If this invariant is removed or
    violated, M2.9 monotonicity MUST be implemented with an atomic server-side
    CAS/Lua operation BEFORE multi-writer operation is enabled.
  - Keys are GLOBAL market-data (no user_id, §4.3): provider+instrument only.
  - Forming candle = mutable state (overwrites expected); finalized candles are
    immutable history — they live in WARM/Timescale, never here.
  - Staleness: we pass through timestamps/watermark; age policy = consumer's call.
  - Validation (caller bug) raises ValueError OUTSIDE the I/O try-block; I/O never raises.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from shared.contracts.market_data import Candle, MarketData

logger = logging.getLogger("m2.9.hot")

# Key parts: no spaces/colons/slashes/newlines — kills key-injection ("a b", "x:y", "; DROP")
_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Timeframes: 1m 5m 15m 40m 1h 4h 24h 1d (Candle.interval vocabulary)
_TF_RE = re.compile(r"^[0-9]{1,3}(m|h|d)$")
_TTL_MIN, _TTL_MAX = 1, 86400


def _check_part(value: str, what: str) -> str:
    if not isinstance(value, str) or not _PART_RE.match(value):
        raise ValueError(f"invalid {what} for HOT key: {value!r}")
    return value


def _check_timeframe(timeframe: str) -> str:
    if not isinstance(timeframe, str) or not _TF_RE.match(timeframe):
        raise ValueError(f"invalid timeframe for HOT key: {timeframe!r}")
    return timeframe


def resolve_source_namespace(md: MarketData) -> str:
    """ONE canonical write-path rule: provider if present, otherwise broker.

    WHY single function: set_latest and get_latest must never disagree on the
    namespace — every key flows through resolve_source_namespace + key builders.
    """
    ns = md.provider if md.provider else md.broker
    return _check_part(ns, "provider")


def latest_key(provider: str, instrument_id: str, *, namespace: str = "hot") -> str:
    """ONLY place latest keys are formed (writes AND reads call this)."""
    _check_part(namespace, "namespace")
    return f"{_check_part(namespace, 'namespace')}:{_check_part(provider, 'provider')}:{_check_part(instrument_id, 'instrument_id')}:latest"


def forming_key(provider: str, instrument_id: str, timeframe: str, *, namespace: str = "hot") -> str:
    """ONLY place forming keys are formed (writes AND reads call this)."""
    _check_part(namespace, "namespace")
    return (
        f"{_check_part(namespace, 'namespace')}:{_check_part(provider, 'provider')}:"
        f"{_check_part(instrument_id, 'instrument_id')}:{_check_timeframe(timeframe)}:forming"
    )


class HotStore:
    """Async HOT-tier cache. Inject `client` (tests) or call `connect()` for redis-py."""

    def __init__(
        self,
        url: str,
        *,
        client: Any = None,
        default_ttl_seconds: int = 60,
        namespace: str = "hot",
    ) -> None:
        # §3.13 sane bounds — refuse absurd config at construction, not at runtime
        if not isinstance(default_ttl_seconds, int) or not (_TTL_MIN <= default_ttl_seconds <= _TTL_MAX):
            raise ValueError(
                f"default_ttl_seconds out of bounds {_TTL_MIN}..{_TTL_MAX}: {default_ttl_seconds!r}"
            )
        _check_part(namespace, "namespace")
        self._url = url
        self._client = client
        self._ttl = default_ttl_seconds
        self._ns = namespace

    async def connect(self) -> None:
        """Create the real redis-py client lazily — hermetic tests never import redis."""
        if self._client is None:
            from redis import asyncio as redis  # lazy import: Step-5 dep, tests inject fakes

            self._client = redis.from_url(self._url, decode_responses=True)

    # ---- writes: validation FIRST (raises), then fail-open I/O ---------------------

    async def set_latest(self, md: MarketData) -> bool:
        if not isinstance(md, MarketData):
            # spec-locked typed error (A32 tests assert ValueError, not TypeError)
            raise ValueError(f"expect MarketData, got {type(md).__name__}")  # noqa: TRY004
        if not md.instrument_id:
            raise ValueError(f"set_latest requires md.instrument_id for key building: {md!r}")
        key = latest_key(resolve_source_namespace(md), md.instrument_id, namespace=self._ns)
        # Monotonic law A33: stale watermark never overwrites newer state
        existing = await self._get(key)
        if existing is not None:
            prev = self._decode_latest(key, existing)  # corrupt -> None -> replaceable
            if prev is not None and md.watermark < prev.watermark:
                logger.info(
                    "HOT stale latest rejected %s: wm %s < stored %s",
                    key, md.watermark, prev.watermark,
                )
                return False
        return await self._set(key, md.model_dump_json())

    async def set_forming(self, provider: str, instrument_id: str, timeframe: str, candle: Candle) -> bool:
        if not isinstance(candle, Candle):
            # spec-locked typed error (A32 tests): ValueError, not TypeError
            raise ValueError(f"expect Candle, got {type(candle).__name__}")  # noqa: TRY004
        key = forming_key(provider, instrument_id, timeframe, namespace=self._ns)
        # Monotonic law A33 (symmetric): older bucket never clobbers current forming
        existing = await self._get(key)
        if existing is not None:
            prev = self._decode_candle(key, existing)
            if prev is not None:
                try:
                    stale = candle.interval_start < prev.interval_start
                except TypeError:
                    # A33 r2: naive/aware mismatch = INVALID temporal state — NOT
                    # permission to overwrite. Reject write, key untouched
                    # (self-heals when the malformed key falls off at TTL expiry).
                    logger.warning(
                        "HOT forming temporal mismatch %s: incoming=%r stored=%r — write rejected",
                        key, candle.interval_start, prev.interval_start,
                    )
                    return False
                if stale:
                    logger.info(
                        "HOT stale forming rejected %s: %s < stored %s",
                        key, candle.interval_start, prev.interval_start,
                    )
                    return False
        return await self._set(key, candle.model_dump_json())

    # ---- reads: fail-open sentinel, corrupt payload -> None ------------------------

    async def get_latest(self, provider: str, instrument_id: str) -> MarketData | None:
        key = latest_key(provider, instrument_id, namespace=self._ns)  # validates before I/O
        raw = await self._get(key)
        if raw is None:
            return None
        return self._decode_latest(key, raw)

    async def get_forming(self, provider: str, instrument_id: str, timeframe: str) -> Candle | None:
        key = forming_key(provider, instrument_id, timeframe, namespace=self._ns)
        raw = await self._get(key)
        if raw is None:
            return None
        return self._decode_candle(key, raw)

    # ---- decode: corrupt stored value -> None (cache never truth; set-gates reuse) --

    def _decode_latest(self, key: str, raw: str) -> MarketData | None:
        try:
            return MarketData.model_validate_json(raw)
        except Exception as exc:  # noqa: BLE001 — corrupt / wrong-type stored value
            logger.warning("HOT corrupt latest %s (fail-open -> None): %s", key, exc)
            return None

    def _decode_candle(self, key: str, raw: str) -> Candle | None:
        try:
            return Candle.model_validate_json(raw)
        except Exception as exc:  # noqa: BLE001 — corrupt payload fails open to None
            logger.warning("HOT corrupt forming %s (fail-open -> None): %s", key, exc)
            return None

    # ---- health: cache availability ONLY (never market-state validity) -------------

    async def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(await self._client.ping())
        except Exception as exc:  # noqa: BLE001 — cache health is best-effort, never raises
            logger.warning("HOT ping failed (fail-open -> False): %s", exc)
            return False

    async def close(self) -> None:
        if self._client is None:
            return
        try:
            aclose_fn: Any = getattr(self._client, "aclose", None)
            if aclose_fn is not None:
                await aclose_fn()
        except Exception as exc:  # noqa: BLE001 — close is best-effort, idempotent, never raises
            logger.warning("HOT close failed (ignored): %s", exc)
        return

    # ---- private I/O (everything here fails open) ----------------------------------

    async def _set(self, key: str, payload: str) -> bool:
        if self._client is None:
            logger.warning("HOT set skipped, no client (fail-open): %s", key)
            return False
        try:
            await self._client.set(key, payload, ex=self._ttl)
            return True
        except Exception as exc:  # noqa: BLE001 — cache is never the truth: swallow ALL I/O errors
            logger.warning("HOT set failed (fail-open, cache never truth) %s: %s", key, exc)
            return False

    async def _get(self, key: str) -> str | None:
        if self._client is None:
            logger.warning("HOT get skipped, no client (fail-open): %s", key)
            return None
        try:
            raw = await self._client.get(key)
            if isinstance(raw, bytes):  # decode_responses=True yields str; defensive only
                raw = raw.decode("utf-8")
            return raw if raw is None or isinstance(raw, str) else None  # unexpected type -> missing
        except Exception as exc:  # noqa: BLE001 — fail open: WARM/pipeline is authoritative
            logger.warning("HOT get failed (fail-open -> None) %s: %s", key, exc)
            return None
