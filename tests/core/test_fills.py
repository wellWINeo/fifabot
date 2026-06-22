from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.fills import (
    MakerOrder,
    crosses,
    round_trip_fill_costs,
    simulate_maker_fill,
    token_price,
)
from core.models import CostInputs, Side
from data.events import Quote

_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def _quote(minute: int, price: str) -> Quote:
    return Quote(
        market_id="m", ts=_T0 + timedelta(minutes=minute), price=Decimal(price)
    )


def _order(side: Side, limit: str, expiry_min: int = 10) -> MakerOrder:
    return MakerOrder(
        side=side,
        limit_price=Decimal(limit),
        shares=Decimal("10"),
        placed_ts=_T0,
        expiry_ts=_T0 + timedelta(minutes=expiry_min),
    )


def test_token_price_buy_no_complements() -> None:
    assert token_price(Side.BUY_YES, Decimal("0.40")) == Decimal("0.40")
    assert token_price(Side.BUY_NO, Decimal("0.40")) == Decimal("0.60")


def test_buy_yes_fills_when_price_drops_to_limit() -> None:
    order = _order(Side.BUY_YES, "0.40")
    # later quote at 0.40 trades through our resting bid
    assert crosses(order, _quote(2, "0.40")) is True
    assert (
        simulate_maker_fill(order, [_quote(1, "0.45"), _quote(2, "0.40")])
        == _quote(2, "0.40").ts
    )


def test_buy_yes_does_not_fill_when_price_rises() -> None:
    order = _order(Side.BUY_YES, "0.40")
    assert crosses(order, _quote(2, "0.55")) is False
    assert simulate_maker_fill(order, [_quote(1, "0.50"), _quote(2, "0.55")]) is None


def test_buy_no_fills_when_yes_rises_to_limit() -> None:
    order = _order(Side.BUY_NO, "0.60")  # NO token limit = 0.40
    assert crosses(order, _quote(2, "0.60")) is True  # yes>=0.60 -> no token<=0.40
    assert crosses(order, _quote(2, "0.55")) is False


def test_quote_outside_window_never_crosses() -> None:
    order = _order(Side.BUY_YES, "0.40", expiry_min=3)
    assert crosses(order, _quote(0, "0.30")) is False  # at/before placed_ts
    assert crosses(order, _quote(5, "0.30")) is False  # after expiry


def test_round_trip_costs() -> None:
    costs = CostInputs(
        spread=Decimal("0"),
        fee_rate=Decimal("0.02"),
        gas_usd=Decimal("0.01"),
        model_error_margin=Decimal("0"),
    )
    # 0.02*(0.40+0.55)*10 + 0.01 = 0.19 + 0.01 = 0.20
    assert round_trip_fill_costs(
        costs, Decimal("0.40"), Decimal("0.55"), Decimal("10")
    ) == Decimal("0.20")


def test_maker_order_rejects_limit_price_off_tick() -> None:
    with pytest.raises(ValueError, match="tick"):
        MakerOrder(
            side=Side.BUY_YES,
            limit_price=Decimal("0.405"),
            shares=Decimal("10"),
            placed_ts=_T0,
            expiry_ts=_T0 + timedelta(minutes=10),
        )


def test_maker_order_accepts_custom_tick_size() -> None:
    order = MakerOrder(
        side=Side.BUY_YES,
        limit_price=Decimal("0.405"),
        shares=Decimal("10"),
        placed_ts=_T0,
        expiry_ts=_T0 + timedelta(minutes=10),
        tick_size=Decimal("0.005"),
    )
    assert order.limit_price == Decimal("0.405")


@given(
    limit=st.decimals(min_value="0.02", max_value="0.98", places=2),
    drop=st.decimals(min_value="0.00", max_value="0.50", places=2),
)
def test_buy_yes_fills_iff_quote_reaches_limit(limit: Decimal, drop: Decimal) -> None:
    order = _order(Side.BUY_YES, str(limit))
    quote_price = max(Decimal("0.01"), min(Decimal("0.99"), limit - drop))
    quote = Quote(market_id="m", ts=_T0 + timedelta(minutes=1), price=quote_price)
    assert crosses(order, quote) == (quote_price <= limit)
