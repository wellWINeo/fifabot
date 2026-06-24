from decimal import Decimal

from core.models import Side
from scripts.probe_amoy_order import build_probe_orders


def test_build_probe_orders_makes_a_buy_and_counter() -> None:
    buy, counter = build_probe_orders(
        token_id="yes", price=Decimal("0.50"), size=Decimal("5")
    )
    assert buy.token_id == "yes" and buy.side is Side.BUY_YES
    assert buy.price == Decimal("0.50") and buy.size == Decimal("5")
    assert counter.side is Side.BUY_NO
    assert counter.price == Decimal("0.50")
    assert buy.signature_type == 0 and counter.signature_type == 0
