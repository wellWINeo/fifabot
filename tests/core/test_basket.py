from decimal import Decimal

from core.basket import basket_cost, basket_decide
from core.cost_model import round_trip_cost
from core.models import CostInputs


def _costs(margin: str = "0.02") -> CostInputs:
    return CostInputs(
        spread=Decimal("0.01"),
        fee_rate=Decimal("0"),
        gas_usd=Decimal("0"),
        model_error_margin=Decimal(margin),
    )


def test_basket_cost_excludes_model_error_margin() -> None:
    costs = _costs("0.02")
    notional = Decimal("10")
    assert basket_cost(costs, notional) < round_trip_cost(costs, notional)
    # equals the per-leg hurdle only when there is no margin
    assert basket_cost(_costs("0"), notional) == round_trip_cost(_costs("0"), notional)


def test_long_set_when_sum_below_one() -> None:
    # legs sum to 0.94; hurdle (spread only) = 0.01 -> 0.94 < 0.99 -> long the set
    result = basket_decide([0.30, 0.30, 0.34], _costs("0"), Decimal("10"))
    assert result.action == "long_set"
    assert abs(result.edge - 0.06) < 1e-9


def test_short_set_when_sum_above_one() -> None:
    result = basket_decide([0.40, 0.40, 0.30], _costs("0"), Decimal("10"))
    assert result.action == "short_set"
    assert abs(result.edge - 0.10) < 1e-9


def test_abstains_when_within_real_costs() -> None:
    # sum 1.005, hurdle 0.01 -> deviation 0.005 < 0.01 -> abstain
    result = basket_decide([0.335, 0.335, 0.335], _costs("0"), Decimal("10"))
    assert result.action == "abstain"


def test_basket_acts_where_per_leg_margin_would_abstain() -> None:
    # deviation 0.015; spread-only basket hurdle 0.01 -> acts.
    # a per-leg gate adding a 0.02 margin (hurdle 0.03) would abstain.
    result = basket_decide([0.33, 0.33, 0.325], _costs("0.02"), Decimal("10"))
    assert result.action == "long_set"
