"""Gamma REST adapter: pure parsers + a full async client (offset pagination)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Self

import httpx

from data.events import Market, Quote
from data.http import RateLimiter, get_json
from data.payloads import GammaMarket, GammaPriceHistory

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"


def parse_market(raw: GammaMarket) -> Market:
    return Market(
        market_id=raw.id,
        question=raw.question,
        token_ids=tuple(raw.clobTokenIds),
        tick_size=raw.tickSize,
        active=raw.active and not raw.closed,
    )


def parse_price_history(market_id: str, raw: GammaPriceHistory) -> list[Quote]:
    return [
        Quote(
            market_id=market_id,
            ts=datetime.fromtimestamp(point.t, tz=UTC),
            price=Decimal(str(point.p)),
        )
        for point in raw.history
    ]


class GammaClient:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = GAMMA_BASE_URL,
        limiter: RateLimiter | None = None,
        max_retries: int = 3,
        retry_backoff: float = 0.5,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url, transport=transport, headers=dict(headers or {})
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

    async def fetch_markets(self, *, limit: int = 100) -> list[Market]:
        markets: list[Market] = []
        offset = 0
        while True:
            raw = await get_json(
                self._client,
                "/markets",
                {"limit": limit, "offset": offset},
                limiter=self._limiter,
                max_retries=self._max_retries,
                retry_backoff=self._retry_backoff,
            )
            page = [parse_market(GammaMarket.model_validate(m)) for m in raw]
            markets.extend(page)
            if len(page) < limit:
                break
            offset += limit
        return markets

    async def fetch_price_history(self, market_id: str) -> list[Quote]:
        raw = await get_json(
            self._client,
            "/prices-history",
            {"market": market_id},
            limiter=self._limiter,
            max_retries=self._max_retries,
            retry_backoff=self._retry_backoff,
        )
        return parse_price_history(market_id, GammaPriceHistory.model_validate(raw))
