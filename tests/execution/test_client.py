from decimal import Decimal

from core.models import Side
from execution.client import Allowances, FakeExecutionClient
from execution.orders import OrderRequest


def _order() -> OrderRequest:
    return OrderRequest(
        market_id="m",
        token_id="yes",
        side=Side.BUY_YES,
        price=Decimal("0.40"),
        size=Decimal("10"),
    )


def test_fake_records_placed_orders_and_returns_id() -> None:
    client = FakeExecutionClient()
    order_id = client.place(_order())
    assert order_id == client.next_id
    assert client.placed == [_order()]


def test_fake_reports_configured_allowances() -> None:
    client = FakeExecutionClient(usdc_allowance=Decimal("3"))
    assert client.allowances() == Allowances(usdc=Decimal("3"), ctf=Decimal("1000"))
