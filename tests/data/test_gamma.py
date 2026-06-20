"""Gamma adapter: pure parsers (fixtures) + async client over MockTransport."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from data.events import Market, Quote
from data.gamma import GammaClient, parse_market, parse_price_history
from data.payloads import GammaMarket, GammaPriceHistory

_FIX = Path(__file__).parent.parent / "fixtures" / "gamma"


def test_parse_market_maps_fields() -> None:
    raw = GammaMarket.model_validate(json.loads((_FIX / "markets.json").read_text())[0])
    market = parse_market(raw)
    assert isinstance(market, Market)
    assert market.market_id == "m0"
    assert market.token_ids == ("111", "222")
    assert market.tick_size == Decimal("0.01")
    assert market.active is True


def test_parse_market_inactive_when_closed() -> None:
    raw = GammaMarket(
        id="x", question="q", clobTokenIds=["1"], active=True, closed=True
    )
    assert parse_market(raw).active is False


def test_parse_price_history_builds_quotes() -> None:
    raw = GammaPriceHistory.model_validate(
        json.loads((_FIX / "prices_history.json").read_text())
    )
    quotes = parse_price_history("m0", raw)
    assert [q.price for q in quotes] == [
        Decimal("0.45"),
        Decimal("0.47"),
        Decimal("0.52"),
    ]
    assert quotes[0].ts == datetime.fromtimestamp(1718800000, tz=UTC)
    assert all(isinstance(q, Quote) for q in quotes)


def test_fetch_markets_paginates() -> None:
    async def _run() -> None:
        all_markets = json.loads((_FIX / "markets.json").read_text())

        def handler(request: httpx.Request) -> httpx.Response:
            offset = int(request.url.params["offset"])
            limit = int(request.url.params["limit"])
            return httpx.Response(200, json=all_markets[offset : offset + limit])

        transport = httpx.MockTransport(handler)
        async with GammaClient(
            transport=transport, base_url="http://t", retry_backoff=0.0
        ) as client:
            markets = await client.fetch_markets(limit=2)
        assert [m.market_id for m in markets] == ["m0", "m1", "m2"]

    asyncio.run(_run())


def test_fetch_price_history_returns_quotes() -> None:
    async def _run() -> None:
        payload = json.loads((_FIX / "prices_history.json").read_text())

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["market"] == "m0"
            return httpx.Response(200, json=payload)

        transport = httpx.MockTransport(handler)
        async with GammaClient(
            transport=transport, base_url="http://t", retry_backoff=0.0
        ) as client:
            quotes = await client.fetch_price_history("m0")
        assert len(quotes) == 3
        assert quotes[-1].price == Decimal("0.52")

    asyncio.run(_run())
