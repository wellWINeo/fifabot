"""Maker-first fill model: a resting limit fills only if a later quote crosses it.

Pure. Shared by the backtest engine and the paper orchestrator. The forward
window is forward simulation of an order's life, not look-ahead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from core.models import DEFAULT_TICK, CostInputs, Side


class PriceTick(Protocol):
    """Structural shape of a timestamped quote -- avoids importing data.events."""

    @property
    def ts(self) -> datetime: ...

    @property
    def price(self) -> Decimal: ...


@dataclass(frozen=True)
class MakerOrder:
    side: Side
    limit_price: Decimal
    shares: Decimal
    placed_ts: datetime
    expiry_ts: datetime
    tick_size: Decimal = DEFAULT_TICK

    def __post_init__(self) -> None:
        if self.limit_price % self.tick_size != 0:
            raise ValueError(
                f"limit_price {self.limit_price} is not a multiple of "
                f"tick {self.tick_size}"
            )


def token_price(side: Side, yes_price: Decimal) -> Decimal:
    return yes_price if side is Side.BUY_YES else Decimal(1) - yes_price


def crosses(order: MakerOrder, quote: PriceTick) -> bool:
    if not (order.placed_ts < quote.ts <= order.expiry_ts):
        return False
    return token_price(order.side, quote.price) <= token_price(
        order.side, order.limit_price
    )


def simulate_maker_fill(
    order: MakerOrder, future_quotes: Sequence[PriceTick]
) -> datetime | None:
    for quote in future_quotes:
        if crosses(order, quote):
            return quote.ts
    return None


def round_trip_fill_costs(
    costs: CostInputs, entry_price: Decimal, exit_price: Decimal, shares: Decimal
) -> Decimal:
    return costs.fee_rate * (entry_price + exit_price) * shares + costs.gas_usd
