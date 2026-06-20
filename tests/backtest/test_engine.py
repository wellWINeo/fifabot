"""Replay engine: deterministic fills, P&L via core.metrics, view forbids future."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backtest.engine import BacktestResult, replay
from backtest.feed import LookAheadError
from backtest.view import MarketView
from core.models import Decision, GateResult, RiskLimits, Side, SizingResult
from data.events import MarketEvent, Quote, event_from_quote


def _event(minute: int, price: str) -> MarketEvent:
    return event_from_quote(
        Quote(
            market_id="m1",
            ts=datetime(2024, 6, 1, 0, minute, tzinfo=UTC),
            price=Decimal(price),
        )
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        bankroll=Decimal("25"),
        kelly_fraction=0.25,
        max_position_fraction=0.2,
        max_position_usd=Decimal("5"),
    )


class _BuyBelowHalf:
    """Stateless synthetic strategy: buy YES whenever price < 0.50."""

    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        if event.quote.price < Decimal("0.50"):
            return Decision(
                gate=GateResult.act(side=Side.BUY_YES, edge=0.1),
                sizing=SizingResult(stake_usd=Decimal("5"), shares=Decimal("10")),
            )
        return None


def test_replay_open_then_close_pnl() -> None:
    # Buy 10 YES at 0.40 (event 1), close at 0.55 (event 2). pnl = 10*(0.55-0.40).
    result = replay([_event(1, "0.40"), _event(2, "0.55")], _BuyBelowHalf(), _limits())
    assert isinstance(result, BacktestResult)
    assert result.realized_pnl == Decimal("1.50")
    assert len(result.fills) == 1


def test_replay_is_deterministic() -> None:
    events = [_event(1, "0.40"), _event(2, "0.55"), _event(3, "0.48")]
    r1 = replay(events, _BuyBelowHalf(), _limits())
    r2 = replay(events, _BuyBelowHalf(), _limits())
    assert r1 == r2


def test_replay_no_trades_when_strategy_abstains() -> None:
    class _NeverActs:
        def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
            return None

    result = replay([_event(1, "0.40"), _event(2, "0.55")], _NeverActs(), _limits())
    assert result.fills == ()
    assert result.realized_pnl == Decimal("0")


def test_strategy_view_forbids_future() -> None:
    captured: dict[str, MarketView] = {}

    class _Spy:
        def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
            captured["view"] = view
            return None

    replay([_event(1, "0.40")], _Spy(), _limits())
    view = captured["view"]
    future = view.as_of + timedelta(minutes=1)
    with pytest.raises(LookAheadError):
        view.price_at("m1", future)
