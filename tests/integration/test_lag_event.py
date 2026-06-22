import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.feed import HistoricalFeed
from app.orchestrator import run_paper
from backtest.signals import (
    CompositeStrategy,
    DivergenceStrategy,
    NamedSignal,
    SignalDecision,
)
from core.models import CalibrationSample, CostInputs, RiskLimits, Side
from data.events import Quote, event_from_quote
from data.reference import ReplayReference

_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


class _Id:
    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        return None

    def predict(self, raw: float) -> float:
        return raw


def _costs() -> CostInputs:
    return CostInputs(
        spread=Decimal("0.01"),
        fee_rate=Decimal("0"),
        gas_usd=Decimal("0"),
        model_error_margin=Decimal("0"),
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        bankroll=Decimal("25"),
        kelly_fraction=0.25,
        max_position_fraction=0.2,
        max_position_usd=Decimal("5"),
    )


def _quote(minute: int, price: str) -> Quote:
    return Quote(
        market_id="m", ts=_T0 + timedelta(minutes=minute), price=Decimal(price)
    )


def _composite(log: list[SignalDecision]) -> CompositeStrategy:
    s1 = DivergenceStrategy(
        costs=_costs(),
        notional_hint=Decimal("10"),
        calibrator=_Id(),
        limits=_limits(),
    )
    return CompositeStrategy([NamedSignal("S1", s1, promoted=True)], log=log)


def test_lag_event_produces_act_and_fills() -> None:
    async def _run() -> None:
        # Reference fair = 0.70 throughout; Polymarket lags at 0.50 then drifts up.
        ref = ReplayReference([Quote(market_id="m", ts=_T0, price=Decimal("0.70"))])
        events = [_quote(1, "0.50"), _quote(2, "0.49"), _quote(3, "0.68")]
        log: list[SignalDecision] = []
        feed = HistoricalFeed([event_from_quote(q) for q in events])
        result = await run_paper(
            feed, _composite(log), _limits(), reference=ref, costs=_costs()
        )
        # S1 sees 0.50 << 0.70 fair -> ACT BUY_YES; order rests at 0.50, event 2
        # dips to 0.49 -> fills; event 3 at 0.68 -> closes for a profit.
        acted = [r for r in log if r.action == "act"]
        assert acted and acted[0].side is Side.BUY_YES
        assert len(result.fills) == 1
        assert result.realized_pnl > 0

    asyncio.run(_run())


def test_no_edge_match_abstains_and_never_fills() -> None:
    async def _run() -> None:
        ref = ReplayReference([Quote(market_id="m", ts=_T0, price=Decimal("0.50"))])
        events = [_quote(1, "0.50"), _quote(2, "0.50"), _quote(3, "0.50")]
        log: list[SignalDecision] = []
        feed = HistoricalFeed([event_from_quote(q) for q in events])
        result = await run_paper(
            feed, _composite(log), _limits(), reference=ref, costs=_costs()
        )
        assert all(r.action == "abstain" for r in log)
        assert result.fills == ()
        assert result.realized_pnl == Decimal("0")

    asyncio.run(_run())
