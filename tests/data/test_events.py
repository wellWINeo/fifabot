"""Canonical records: tz-aware timestamps, price bounds, event helper."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from data.events import (
    DiscoveredMarket,
    DiscoveryManifest,
    Market,
    MarketGroup,
    Quote,
    event_from_quote,
)


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


def test_market_group_construction() -> None:
    group = MarketGroup(group_id="30615", market_ids=("558934", "558935"))
    assert group.market_ids == ("558934", "558935")
    assert group.kind == "negrisk"


def test_market_group_requires_two_legs() -> None:
    with pytest.raises(ValidationError):
        MarketGroup(group_id="g", market_ids=("only-one",))


def test_market_has_minimum_order_size() -> None:
    market = Market(
        market_id="m",
        question="q",
        token_ids=("yes", "no"),
        tick_size=Decimal("0.01"),
        minimum_order_size=Decimal("5"),
    )
    assert market.minimum_order_size == Decimal("5")


def test_market_rejects_nonpositive_minimum_order_size() -> None:
    with pytest.raises(ValidationError):
        Market(
            market_id="m",
            question="q",
            token_ids=("yes", "no"),
            tick_size=Decimal("0.01"),
            minimum_order_size=Decimal("0"),
        )


def test_discovered_market_construction() -> None:
    dm = DiscoveredMarket(
        market_id="558934",
        question="Will Spain win the 2026 FIFA World Cup?",
        token_ids=("4394", "1126"),
        event_slug="world-cup-winner",
        kind="outright",
        group_id="30615",
    )
    assert dm.group_id == "30615"
    assert dm.kind == "outright"


def test_discovered_market_group_id_optional() -> None:
    dm = DiscoveredMarket(
        market_id="x", question="q", token_ids=("a",), event_slug="s", kind="prop"
    )
    assert dm.group_id is None


def test_discovered_market_is_frozen() -> None:
    dm = DiscoveredMarket(
        market_id="x", question="q", token_ids=("a",), event_slug="s", kind="prop"
    )
    with pytest.raises(ValidationError):
        dm.kind = "other"  # type: ignore[misc]


def test_discovery_manifest_holds_markets_and_groups() -> None:
    dm = DiscoveredMarket(
        market_id="x", question="q", token_ids=("a",), event_slug="s", kind="prop"
    )
    manifest = DiscoveryManifest(
        topic="fifa-2026",
        tag="world-cup",
        discovered_at=datetime(2026, 6, 27, 12, 0, tzinfo=UTC),
        markets=(dm,),
        groups=(),
    )
    assert manifest.markets[0].market_id == "x"
    assert manifest.tag == "world-cup"
