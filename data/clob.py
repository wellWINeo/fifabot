"""CLOB adapter: pure parsers + async REST client + WS subscription client.

Auth fields come from env only; the secret is never sent as a header here —
full L2 request signing is Phase 5. Public market-data reads need no auth.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

import httpx
import websockets

from data.events import Quote
from data.http import RateLimiter, get_json
from data.payloads import ClobBook, ClobPriceHistory

CLOB_BASE_URL = "https://clob.polymarket.com"
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def parse_book(raw: ClobBook) -> Quote:
    if not raw.asks or not raw.bids:
        raise ValueError("book is missing bid/ask levels")
    if raw.timestamp is None:
        raise ValueError("book is missing a timestamp")
    best_ask = min(raw.asks, key=lambda level: level.price)
    best_bid = max(raw.bids, key=lambda level: level.price)
    mid = (best_ask.price + best_bid.price) / 2
    return Quote(
        market_id=raw.market,
        ts=datetime.fromtimestamp(raw.timestamp / 1000, tz=UTC),
        price=mid,
        bid=best_bid.price,
        ask=best_ask.price,
        size=best_ask.size,
    )


def parse_price_history(token_id: str, raw: ClobPriceHistory) -> list[Quote]:
    return [
        Quote(
            market_id=token_id,
            ts=datetime.fromtimestamp(point.t, tz=UTC),
            price=point.p,
        )
        for point in raw.history
    ]


def parse_ws_message(msg: Mapping[str, Any]) -> Quote | None:
    if msg.get("event_type") != "book":
        return None
    return parse_book(ClobBook.model_validate(msg))


@dataclass(frozen=True)
class ClobAuth:
    api_key: str
    secret: str
    passphrase: str

    @classmethod
    def from_env(cls) -> ClobAuth | None:
        key = os.environ.get("CLOB_API_KEY")
        secret = os.environ.get("CLOB_API_SECRET")
        passphrase = os.environ.get("CLOB_API_PASSPHRASE")
        if key and secret and passphrase:
            return cls(api_key=key, secret=secret, passphrase=passphrase)
        return None


def _auth_headers(auth: ClobAuth) -> dict[str, str]:
    return {"POLY_API_KEY": auth.api_key, "POLY_PASSPHRASE": auth.passphrase}


class ClobClient:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = CLOB_BASE_URL,
        auth: ClobAuth | None = None,
        limiter: RateLimiter | None = None,
        max_retries: int = 3,
        retry_backoff: float = 0.5,
    ) -> None:
        headers = _auth_headers(auth) if auth is not None else {}
        self._client = httpx.AsyncClient(
            base_url=base_url, transport=transport, headers=headers
        )
        self._limiter = limiter or RateLimiter()
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_book(self, token_id: str) -> Quote:
        raw = await get_json(
            self._client,
            "/book",
            {"token_id": token_id},
            limiter=self._limiter,
            max_retries=self._max_retries,
            retry_backoff=self._retry_backoff,
        )
        return parse_book(ClobBook.model_validate(raw))

    async def fetch_price_history(self, token_id: str) -> list[Quote]:
        raw = await get_json(
            self._client,
            "/prices-history",
            {"market": token_id},
            limiter=self._limiter,
            max_retries=self._max_retries,
            retry_backoff=self._retry_backoff,
        )
        return parse_price_history(token_id, ClobPriceHistory.model_validate(raw))


class ClobWsClient:
    def __init__(
        self,
        *,
        ws_connect: Callable[..., Any] | None = None,
        url: str = CLOB_WS_URL,
    ) -> None:
        self._connect: Callable[..., Any] = ws_connect or websockets.connect
        self._url = url

    async def stream(self, market_ids: Sequence[str]) -> AsyncIterator[Quote]:
        async with self._connect(self._url) as ws:
            await ws.send(
                json.dumps({"type": "subscribe", "markets": list(market_ids)})
            )
            async for raw in ws:
                quote = parse_ws_message(json.loads(raw))
                if quote is not None:
                    yield quote
