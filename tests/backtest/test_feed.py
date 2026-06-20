"""Event feed: chronology validation; out-of-order injection is rejected."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from backtest.feed import LookAheadError, load_events
from data.events import MarketEvent, Quote, event_from_quote


def _event(minute: int) -> MarketEvent:
    quote = Quote(
        market_id="m1",
        ts=datetime(2024, 6, 1, 0, minute, tzinfo=UTC),
        price=Decimal("0.50"),
    )
    return event_from_quote(quote)


def test_load_events_accepts_chronological() -> None:
    events = load_events([_event(1), _event(2), _event(2), _event(3)])
    assert [e.ts.minute for e in events] == [1, 2, 2, 3]


def test_load_events_rejects_future_injection() -> None:
    with pytest.raises(LookAheadError):
        load_events([_event(1), _event(5), _event(2)])
