from decimal import Decimal

from core.models import Side
from execution.client import Allowances, FakeExecutionClient, OrderStatus
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


def test_order_status_fill_fields_default_to_zero() -> None:
    status = OrderStatus(order_id="x", state="open")
    assert status.filled_size == Decimal(0)
    assert status.avg_fill_price == Decimal(0)
    assert status.fees_paid == Decimal(0)


def test_fake_client_status_returns_scripted_sequence() -> None:
    seq = [
        OrderStatus(order_id="o", state="open"),
        OrderStatus(
            order_id="o",
            state="matched",
            filled_size=Decimal("5"),
            avg_fill_price=Decimal("0.51"),
            fees_paid=Decimal("0.02"),
        ),
    ]
    client = FakeExecutionClient(status_sequence=seq)
    assert client.status("o").state == "open"
    second = client.status("o")
    assert second.state == "matched"
    assert second.filled_size == Decimal("5")
    # exhausted sequence repeats the last entry
    assert client.status("o").state == "matched"


def test_fake_client_status_defaults_to_open_without_sequence() -> None:
    assert FakeExecutionClient().status("o").state == "open"
