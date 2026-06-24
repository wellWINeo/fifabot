from decimal import Decimal

import pytest

from core.models import Side
from data.events import Market
from execution.orders import OrderRequest, OrderValidationError, validate_order


def _market() -> Market:
    return Market(
        market_id="m",
        question="q",
        token_ids=("yes", "no"),
        tick_size=Decimal("0.01"),
        minimum_order_size=Decimal("5"),
    )


def _order(price: str = "0.40", size: str = "10") -> OrderRequest:
    return OrderRequest(
        market_id="m",
        token_id="yes",
        side=Side.BUY_YES,
        price=Decimal(price),
        size=Decimal(size),
    )


def test_notional_is_price_times_size() -> None:
    assert _order("0.40", "10").notional() == Decimal("4.0")


def test_signature_type_defaults_to_zero() -> None:
    assert _order().signature_type == 0


def test_validate_accepts_on_tick_above_min() -> None:
    validate_order(_order("0.40", "10"), _market())  # no raise


def test_validate_rejects_off_tick_price() -> None:
    with pytest.raises(OrderValidationError, match="tick"):
        validate_order(_order("0.405", "10"), _market())


def test_validate_rejects_below_min_size() -> None:
    with pytest.raises(OrderValidationError, match="minimum"):
        validate_order(_order("0.40", "4"), _market())
