import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.feed import HistoricalFeed
from app.orchestrator import PaperResult, run_paper
from backtest.view import MarketView
from core.models import (
    Decision,
    GateResult,
    RiskLimits,
    Side,
    SizingResult,
)
from data.events import MarketEvent, Quote, event_from_quote

_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def _event(minute: int, price: str) -> MarketEvent:
    return event_from_quote(
        Quote(market_id="m", ts=_T0 + timedelta(minutes=minute), price=Decimal(price))
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        bankroll=Decimal("25"),
        kelly_fraction=0.25,
        max_position_fraction=0.2,
        max_position_usd=Decimal("5"),
    )


class _BuyBelowHalf:
    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        if event.quote.price < Decimal("0.50"):
            return Decision(
                gate=GateResult.act(side=Side.BUY_YES, edge=0.1),
                sizing=SizingResult(stake_usd=Decimal("4"), shares=Decimal("10")),
            )
        return None


def test_run_paper_matches_engine_on_historical_feed() -> None:
    async def _run() -> None:
        feed = HistoricalFeed([_event(1, "0.40"), _event(2, "0.38"), _event(3, "0.55")])
        result = await run_paper(feed, _BuyBelowHalf(), _limits())
        assert isinstance(result, PaperResult)
        assert len(result.fills) == 1
        assert result.fills[0].entry_price == Decimal("0.40")
        assert result.realized_pnl == Decimal("1.50")

    asyncio.run(_run())


def test_run_paper_no_orders_when_strategy_abstains() -> None:
    async def _run() -> None:
        class _NeverActs:
            def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
                return None

        feed = HistoricalFeed([_event(1, "0.40"), _event(2, "0.55")])
        result = await run_paper(feed, _NeverActs(), _limits())
        assert result.fills == ()
        assert result.realized_pnl == Decimal("0")

    asyncio.run(_run())
