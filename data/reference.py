"""Reference-price interface + a fixture-backed replay implementation.

The replay honors the same as-of discipline as the harness: it never returns a
quote with a timestamp after the requested ts. A live Betfair adapter is deferred
to the phase whose signal first consumes it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from data.events import Quote


class ReferencePrice(Protocol):
    def at(self, market_id: str, ts: datetime) -> Decimal | None: ...


class ReplayReference:
    def __init__(self, quotes: Sequence[Quote]) -> None:
        self._by_market: dict[str, list[Quote]] = {}
        for quote in sorted(quotes, key=lambda q: q.ts):
            self._by_market.setdefault(quote.market_id, []).append(quote)

    def at(self, market_id: str, ts: datetime) -> Decimal | None:
        result: Decimal | None = None
        for quote in self._by_market.get(market_id, []):
            if quote.ts <= ts:
                result = quote.price
            else:
                break
        return result
