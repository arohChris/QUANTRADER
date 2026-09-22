"""
M1.4 — Retry Policy — jitter + idempotency + dead-letter
INSTITUTIONAL HEDGE FUND GRADE

WHY jitter? Without jitter, 100 retries at 2**attempt hit broker at same second → 429 thundering herd.
    With jitter, retries spread over window.

WHY idempotency_key? M6 retry with same client_order_id — broker dedupes, no double order.

WHY dead-letter? After 3 fails, don't silently drop — enqueue to DLQ for human triage.
    M12 Ops monitors dead_letter queue length.

WHY async? Broker calls are I/O — must not block.

Usage:
    from shared.core.retry_policy import retry_with_backoff
    result = await retry_with_backoff(
        lambda: broker.send_order(order),
        idempotency_key=order.client_order_id,
        max_attempts=3,
    )

Pure sync alternative for non-I/O retries:
    result = retry_sync(lambda: validate(order), max_attempts=3)
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class RetryExhaustedError(RuntimeError):
    """All retries failed — will be sent to dead-letter."""


# In-memory dedup store — Day 1 in-memory, prod Redis
_seen_keys: dict[str, Any] = {}


def _is_duplicate(key: str | None) -> bool:
    return key is not None and key in _seen_keys


def _record(key: str | None, result: Any) -> None:
    if key is not None:
        _seen_keys[key] = result


async def retry_with_backoff(
    fn: Callable[[], Awaitable[Any]] | Callable[[], Any],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    jitter: bool = True,
    idempotency_key: str | None = None,
    retryable_exceptions: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError, OSError),
    dead_letter_handler: Callable[[str | None, BaseException], None] | None = None,
) -> Any:
    """
    WHY async def? So callers can `await retry_with_backoff(broker.send, ...)` without blocking.
    WHY base_delay 0.5? P95 order→ack <500ms — first retry after 0.5s, second after 1s, third after 2s.
    WHY dead_letter_handler? M6 can log to DB DLQ table, M2 recovery can log to retry queue.
    """
    # WHY idempotency check BEFORE first try? If this is a retry of already-succeeded key, return cached.
    if _is_duplicate(idempotency_key):
        logger.info("retry.idempotent_hit", extra={"idempotency_key": idempotency_key})
        return _seen_keys[idempotency_key]  # type: ignore[index]

    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            # Support both async and sync fn
            result = fn()  # type: ignore[call-arg]
            if asyncio.iscoroutine(result):
                result = await result
            _record(idempotency_key, result)
            if attempt > 1:
                logger.info("retry.succeeded", extra={"attempt": attempt, "idempotency_key": idempotency_key})
            return result
        except retryable_exceptions as exc:  # noqa: BLE001 — we re-raise after exhaust
            last_exc = exc
            if attempt == max_attempts:
                break
            # Exponential backoff: 0.5, 1.0, 2.0 + jitter 0-0.3
            delay = base_delay * (2 ** (attempt - 1))
            if jitter:
                delay += random.uniform(0, 0.3 * delay)
            logger.warning(
                "retry.attempt_failed",
                extra={"attempt": attempt, "max": max_attempts, "delay": round(delay, 3), "error": str(exc)},
            )
            await asyncio.sleep(delay)
        except Exception as exc:  # noqa: BLE001 — non-retryable, fail fast
            # WHY not retry? ValidationError, PermissionDenied — retrying won't help, fail loud
            logger.exception("retry.non_retryable", extra={"error": str(exc)})
            raise

    # Exhausted
    logger.error("retry.exhausted", extra={"idempotency_key": idempotency_key, "error": str(last_exc)})
    if dead_letter_handler and idempotency_key:
        try:
            dead_letter_handler(idempotency_key, last_exc or RuntimeError("unknown"))
        except Exception:  # noqa: BLE001
            logger.exception("dead_letter_handler failed")
    raise RetryExhaustedError(f"Retry exhausted after {max_attempts} attempts: {last_exc}") from last_exc


def retry_sync(
    fn: Callable[[], Any],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.1,
    jitter: bool = True,
    retryable_exceptions: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError),
) -> Any:
    """Sync version for pure calc retries (rare — most I/O is async)."""
    import time

    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except retryable_exceptions as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            if jitter:
                delay += random.uniform(0, 0.3 * delay)
            time.sleep(delay)
        except Exception:
            raise
    raise RetryExhaustedError(f"Retry exhausted after {max_attempts}: {last_exc}") from last_exc
