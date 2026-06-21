"""MarketView: an immutable, time-bounded view of market history.

The strategy only ever receives a MarketView, never the raw feed. Any query for a
timestamp after as_of raises LookAheadError, so the future is unreachable by
construction. The engine guarantees quotes_by_market holds only quotes with
ts <= as_of when it builds the view.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from backtest.feed import LookAheadError
from data.events import Quote
from data.reference import ReferencePrice


class MarketView:
    def __init__(
        self,
        as_of: datetime,
        quotes_by_market: Mapping[str, list[Quote]],
        reference: ReferencePrice | None = None,
    ) -> None:
        self._as_of = as_of
        self._quotes = quotes_by_market
        self._reference = reference

    @property
    def as_of(self) -> datetime:
        return self._as_of

    def _guard(self, ts: datetime) -> None:
        if ts > self._as_of:
            raise LookAheadError(f"query ts {ts} is after as_of {self._as_of}")

    def latest_price(self, market_id: str) -> Decimal | None:
        return self.price_at(market_id, self._as_of)

    def price_at(self, market_id: str, ts: datetime) -> Decimal | None:
        self._guard(ts)
        result: Decimal | None = None
        for quote in self._quotes.get(market_id, []):
            if quote.ts <= ts:
                result = quote.price
            else:
                break
        return result

    def reference_at(self, market_id: str, ts: datetime) -> Decimal | None:
        self._guard(ts)
        if self._reference is None:
            return None
        return self._reference.at(market_id, ts)
