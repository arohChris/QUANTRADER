"""
M1.4 — Retry Policy Tests — jitter, idempotency, dead-letter
"""
from __future__ import annotations

import pytest

from shared.core.retry_policy import RetryExhaustedError, retry_with_backoff


@pytest.mark.asyncio
async def test_retry_succeeds_first_try() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = await retry_with_backoff(fn, max_attempts=3)
    assert result == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_retry_retries_then_succeeds() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("flaky")
        return "ok"

    result = await retry_with_backoff(fn, max_attempts=3, base_delay=0.01)
    assert result == "ok"
    assert calls == 3


@pytest.mark.asyncio
async def test_retry_exhausted() -> None:
    async def fn() -> str:
        raise ConnectionError("always down")

    with pytest.raises(RetryExhaustedError):
        await retry_with_backoff(fn, max_attempts=2, base_delay=0.01)


@pytest.mark.asyncio
async def test_retry_non_retryable_fails_fast() -> None:
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        raise ValueError("not retryable")

    with pytest.raises(ValueError):
        await retry_with_backoff(fn, max_attempts=3, base_delay=0.01)
    assert calls == 1  # no retry


@pytest.mark.asyncio
async def test_retry_idempotency() -> None:
    # WHY test? client_order_id dedup — retry same key returns cached, not duplicate broker call
    calls = 0

    async def fn() -> str:
        nonlocal calls
        calls += 1
        return "filled"

    key = "client-order-123"
    r1 = await retry_with_backoff(fn, max_attempts=3, idempotency_key=key, base_delay=0.01)
    assert r1 == "filled"
    assert calls == 1

    # Second call with same key — should return cached without calling fn again
    r2 = await retry_with_backoff(fn, max_attempts=3, idempotency_key=key, base_delay=0.01)
    assert r2 == "filled"
    assert calls == 1  # not incremented

    # Cleanup for other tests
    from shared.core.retry_policy import _seen_keys

    _seen_keys.pop(key, None)
