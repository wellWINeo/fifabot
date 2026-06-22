"""Thin async paper-trading loop. Zero real orders -- fills are simulated.

Consumes a Feed, builds the as-of MarketView per event, runs the strategy, and
applies the same maker-first fill lifecycle as the backtest engine via
core.fills. Historical feed -> deterministic; live feed -> human-run smoke test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.feed import Feed
from backtest.strategy import Strategy
from backtest.view import MarketView
from core.fills import MakerOrder, crosses, round_trip_fill_costs, token_price
from core.metrics import realized_pnl
from core.models import CostInputs, Fill, RiskLimits, Side
from data.events import Quote
from data.reference import ReferencePrice

_ZERO_COSTS = CostInputs(
    spread=Decimal(0),
    fee_rate=Decimal(0),
    gas_usd=Decimal(0),
    model_error_margin=Decimal(0),
)


@dataclass(frozen=True)
class PaperResult:
    fills: tuple[Fill, ...]
    realized_pnl: Decimal


@dataclass
class _OpenPosition:
    side: Side
    entry_price: Decimal
    shares: Decimal
    opened_ts: datetime


async def run_paper(
    feed: Feed,
    strategy: Strategy,
    limits: RiskLimits,
    *,
    reference: ReferencePrice | None = None,
    costs: CostInputs = _ZERO_COSTS,
    fill_expiry: timedelta = timedelta(minutes=5),
) -> PaperResult:
    quotes_by_market: dict[str, list[Quote]] = {}
    pending: dict[str, MakerOrder] = {}
    open_positions: dict[str, _OpenPosition] = {}
    fills: list[Fill] = []

    async for event in feed.events():
        quotes_by_market.setdefault(event.market_id, []).append(event.quote)
        view = MarketView(event.ts, quotes_by_market, reference)
        decision = strategy.on_event(event, view)

        market = event.market_id
        quote = event.quote

        if market in pending:
            order = pending[market]
            if crosses(order, quote):
                open_positions[market] = _OpenPosition(
                    side=order.side,
                    entry_price=token_price(order.side, order.limit_price),
                    shares=order.shares,
                    opened_ts=quote.ts,
                )
                del pending[market]
            elif quote.ts > order.expiry_ts:
                del pending[market]
        elif market in open_positions and quote.ts > open_positions[market].opened_ts:
            position = open_positions.pop(market)
            exit_price = token_price(position.side, quote.price)
            fills.append(
                Fill(
                    side=position.side,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    shares=position.shares,
                    costs_usd=round_trip_fill_costs(
                        costs, position.entry_price, exit_price, position.shares
                    ),
                )
            )

        if (
            market not in pending
            and market not in open_positions
            and decision is not None
            and decision.gate.action == "act"
            and decision.gate.side is not None
            and decision.sizing.shares > 0
        ):
            pending[market] = MakerOrder(
                side=decision.gate.side,
                limit_price=quote.price,
                shares=decision.sizing.shares,
                placed_ts=quote.ts,
                expiry_ts=quote.ts + fill_expiry,
            )

    for market, position in open_positions.items():
        last_price = quotes_by_market[market][-1].price
        exit_price = token_price(position.side, last_price)
        fills.append(
            Fill(
                side=position.side,
                entry_price=position.entry_price,
                exit_price=exit_price,
                shares=position.shares,
                costs_usd=round_trip_fill_costs(
                    costs, position.entry_price, exit_price, position.shares
                ),
            )
        )

    return PaperResult(fills=tuple(fills), realized_pnl=realized_pnl(fills))
