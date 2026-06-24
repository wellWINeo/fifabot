"""Thin async paper-trading loop. Zero real orders -- fills are simulated.

Consumes a Feed, builds the as-of MarketView per event, runs the strategy, and
applies the same maker-first fill lifecycle as the backtest engine via
core.fills. Historical feed -> deterministic; live feed -> human-run smoke test.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.feed import Feed
from backtest.strategy import Strategy
from backtest.view import MarketView
from core.fills import MakerOrder, crosses, round_trip_fill_costs, token_price
from core.metrics import realized_pnl
from core.models import CostInputs, Fill, RiskLimits, Side
from core.risk import RiskConfig, RiskState, on_fill, on_mark
from data.events import Market, Quote
from data.reference import ReferencePrice
from execution.orders import OrderRequest
from execution.store import InMemoryRiskStore, RiskStore
from execution.venue import ExecutionVenue, SimulatedVenue

_ZERO_COSTS = CostInputs(
    spread=Decimal(0),
    fee_rate=Decimal(0),
    gas_usd=Decimal(0),
    model_error_margin=Decimal(0),
)

_T0_FALLBACK = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class PaperResult:
    fills: tuple[Fill, ...]
    realized_pnl: Decimal
    halted: bool = False
    halt_reason: str | None = None


@dataclass
class _OpenPosition:
    side: Side
    entry_price: Decimal
    shares: Decimal
    opened_ts: datetime


def _unrealized_pnl(
    open_positions: Mapping[str, _OpenPosition],
    quotes_by_market: Mapping[str, list[Quote]],
) -> Decimal:
    total = Decimal(0)
    for market, position in open_positions.items():
        mark = token_price(position.side, quotes_by_market[market][-1].price)
        total += (mark - position.entry_price) * position.shares
    return total


async def run_paper(
    feed: Feed,
    strategy: Strategy,
    limits: RiskLimits,
    *,
    reference: ReferencePrice | None = None,
    costs: CostInputs = _ZERO_COSTS,
    fill_expiry: timedelta = timedelta(minutes=5),
    risk: RiskConfig | None = None,
    markets: Mapping[str, Market] | None = None,
    venue: ExecutionVenue | None = None,
    store: RiskStore | None = None,
) -> PaperResult:
    risk_enabled = risk is not None
    if risk_enabled and markets is None:
        raise ValueError("markets is required when risk is supplied")
    venue = venue or SimulatedVenue()
    store = store or InMemoryRiskStore()
    risk_state: RiskState | None = None
    if risk_enabled:
        risk_state = store.load() or RiskState.start(_T0_FALLBACK)

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
            fill = Fill(
                side=position.side,
                entry_price=position.entry_price,
                exit_price=exit_price,
                shares=position.shares,
                costs_usd=round_trip_fill_costs(
                    costs,
                    position.entry_price,
                    exit_price,
                    position.shares,
                ),
            )
            fills.append(fill)
            if risk_enabled:
                assert risk_state is not None
                risk_state = on_fill(risk_state, market, fill, quote.ts)
                store.save(risk_state)

        if risk_enabled:
            assert risk_state is not None and risk is not None
            risk_state = on_mark(
                risk_state,
                risk,
                _unrealized_pnl(open_positions, quotes_by_market),
                quote.ts,
            )
            store.save(risk_state)

        wants_order = (
            market not in pending
            and market not in open_positions
            and decision is not None
            and decision.gate.action == "act"
            and decision.gate.side is not None
            and decision.sizing.shares > 0
        )
        if wants_order:
            assert decision is not None and decision.gate.side is not None
            admit = True
            if risk_enabled:
                assert (
                    risk_state is not None and markets is not None and risk is not None
                )
                if risk_state.halted:
                    admit = False
                else:
                    order_req = OrderRequest(
                        market_id=market,
                        token_id=markets[market].token_ids[0],
                        side=decision.gate.side,
                        price=quote.price,
                        size=decision.sizing.shares,
                    )
                    risk_state, result = venue.place(
                        order_req,
                        markets[market],
                        risk_state,
                        risk,
                        quote.ts,
                    )
                    store.save(risk_state)
                    admit = result.status == "placed"
            if admit:
                pending[market] = MakerOrder(
                    side=decision.gate.side,
                    limit_price=quote.price,
                    shares=decision.sizing.shares,
                    placed_ts=quote.ts,
                    expiry_ts=quote.ts + fill_expiry,
                )

    for market, position in open_positions.items():
        last_quote = quotes_by_market[market][-1]
        exit_price = token_price(position.side, last_quote.price)
        fill = Fill(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            shares=position.shares,
            costs_usd=round_trip_fill_costs(
                costs, position.entry_price, exit_price, position.shares
            ),
        )
        fills.append(fill)
        if risk_enabled:
            assert risk_state is not None
            risk_state = on_fill(risk_state, market, fill, last_quote.ts)
            store.save(risk_state)

    return PaperResult(
        fills=tuple(fills),
        realized_pnl=realized_pnl(fills),
        halted=risk_state.halted if risk_state is not None else False,
        halt_reason=risk_state.halt_reason if risk_state is not None else None,
    )
