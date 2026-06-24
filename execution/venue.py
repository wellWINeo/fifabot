"""Execution venue seam + preflight pipeline.

Pipeline order: validate -> risk pretrade -> (Clob only) allowance -> submit.
Each step yields a typed OrderResult; a raw client/validation error never
reaches the caller. SimulatedVenue runs preflight only; ClobVenue adds the
allowance precondition and the real client submit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from core.risk import (
    RiskConfig,
    RiskOrder,
    RiskState,
    on_order_placed,
    pretrade_check,
    suppress_market,
)
from data.events import Market
from execution.client import ExecutionClient
from execution.orders import (
    OrderRequest,
    OrderResult,
    OrderValidationError,
    validate_order,
)


class ExecutionVenue(Protocol):
    def place(
        self,
        order: OrderRequest,
        market: Market,
        state: RiskState,
        config: RiskConfig,
        now: datetime,
    ) -> tuple[RiskState, OrderResult]: ...


def _preflight(
    order: OrderRequest,
    market: Market,
    state: RiskState,
    config: RiskConfig,
    now: datetime,
) -> tuple[RiskState, OrderResult | None]:
    try:
        validate_order(order, market)
    except OrderValidationError as exc:
        return state, OrderResult(status="rejected", reason=str(exc))
    risk_order = RiskOrder(market_id=order.market_id, notional=order.notional())
    new_state, check = pretrade_check(state, config, risk_order, now)
    if not check.allowed:
        status = "halted" if check.scope == "global" else "rejected"
        return new_state, OrderResult(status=status, reason=check.reason)
    return new_state, None


class SimulatedVenue:
    def place(
        self,
        order: OrderRequest,
        market: Market,
        state: RiskState,
        config: RiskConfig,
        now: datetime,
    ) -> tuple[RiskState, OrderResult]:
        state, blocked = _preflight(order, market, state, config, now)
        if blocked is not None:
            return state, blocked
        risk_order = RiskOrder(market_id=order.market_id, notional=order.notional())
        return on_order_placed(state, risk_order, now), OrderResult(
            status="placed", order_id="sim"
        )


class ClobVenue:
    def __init__(self, client: ExecutionClient) -> None:
        self._client = client

    def place(
        self,
        order: OrderRequest,
        market: Market,
        state: RiskState,
        config: RiskConfig,
        now: datetime,
    ) -> tuple[RiskState, OrderResult]:
        state, blocked = _preflight(order, market, state, config, now)
        if blocked is not None:
            return state, blocked
        if self._client.allowances().usdc < order.notional():
            reason = "halt-market: insufficient USDC allowance"
            return suppress_market(state, order.market_id, reason), OrderResult(
                status="rejected", reason=reason
            )
        order_id = self._client.place(order)
        risk_order = RiskOrder(market_id=order.market_id, notional=order.notional())
        return on_order_placed(state, risk_order, now), OrderResult(
            status="placed", order_id=order_id
        )
