from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.models import CostInputs, Side
from execution.client import OrderStatus
from execution.orders import OrderRequest
from execution.reconcile import reconcile


def _order(price: str = "0.50", size: str = "10") -> OrderRequest:
    return OrderRequest(
        market_id="m",
        token_id="t",
        side=Side.BUY_YES,
        price=Decimal(price),
        size=Decimal(size),
    )


def _costs() -> CostInputs:
    return CostInputs(
        spread=Decimal("0.02"),
        fee_rate=Decimal("0.01"),
        gas_usd=Decimal(0),
        model_error_margin=Decimal(0),
    )


def test_full_fill_with_adverse_slippage() -> None:
    order = _order(price="0.50", size="10")
    status = OrderStatus(
        order_id="o",
        state="matched",
        filled_size=Decimal("10"),
        avg_fill_price=Decimal("0.52"),
        fees_paid=Decimal("0.05"),
    )
    report = reconcile(order, status, _costs())
    assert report.fill_ratio == Decimal(1)
    assert report.slippage == Decimal("0.02")
    # actual = fees + slippage*filled = 0.05 + 0.02*10
    assert report.actual_cost == pytest.approx(0.25)


def test_zero_fill_reports_zero_slippage_and_fees_only_cost() -> None:
    order = _order()
    status = OrderStatus(order_id="o", state="cancelled")
    report = reconcile(order, status, _costs())
    assert report.fill_ratio == Decimal(0)
    assert report.slippage == Decimal(0)
    assert report.avg_fill_price == Decimal(0)
    assert report.actual_cost == pytest.approx(0.0)


def test_partial_fill_ratio_between_zero_and_one() -> None:
    order = _order(price="0.50", size="10")
    status = OrderStatus(
        order_id="o",
        state="matched",
        filled_size=Decimal("4"),
        avg_fill_price=Decimal("0.50"),
        fees_paid=Decimal("0.01"),
    )
    report = reconcile(order, status, _costs())
    assert report.fill_ratio == Decimal("0.4")


def test_cost_delta_is_in_total_usd_not_per_share_rate() -> None:
    order = _order(price="0.50", size="10")
    status = OrderStatus(
        order_id="o",
        state="matched",
        filled_size=Decimal("10"),
        avg_fill_price=Decimal("0.52"),
        fees_paid=Decimal("0.05"),
    )
    report = reconcile(order, status, _costs())
    # expected = one_way_cost_rate * size = (0.01 + 0.01) * 10 = 0.20 USD
    # actual   = fees + slippage * filled = 0.05 + 0.02 * 10  = 0.25 USD
    assert report.expected_cost == pytest.approx(0.20)
    assert report.actual_cost == pytest.approx(0.25)
    assert report.cost_delta == pytest.approx(0.05)


@given(
    filled=st.decimals(min_value=0, max_value=1000, places=2),
    size=st.decimals(min_value=1, max_value=1000, places=2),
)
def test_fill_ratio_always_within_unit_interval(filled: Decimal, size: Decimal) -> None:
    order = OrderRequest(
        market_id="m",
        token_id="t",
        side=Side.BUY_YES,
        price=Decimal("0.50"),
        size=size,
    )
    status = OrderStatus(
        order_id="o",
        state="matched",
        filled_size=filled,
        avg_fill_price=Decimal("0.50"),
        fees_paid=Decimal(0),
    )
    report = reconcile(order, status, _costs())
    assert Decimal(0) <= report.fill_ratio <= Decimal(1)
