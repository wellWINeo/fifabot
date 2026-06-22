"""The feed seam: one timestamped MarketEvent source for the orchestrator.

HistoricalFeed replays recorded events (deterministic, offline); LiveFeed wraps
the CLOB websocket stream. Both present the same async interface so the
orchestrator runs one code path over either origin.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from backtest.feed import load_events
from data.clob import ClobWsClient
from data.events import MarketEvent, event_from_quote


class Feed(Protocol):
    def events(self) -> AsyncIterator[MarketEvent]: ...


class HistoricalFeed:
    def __init__(self, events: Sequence[MarketEvent]) -> None:
        self._events = load_events(events)

    async def events(self) -> AsyncIterator[MarketEvent]:
        for event in self._events:
            yield event


class LiveFeed:
    def __init__(self, ws: ClobWsClient, market_ids: Sequence[str]) -> None:
        self._ws = ws
        self._market_ids = list(market_ids)

    async def events(self) -> AsyncIterator[MarketEvent]:
        async for quote in self._ws.stream(self._market_ids):
            yield event_from_quote(quote)
