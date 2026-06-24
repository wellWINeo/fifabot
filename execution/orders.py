"""Order request/result models and pure pre-submission validation.

Validation is a pure guard that raises before any signing or network call.
The venue maps the raised error to a typed OrderResult (it never lets a raw
error reach the caller).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.models import Side
from data.events import Market


class OrderValidationError(ValueError):
    """Raised when an order violates tick size or minimum order size."""


class OrderRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str
    token_id: str
    side: Side
    price: Decimal = Field(gt=0, lt=1)
    size: Decimal = Field(gt=0)
    signature_type: int = 0

    def notional(self) -> Decimal:
        return self.price * self.size


class OrderResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["placed", "rejected", "halted"]
    reason: str | None = None
    order_id: str | None = None


def validate_order(order: OrderRequest, market: Market) -> None:
    if order.price % market.tick_size != 0:
        raise OrderValidationError(
            f"price {order.price} is not a multiple of tick {market.tick_size}"
        )
    if order.size < market.minimum_order_size:
        raise OrderValidationError(
            f"size {order.size} below minimum {market.minimum_order_size}"
        )
