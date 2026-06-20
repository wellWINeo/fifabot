"""Shared HTTP helpers for the data adapters: rate limiting and bounded retry."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Self

import httpx


class RateLimiter:
    """Bounds the number of concurrent in-flight requests."""

    def __init__(self, max_concurrency: int = 8) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        self._sem = asyncio.Semaphore(max_concurrency)

    async def acquire(self) -> None:
        await self._sem.acquire()

    def release(self) -> None:
        self._sem.release()

    async def __aenter__(self) -> Self:
        await self.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.release()


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


async def get_json(
    client: httpx.AsyncClient,
    url: str,
    params: Mapping[str, Any] | None = None,
    *,
    limiter: RateLimiter,
    max_retries: int = 3,
    retry_backoff: float = 0.5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            async with limiter:
                resp = await client.get(url, params=dict(params or {}))
            resp.raise_for_status()
            return resp.json()
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if attempt < max_retries and _is_retryable(exc):
                await sleep(retry_backoff * (2**attempt))
                continue
            raise
    assert last_exc is not None  # unreachable: loop either returns or raises
    raise last_exc
