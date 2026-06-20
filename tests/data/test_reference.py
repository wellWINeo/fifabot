"""Reference-price replay: as-of semantics, never returns a future quote."""

from datetime import UTC, datetime
from decimal import Decimal

from data.events import Quote
from data.reference import ReplayReference


def _q(minute: int, price: str) -> Quote:
    return Quote(
        market_id="m1",
        ts=datetime(2024, 6, 1, 0, minute, tzinfo=UTC),
        price=Decimal(price),
    )


def test_returns_latest_at_or_before_ts() -> None:
    ref = ReplayReference([_q(1, "0.40"), _q(3, "0.44"), _q(5, "0.48")])
    assert ref.at("m1", datetime(2024, 6, 1, 0, 4, tzinfo=UTC)) == Decimal("0.44")


def test_returns_exact_match() -> None:
    ref = ReplayReference([_q(1, "0.40"), _q(3, "0.44")])
    assert ref.at("m1", datetime(2024, 6, 1, 0, 3, tzinfo=UTC)) == Decimal("0.44")


def test_none_before_first_quote() -> None:
    ref = ReplayReference([_q(3, "0.44")])
    assert ref.at("m1", datetime(2024, 6, 1, 0, 1, tzinfo=UTC)) is None


def test_none_for_unknown_market() -> None:
    ref = ReplayReference([_q(1, "0.40")])
    assert ref.at("other", datetime(2024, 6, 1, 0, 5, tzinfo=UTC)) is None
