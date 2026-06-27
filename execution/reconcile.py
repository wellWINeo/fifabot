"""Post-trade reconciliation: intended order vs actual fill.

Pure measurement, not a trade decision, so it lives at the execution edge but
imports the core cost model for the expected one-way hurdle. SELL/round-trip
reconciliation is deferred with the live loop.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from core.cost_model import one_way_cost
from core.models import CostInputs
from execution.client import OrderStatus
from execution.orders import OrderRequest


class ReconciliationReport(BaseModel):
    """All cost fields (expected_cost, actual_cost, cost_delta) are total USD,
    not per-share rates."""

    model_config = ConfigDict(frozen=True)

    intended_price: Decimal
    intended_size: Decimal
    filled_size: Decimal
    avg_fill_price: Decimal
    fill_ratio: Decimal
    slippage: Decimal
    fees_paid: Decimal
    expected_cost: float  # total USD: one_way_cost_rate * intended_size
    actual_cost: float  # total USD: fees_paid + slippage * filled_size
    cost_delta: float  # actual_cost - expected_cost (positive = worse than expected)


def reconcile(
    order: OrderRequest, status: OrderStatus, costs: CostInputs
) -> ReconciliationReport:
    expected_cost = one_way_cost(costs, order.notional()) * float(order.size)
    filled = status.filled_size
    if filled <= 0:
        actual_cost = float(status.fees_paid)
        return ReconciliationReport(
            intended_price=order.price,
            intended_size=order.size,
            filled_size=Decimal(0),
            avg_fill_price=Decimal(0),
            fill_ratio=Decimal(0),
            slippage=Decimal(0),
            fees_paid=status.fees_paid,
            expected_cost=expected_cost,
            actual_cost=actual_cost,
            cost_delta=actual_cost - expected_cost,
        )
    fill_ratio = min(filled / order.size, Decimal(1))
    slippage = status.avg_fill_price - order.price
    actual_cost = float(status.fees_paid) + float(slippage) * float(filled)
    return ReconciliationReport(
        intended_price=order.price,
        intended_size=order.size,
        filled_size=filled,
        avg_fill_price=status.avg_fill_price,
        fill_ratio=fill_ratio,
        slippage=slippage,
        fees_paid=status.fees_paid,
        expected_cost=expected_cost,
        actual_cost=actual_cost,
        cost_delta=actual_cost - expected_cost,
    )
