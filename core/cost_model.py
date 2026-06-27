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


def one_way_cost(costs: CostInputs, notional: Decimal) -> float:
    """Return the per-share one-way (single-leg) price hurdle in price units.

    Half-spread + one fee + amortized gas + model error margin. Mirrors
    round_trip_cost for the open-only leg the live micro-trade trades; the gas
    term matches round_trip_cost (gas_usd is the per-trade estimate), so
    one_way_cost <= round_trip_cost always holds.
    """
    if notional <= 0:
        raise ValueError("notional must be positive")
    spread = float(costs.spread) / 2.0
    fee = float(costs.fee_rate)
    gas = float(costs.gas_usd) / float(notional)
    margin = float(costs.model_error_margin)
    return spread + fee + gas + margin
