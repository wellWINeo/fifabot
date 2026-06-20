"""MarketView: as-of queries; any future query raises (structural guard)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backtest.feed import LookAheadError
from backtest.view import MarketView
from data.events import Quote
from data.reference import ReplayReference


def _q(minute: int, price: str) -> Quote:
    return Quote(
        market_id="m1",
        ts=datetime(2024, 6, 1, 0, minute, tzinfo=UTC),
        price=Decimal(price),
    )


def _view(as_of_minute: int) -> MarketView:
    as_of = datetime(2024, 6, 1, 0, as_of_minute, tzinfo=UTC)
    quotes = {"m1": [_q(1, "0.40"), _q(3, "0.44")]}
    ref = ReplayReference([_q(1, "0.60"), _q(3, "0.62")])
    return MarketView(as_of=as_of, quotes_by_market=quotes, reference=ref)


def test_latest_price() -> None:
    assert _view(3).latest_price("m1") == Decimal("0.44")


def test_price_at_returns_as_of_or_before() -> None:
    view = _view(3)
    ts = datetime(2024, 6, 1, 0, 2, tzinfo=UTC)
    assert view.price_at("m1", ts) == Decimal("0.40")


def test_price_at_future_raises() -> None:
    view = _view(3)
    future = view.as_of + timedelta(minutes=1)
    with pytest.raises(LookAheadError):
        view.price_at("m1", future)


def test_reference_at_future_raises() -> None:
    view = _view(3)
    future = view.as_of + timedelta(minutes=1)
    with pytest.raises(LookAheadError):
        view.reference_at("m1", future)


def test_reference_at_returns_value() -> None:
    view = _view(3)
    assert view.reference_at("m1", view.as_of) == Decimal("0.62")
