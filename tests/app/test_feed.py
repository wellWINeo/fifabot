import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.feed import HistoricalFeed, LiveFeed
from backtest.feed import LookAheadError
from data.events import MarketEvent, Quote, event_from_quote

_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def _event(minute: int, price: str) -> MarketEvent:
    return event_from_quote(
        Quote(market_id="m", ts=_T0 + timedelta(minutes=minute), price=Decimal(price))
    )


def test_historical_feed_yields_in_order() -> None:
    async def _run() -> None:
        feed = HistoricalFeed([_event(0, "0.40"), _event(1, "0.55")])
        got = [e async for e in feed.events()]
        assert [e.quote.price for e in got] == [Decimal("0.40"), Decimal("0.55")]

    asyncio.run(_run())


def test_historical_feed_rejects_out_of_order() -> None:
    with pytest.raises(LookAheadError):
        HistoricalFeed([_event(2, "0.40"), _event(1, "0.55")])


class _FakeWsClient:
    def __init__(self, quotes: list[Quote]) -> None:
        self._quotes = quotes
        self.subscribed: list[str] = []

    async def stream(self, market_ids: list[str]) -> AsyncIterator[Quote]:
        self.subscribed = list(market_ids)
        for q in self._quotes:
            yield q


def test_live_feed_wraps_ws_quotes_into_events() -> None:
    async def _run() -> None:
        quotes = [
            Quote(market_id="m", ts=_T0, price=Decimal("0.40")),
            Quote(market_id="m", ts=_T0 + timedelta(minutes=1), price=Decimal("0.55")),
        ]
        ws = _FakeWsClient(quotes)
        feed = LiveFeed(ws, ["m"])  # type: ignore[arg-type]
        got = [e async for e in feed.events()]
        assert ws.subscribed == ["m"]
        assert all(isinstance(e, MarketEvent) for e in got)
        assert [e.quote.price for e in got] == [Decimal("0.40"), Decimal("0.55")]

    asyncio.run(_run())
