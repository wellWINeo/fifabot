"""Pure S2 basket gate: near-riskless cross-market arbitrage on a group.

For a mutually-exclusive group, `overround = sum(yes_prices)`. `overround < 1`
=> long the complete set (locked profit `1 - overround` at resolution);
`overround > 1` => short the set. Unlike the per-leg edge gate, the hurdle
excludes `model_error_margin` — an accounting identity does not pay a
probabilistic-error margin. Pure and unwired: no venue consumes this yet
(atomic multi-leg execution is deferred).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from core.cost_model import round_trip_cost
from core.models import CostInputs
from core.signals.devig import overround


class BasketDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: Literal["long_set", "short_set", "abstain"]
    edge: float


def basket_cost(costs: CostInputs, notional: Decimal) -> float:
    riskless = costs.model_copy(update={"model_error_margin": Decimal(0)})
    return round_trip_cost(riskless, notional)


def basket_decide(
    yes_prices: Sequence[float], costs: CostInputs, notional: Decimal
) -> BasketDecision:
    total = overround(yes_prices)
    hurdle = basket_cost(costs, notional)
    if total < 1.0 - hurdle:
        return BasketDecision(action="long_set", edge=1.0 - total)
    if total > 1.0 + hurdle:
        return BasketDecision(action="short_set", edge=total - 1.0)
    return BasketDecision(action="abstain", edge=0.0)
