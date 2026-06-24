"""Pure risk brain: caps, runaway-loop breakers, and a sticky kill switch.

All financial/safety logic lives here as pure functions over a frozen
RiskState. The execution edge calls these; it never re-implements them. Two
halt scopes: global (sticky kill switch) and per-market suppression.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.metrics import realized_pnl
from core.models import Fill, Side

HaltScope = Literal["global", "market"]


class RiskConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_position_usd: Decimal = Field(gt=0)
    max_daily_loss_usd: Decimal = Field(gt=0)
    max_orders_per_run: int = Field(gt=0)
    resubmit_window_seconds: float = Field(gt=0)
    max_orders_per_market_in_window: int = Field(gt=0)


class RiskOrder(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str
    notional: Decimal = Field(gt=0)


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    allowed: bool
    scope: HaltScope | None = None
    reason: str | None = None


class RiskState(BaseModel):
    model_config = ConfigDict(frozen=True)

    day: date
    halted: bool = False
    halt_reason: str | None = None
    halted_markets: frozenset[str] = frozenset()
    day_realized_pnl: Decimal = Decimal(0)
    unrealized_pnl: Decimal = Decimal(0)
    orders_this_run: int = 0
    exposure: dict[str, Decimal] = Field(default_factory=dict)
    order_ts: dict[str, tuple[datetime, ...]] = Field(default_factory=dict)

    @classmethod
    def start(cls, now: datetime) -> RiskState:
        return cls(day=now.astimezone().date())


def trip(state: RiskState, reason: str) -> RiskState:
    if state.halted:
        return state
    return state.model_copy(update={"halted": True, "halt_reason": reason})


def suppress_market(state: RiskState, market_id: str, reason: str) -> RiskState:
    return state.model_copy(
        update={"halted_markets": state.halted_markets | {market_id}}
    )


def _recent_count(
    state: RiskState, market_id: str, now: datetime, window: float
) -> int:
    cutoff = now.timestamp() - window
    return sum(1 for ts in state.order_ts.get(market_id, ()) if ts.timestamp() > cutoff)


def pretrade_check(
    state: RiskState, config: RiskConfig, order: RiskOrder, now: datetime
) -> tuple[RiskState, CheckResult]:
    if state.halted:
        return state, CheckResult(
            allowed=False, scope="global", reason=state.halt_reason
        )
    if order.market_id in state.halted_markets:
        return state, CheckResult(
            allowed=False, scope="market", reason=f"market {order.market_id} suppressed"
        )
    if state.orders_this_run >= config.max_orders_per_run:
        reason = f"order-count ceiling {config.max_orders_per_run} reached"
        return (
            trip(state, reason),
            CheckResult(allowed=False, scope="global", reason=reason),
        )
    recent = _recent_count(state, order.market_id, now, config.resubmit_window_seconds)
    if recent >= config.max_orders_per_market_in_window:
        reason = f"rapid resubmission on {order.market_id}"
        return (
            trip(state, reason),
            CheckResult(allowed=False, scope="global", reason=reason),
        )
    projected = state.exposure.get(order.market_id, Decimal(0)) + order.notional
    if projected > config.max_position_usd:
        return state, CheckResult(
            allowed=False, scope="market", reason=f"position cap on {order.market_id}"
        )
    return state, CheckResult(allowed=True)


def on_order_placed(state: RiskState, order: RiskOrder, now: datetime) -> RiskState:
    exposure = dict(state.exposure)
    prev = exposure.get(order.market_id, Decimal(0))
    exposure[order.market_id] = prev + order.notional
    order_ts = dict(state.order_ts)
    order_ts[order.market_id] = (*order_ts.get(order.market_id, ()), now)
    return state.model_copy(
        update={
            "orders_this_run": state.orders_this_run + 1,
            "exposure": exposure,
            "order_ts": order_ts,
        }
    )


def roll_day(state: RiskState, now: datetime) -> RiskState:
    today = now.astimezone().date()
    if state.day == today:
        return state
    return state.model_copy(
        update={
            "day": today,
            "day_realized_pnl": Decimal(0),
            "unrealized_pnl": Decimal(0),
        }
    )


def on_fill(state: RiskState, market_id: str, fill: Fill, now: datetime) -> RiskState:
    state = roll_day(state, now)
    pnl = realized_pnl([fill])
    # exposure is recorded in YES-quote-price space (order.notional());
    # fill.entry_price is the token-space price, so convert back before releasing.
    yes_price = (
        fill.entry_price if fill.side is Side.BUY_YES else Decimal(1) - fill.entry_price
    )
    cost_basis = yes_price * fill.shares
    remaining = max(Decimal(0), state.exposure.get(market_id, Decimal(0)) - cost_basis)
    exposure = dict(state.exposure)
    if remaining == Decimal(0):
        exposure.pop(market_id, None)
    else:
        exposure[market_id] = remaining
    return state.model_copy(
        update={
            "day_realized_pnl": state.day_realized_pnl + pnl,
            "exposure": exposure,
        }
    )


def on_mark(
    state: RiskState,
    config: RiskConfig,
    unrealized_pnl: Decimal,
    now: datetime,
) -> RiskState:
    state = roll_day(state, now)
    marked = state.model_copy(update={"unrealized_pnl": unrealized_pnl})
    if state.day_realized_pnl + unrealized_pnl <= -config.max_daily_loss_usd:
        return trip(marked, f"daily loss cap {config.max_daily_loss_usd} breached")
    return marked
