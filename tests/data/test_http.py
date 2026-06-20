"""Shared HTTP helpers: rate-limit bounding and bounded retry."""

import asyncio

import httpx
import pytest

from data.http import RateLimiter, get_json


def test_rate_limiter_bounds_concurrency() -> None:
    async def _run() -> None:
        rl = RateLimiter(max_concurrency=1)
        await rl.acquire()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(rl.acquire(), timeout=0.05)
        rl.release()

    asyncio.run(_run())


def test_get_json_retries_transient_then_succeeds() -> None:
    async def _run() -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ConnectError("boom", request=request)
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            base_url="http://t", transport=transport
        ) as client:
            data = await get_json(
                client, "/x", limiter=RateLimiter(2), retry_backoff=0.0
            )
        assert data == {"ok": True}
        assert calls["n"] == 3

    asyncio.run(_run())


def test_get_json_does_not_retry_4xx() -> None:
    async def _run() -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            base_url="http://t", transport=transport
        ) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await get_json(client, "/x", limiter=RateLimiter(2), retry_backoff=0.0)
        assert calls["n"] == 1

    asyncio.run(_run())
