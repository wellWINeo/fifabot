"""Deterministic replay engine.

Pushes chronologically-ordered events through a Strategy, building an as-of
MarketView per event. Uses a minimal deterministic fill model so P&L is
reproducible; realistic fills/slippage are Phase 4. No wall-clock, no RNG.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from backtest.feed import load_events
from backtest.strategy import Strategy
from backtest.view import MarketView
from core.metrics import realized_pnl, roi
from core.models import Fill, RiskLimits, Side
from data.events import MarketEvent, Quote
from data.reference import ReferencePrice


@dataclass(frozen=True)
class BacktestResult:
    fills: tuple[Fill, ...]
    realized_pnl: Decimal
    roi: float
    signal_probs: tuple[tuple[str, float], ...] = ()


@dataclass
class _OpenPosition:
    side: Side
    entry_price: Decimal
    shares: Decimal


def _token_price(side: Side, yes_price: Decimal) -> Decimal:
    return yes_price if side is Side.BUY_YES else Decimal(1) - yes_price


def replay(
    events: Iterable[MarketEvent],
    strategy: Strategy,
    limits: RiskLimits,
    *,
    reference: ReferencePrice | None = None,
) -> BacktestResult:
    ordered = load_events(events)
    quotes_by_market: dict[str, list[Quote]] = {}
    open_positions: dict[str, _OpenPosition] = {}
    fills: list[Fill] = []
    deployed = Decimal(0)
    signal_probs: list[tuple[str, float]] = []

    for event in ordered:
        quotes_by_market.setdefault(event.market_id, []).append(event.quote)
        view = MarketView(event.ts, quotes_by_market, reference)
        decision = strategy.on_event(event, view)

        if decision is not None and decision.prob is not None:
            signal_probs.append((event.market_id, decision.prob))

        market = event.market_id
        yes_price = event.quote.price

        if market in open_positions:
            position = open_positions.pop(market)
            exit_price = _token_price(position.side, yes_price)
            fills.append(
                Fill(
                    side=position.side,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    shares=position.shares,
                    costs_usd=Decimal(0),
                )
            )
            deployed += position.entry_price * position.shares

        if (
            decision is not None
            and decision.gate.action == "act"
            and decision.gate.side is not None
            and decision.sizing.shares > 0
        ):
            side = decision.gate.side
            open_positions[market] = _OpenPosition(
                side=side,
                entry_price=_token_price(side, yes_price),
                shares=decision.sizing.shares,
            )

    for market, position in open_positions.items():
        last_yes_price = quotes_by_market[market][-1].price
        exit_price = _token_price(position.side, last_yes_price)
        fills.append(
            Fill(
                side=position.side,
                entry_price=position.entry_price,
                exit_price=exit_price,
                shares=position.shares,
                costs_usd=Decimal(0),
            )
        )
        deployed += position.entry_price * position.shares

    pnl = realized_pnl(fills)
    return BacktestResult(
        fills=tuple(fills),
        realized_pnl=pnl,
        roi=roi(pnl, deployed) if deployed > 0 else 0.0,
        signal_probs=tuple(signal_probs),
    )
