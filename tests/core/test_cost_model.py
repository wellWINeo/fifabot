"""Cost model: composition, non-negativity, monotonicity."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.cost_model import round_trip_cost
from core.models import CostInputs


def test_round_trip_cost_components() -> None:
    costs = CostInputs(
        spread=Decimal("0.02"),
        fee_rate=Decimal("0.01"),
        gas_usd=Decimal("1.00"),
        model_error_margin=Decimal("0.005"),
    )
    # 0.02 + 2*0.01 + 1.00/10 + 0.005 = 0.145
    assert round_trip_cost(costs, Decimal("10")) == pytest.approx(0.145)


def test_round_trip_cost_rejects_nonpositive_notional() -> None:
    costs = CostInputs(
        spread=Decimal("0.01"),
        fee_rate=Decimal("0"),
        gas_usd=Decimal("0"),
        model_error_margin=Decimal("0"),
    )
    with pytest.raises(ValueError):
        round_trip_cost(costs, Decimal("0"))


_money = st.decimals(
    min_value=Decimal("0"), max_value=Decimal("5"), places=4, allow_nan=False
)
_pos_notional = st.decimals(
    min_value=Decimal("0.01"), max_value=Decimal("1000"), places=2, allow_nan=False
)


@given(_money, _money, _money, _money, _pos_notional)
def test_round_trip_cost_non_negative(
    spread: Decimal,
    fee_rate: Decimal,
    gas: Decimal,
    margin: Decimal,
    notional: Decimal,
) -> None:
    costs = CostInputs(
        spread=spread, fee_rate=fee_rate, gas_usd=gas, model_error_margin=margin
    )
    assert round_trip_cost(costs, notional) >= 0.0


@given(_money, _money, _money, _money, _money, _pos_notional)
def test_round_trip_cost_monotone_in_spread(
    spread: Decimal,
    bump: Decimal,
    fee_rate: Decimal,
    gas: Decimal,
    margin: Decimal,
    notional: Decimal,
) -> None:
    base = CostInputs(
        spread=spread, fee_rate=fee_rate, gas_usd=gas, model_error_margin=margin
    )
    higher = CostInputs(
        spread=spread + bump,
        fee_rate=fee_rate,
        gas_usd=gas,
        model_error_margin=margin,
    )
    assert round_trip_cost(higher, notional) >= round_trip_cost(base, notional)


@given(_money, _money, _money, _money, _money, _pos_notional)
def test_round_trip_cost_monotone_in_fee_rate(
    fee_rate: Decimal,
    bump: Decimal,
    spread: Decimal,
    gas: Decimal,
    margin: Decimal,
    notional: Decimal,
) -> None:
    base = CostInputs(
        spread=spread, fee_rate=fee_rate, gas_usd=gas, model_error_margin=margin
    )
    higher = CostInputs(
        spread=spread,
        fee_rate=fee_rate + bump,
        gas_usd=gas,
        model_error_margin=margin,
    )
    assert round_trip_cost(higher, notional) >= round_trip_cost(base, notional)


@given(_money, _money, _money, _money, _money, _pos_notional)
def test_round_trip_cost_monotone_in_gas(
    gas: Decimal,
    bump: Decimal,
    spread: Decimal,
    fee_rate: Decimal,
    margin: Decimal,
    notional: Decimal,
) -> None:
    base = CostInputs(
        spread=spread, fee_rate=fee_rate, gas_usd=gas, model_error_margin=margin
    )
    higher = CostInputs(
        spread=spread,
        fee_rate=fee_rate,
        gas_usd=gas + bump,
        model_error_margin=margin,
    )
    assert round_trip_cost(higher, notional) >= round_trip_cost(base, notional)


@given(_money, _money, _money, _money, _money, _pos_notional)
def test_round_trip_cost_monotone_in_margin(
    margin: Decimal,
    bump: Decimal,
    spread: Decimal,
    fee_rate: Decimal,
    gas: Decimal,
    notional: Decimal,
) -> None:
    base = CostInputs(
        spread=spread, fee_rate=fee_rate, gas_usd=gas, model_error_margin=margin
    )
    higher = CostInputs(
        spread=spread,
        fee_rate=fee_rate,
        gas_usd=gas,
        model_error_margin=margin + bump,
    )
    assert round_trip_cost(higher, notional) >= round_trip_cost(base, notional)
