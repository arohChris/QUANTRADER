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
  - Forming candle = mutable state (overwrites expected); finalized candles live
    in WARM/Timescale — EXCEPT the bounded E1 ring below [human A52 + D1
    "Keep 4h as decided", spec REV2 PATCH]: 240 x 1m sliding FIFO, TTL-bounded,
    evicted bar handed to WARM. Ring is rebuildable cache, never truth.
  - Staleness: we pass through timestamps/watermark; age policy = consumer's call.
  - Validation (caller bug) raises ValueError OUTSIDE the I/O try-block; I/O never raises.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from shared.contracts.market_data import Candle, MarketData

logger = logging.getLogger("m2.9.hot")

# Key parts: no spaces/colons/slashes/newlines — kills key-injection ("a b", "x:y", "; DROP")
_PART_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Timeframes: 1m 5m 15m 40m 1h 4h 24h 1d (Candle.interval vocabulary)
_TF_RE = re.compile(r"^[0-9]{1,3}(m|h|d)$")
_TTL_MIN, _TTL_MAX = 1, 86400

# ---- Extension E1 [human A52 2026-09-26 / D1 "Keep 4h as decided"] ---------------
RING_CAPACITY = 240  # 4h x 1m — sliding FIFO; newest bar evicts oldest; never grows past this
RING_TTL_SECONDS = 86400  # within _TTL bounds; refreshed on every append; idle self-clean 1d
RING_TIMEFRAME = "1m"  # D1: ONE 1m ring; higher TFs fold from it (fold work = later slice)


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


def ring_key(provider: str, instrument_id: str, *, namespace: str = "hot") -> str:
    """ONLY place ring keys are formed (writes AND reads call this — no drift)."""
    _check_part(namespace, "namespace")
    return (
        f"{_check_part(namespace, 'namespace')}:{_check_part(provider, 'provider')}:"
        f"{_check_part(instrument_id, 'instrument_id')}:ring:{RING_TIMEFRAME}"
    )


@dataclass(frozen=True)
class RingPush:
    """E1 in-module result (not shared/contracts — first external consumer freezes it).

    WHY status+evicted together: the producer needs BOTH "did the append happen" and
    "which bar fell out" in one call — the evicted bar is the D1 FIFO handoff to WARM.
    """

    status: str  # appended | evicted | duplicate | stale_rejected | failed
    evicted: Candle | None = None  # oldest bar popped by capacity overflow (-> WARM)


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

    # ---- Extension E1: 1m rolling ring (240 bars) — spec m2.9 §Extension E1 --------

    async def ring_push(self, provider: str, instrument_id: str, candle: Candle) -> RingPush:
        if not isinstance(candle, Candle):
            # spec-locked typed error — validation NOT swallowed by fail-open
            raise ValueError(f"expect Candle, got {type(candle).__name__}")  # noqa: TRY004
        key = ring_key(provider, instrument_id, namespace=self._ns)  # validates parts first
        # A33 monotonic on TAIL (append-ordered list: tail = newest ring state)
        tail_raw = await self._lrange(key, -1, -1)
        if tail_raw:
            prev = self._decode_ring(key, tail_raw[0])
            if prev is not None:
                try:
                    if candle.interval_start < prev.interval_start:
                        logger.info(
                            "HOT stale ring push rejected %s: %s < %s",
                            key, candle.interval_start, prev.interval_start,
                        )
                        return RingPush("stale_rejected")
                    if candle.interval_start == prev.interval_start:
                        return RingPush("duplicate")  # rerun-safe: no double-append/evict
                except TypeError:
                    # A33 r2: naive/aware mismatch = INVALID state, never write permission
                    logger.warning(
                        "HOT ring temporal mismatch %s: incoming=%r stored=%r — push rejected",
                        key, candle.interval_start, prev.interval_start,
                    )
                    return RingPush("stale_rejected")
        if not await self._rpush(key, candle.model_dump_json()):
            return RingPush("failed")
        await self._expire(key, RING_TTL_SECONDS)
        # Sliding FIFO (D1): overflow -> pop oldest -> return it (producer hands to WARM)
        size = await self._llen(key)
        if size is not None and size > RING_CAPACITY:
            popped = await self._lpop(key)
            if popped is not None:
                return RingPush("evicted", self._decode_ring(key, popped))
            logger.warning("HOT ring pop failed %s (over cap %s; self-heals next push)", key, size)
        return RingPush("appended")

    async def ring_read(
        self, provider: str, instrument_id: str, *, start: datetime, end: datetime
    ) -> list[Candle]:
        # Validation BEFORE I/O: caller bug (tz/window) is loud, never fail-open
        for where, value in (("start", start), ("end", end)):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError(f"ring_read {where} must be tz-aware UTC, got {value!r}")
        if start > end:
            raise ValueError(f"ring_read window inverted: start {start!r} > end {end!r}")
        key = ring_key(provider, instrument_id, namespace=self._ns)
        raws = await self._lrange(key, 0, -1)
        out: list[Candle] = []
        for raw in raws:
            candle = self._decode_ring(key, raw)
            if candle is None:
                continue  # corrupt element skipped — valid ones survive (cache never truth)
            try:
                if start <= candle.interval_start <= end:
                    out.append(candle)
            except TypeError:
                logger.warning("HOT ring temporal mismatch %s (element skipped)", key)
        # Ascending push order; window verbatim inclusive (A46) — NEVER auto-expanded
        return out

    async def ring_len(self, provider: str, instrument_id: str) -> int:
        key = ring_key(provider, instrument_id, namespace=self._ns)
        size = await self._llen(key)
        return size if size is not None else 0  # fail-open sentinel (ops/verify only)

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

    def _decode_ring(self, key: str, raw: str) -> Candle | None:
        try:
            return Candle.model_validate_json(raw)
        except Exception as exc:  # noqa: BLE001 — corrupt element skipped, list survives
            logger.warning("HOT corrupt ring element %s (fail-open -> skip): %s", key, exc)
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

    # ---- ring private I/O (everything here fails open) ----------------------------

    async def _rpush(self, key: str, payload: str) -> bool:
        if self._client is None:
            logger.warning("HOT ring push skipped, no client (fail-open): %s", key)
            return False
        try:
            await self._client.rpush(key, payload)
            return True
        except Exception as exc:  # noqa: BLE001 — cache is never the truth
            logger.warning("HOT ring push failed (fail-open) %s: %s", key, exc)
            return False

    async def _lrange(self, key: str, start: int, end: int) -> list[str]:
        if self._client is None:
            logger.warning("HOT ring read skipped, no client (fail-open): %s", key)
            return []
        try:
            raws = await self._client.lrange(key, start, end)
        except Exception as exc:  # noqa: BLE001 — fail open: WARM is authoritative
            logger.warning("HOT ring read failed (fail-open -> []) %s: %s", key, exc)
            return []
        out: list[str] = []
        for item in raws:
            if isinstance(item, bytes):  # decode_responses=True yields str; defensive only
                item = item.decode("utf-8")
            if isinstance(item, str):
                out.append(item)  # unexpected element type dropped, never crash a read
        return out

    async def _llen(self, key: str) -> int | None:
        """`None` on any failure — callers distinguish "unknown" from a real 0."""
        if self._client is None:
            logger.warning("HOT ring len skipped, no client (fail-open): %s", key)
            return None
        try:
            return int(await self._client.llen(key))
        except Exception as exc:  # noqa: BLE001
            logger.warning("HOT ring len failed (fail-open) %s: %s", key, exc)
            return None

    async def _lpop(self, key: str) -> str | None:
        if self._client is None:
            return None
        try:
            item = await self._client.lpop(key)
            if isinstance(item, bytes):
                item = item.decode("utf-8")
            return item if item is None or isinstance(item, str) else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("HOT ring pop failed (fail-open) %s: %s", key, exc)
            return None

    async def _expire(self, key: str, ttl: int) -> None:
        if self._client is None:
            return
        try:
            await self._client.expire(key, ttl)
        except Exception as exc:  # noqa: BLE001 — missing TTL self-bounded by LLEN cap anyway
            logger.warning("HOT ring expire failed (fail-open) %s: %s", key, exc)
        return
