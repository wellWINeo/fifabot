"""Round-trip cost model: the per-share price hurdle an edge must clear."""

from __future__ import annotations

from decimal import Decimal

from core.models import CostInputs


def round_trip_cost(costs: CostInputs, notional: Decimal) -> float:
    """Return the per-share price hurdle in price units.

    spread (both legs) + round-trip fees (2 * fee_rate) + amortized round-trip
    gas (gas_usd / notional) + model error margin.
    """
    if notional <= 0:
        raise ValueError("notional must be positive")
    spread = float(costs.spread)
    fees = 2.0 * float(costs.fee_rate)
    gas = float(costs.gas_usd) / float(notional)
    margin = float(costs.model_error_margin)
    return spread + fees + gas + margin
