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
from core.risk import RiskConfig
from data.events import Market, MarketEvent, Quote, event_from_quote
from execution.store import InMemoryRiskStore

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


def _risk(**overrides: object) -> RiskConfig:
    base: dict[str, object] = dict(
        max_position_usd=Decimal("100"),
        max_daily_loss_usd=Decimal("100"),
        max_orders_per_run=1,
        resubmit_window_seconds=600.0,
        max_orders_per_market_in_window=10,
    )
    base.update(overrides)
    return RiskConfig(**base)


def _markets(*ids: str) -> dict[str, Market]:
    return {
        mid: Market(
            market_id=mid,
            question="q",
            token_ids=("yes", "no"),
            tick_size=Decimal("0.01"),
            minimum_order_size=Decimal("1"),
        )
        for mid in ids
    }


def test_unrealized_loss_on_open_position_trips_daily_loss_cap() -> None:
    async def _run() -> None:
        # Buys at 0.40, then the market craters to 0.05 while still open --
        # the open position's mark-to-market loss alone must trip the cap.
        feed = HistoricalFeed([_event(1, "0.40"), _event(2, "0.39"), _event(3, "0.05")])
        result = await run_paper(
            feed,
            _BuyBelowHalf(),
            _limits(),
            risk=_risk(max_daily_loss_usd=Decimal("1")),
            markets=_markets("m"),
        )
        assert result.halted is True
        assert "daily loss" in (result.halt_reason or "")

    asyncio.run(_run())


def test_on_fill_releases_exposure_under_real_market_id() -> None:
    async def _run() -> None:
        feed = HistoricalFeed([_event(1, "0.40"), _event(2, "0.38"), _event(3, "0.55")])
        store = InMemoryRiskStore()
        await run_paper(
            feed,
            _BuyBelowHalf(),
            _limits(),
            risk=_risk(max_position_usd=Decimal("4")),
            markets=_markets("m"),
            store=store,
        )
        final_state = store.load()
        assert final_state is not None
        assert final_state.exposure.get("m", Decimal(0)) == Decimal(0)
        assert final_state.exposure.get("", Decimal(0)) == Decimal(0)

    asyncio.run(_run())


def test_global_halt_blocks_further_admissions() -> None:
    async def _run() -> None:
        feed = HistoricalFeed(
            [
                event_from_quote(Quote(market_id="a", ts=_T0, price=Decimal("0.40"))),
                event_from_quote(
                    Quote(
                        market_id="b",
                        ts=_T0 + timedelta(minutes=1),
                        price=Decimal("0.40"),
                    )
                ),
            ]
        )

        class _AlwaysBuy:
            def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
                return Decision(
                    gate=GateResult.act(side=Side.BUY_YES, edge=0.1),
                    sizing=SizingResult(stake_usd=Decimal("4"), shares=Decimal("10")),
                )

        result = await run_paper(
            feed,
            _AlwaysBuy(),
            _limits(),
            risk=_risk(max_orders_per_run=1),
            markets=_markets("a", "b"),
        )
        assert result.halted is True
        assert "ceiling" in (result.halt_reason or "")

    asyncio.run(_run())
