"""Canonical records: tz-aware timestamps, price bounds, event helper."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from data.events import Quote, event_from_quote


def _quote() -> Quote:
    return Quote(
        market_id="m1",
        ts=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
        price=Decimal("0.45"),
    )


def test_quote_requires_tz_aware_ts() -> None:
    with pytest.raises(ValidationError):
        Quote(market_id="m1", ts=datetime(2024, 6, 1, 12, 0), price=Decimal("0.45"))


def test_quote_rejects_out_of_range_price() -> None:
    with pytest.raises(ValidationError):
        Quote(
            market_id="m1",
            ts=datetime(2024, 6, 1, tzinfo=UTC),
            price=Decimal("1.0"),
        )


def test_event_from_quote_copies_ts_and_market() -> None:
    q = _quote()
    e = event_from_quote(q)
    assert e.ts == q.ts
    assert e.market_id == q.market_id
    assert e.quote == q


def test_models_are_frozen() -> None:
    q = _quote()
    with pytest.raises(ValidationError):
        q.price = Decimal("0.5")  # type: ignore[misc]
