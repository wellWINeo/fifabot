"""Replay engine: deterministic fills, P&L via core.metrics, view forbids future."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from backtest.engine import BacktestResult, replay
from backtest.feed import LookAheadError
from backtest.signals import ConsistencyStrategy
from backtest.view import MarketView
from core.models import (
    CalibrationSample,
    CostInputs,
    Decision,
    GateResult,
    RiskLimits,
    Side,
    SizingResult,
)
from data.events import MarketEvent, MarketGroup, Quote, event_from_quote


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


def test_replay_maker_fills_then_closes_pnl() -> None:
    # Order rests at 0.40 (event 1). Event 2 dips to 0.38 -> crosses -> fill at 0.40.
    # Once filled, the market is "open", so no new order rests; event 3 at 0.55
    # closes the position. pnl = 10*(0.55-0.40) - costs(0) = 1.50.
    result = replay(
        [_event(1, "0.40"), _event(2, "0.38"), _event(3, "0.55")],
        _BuyBelowHalf(),
        _limits(),
    )
    assert isinstance(result, BacktestResult)
    assert len(result.fills) == 1
    fill = result.fills[0]
    assert fill.entry_price == Decimal("0.40")
    assert fill.exit_price == Decimal("0.55")
    assert result.realized_pnl == Decimal("1.50")


def test_replay_order_expires_unfilled_when_price_never_crosses() -> None:
    # Rests at 0.40; price only rises -> never crosses -> no fill, no position.
    result = replay(
        [_event(1, "0.40"), _event(2, "0.55"), _event(3, "0.60")],
        _BuyBelowHalf(),
        _limits(),
    )
    assert result.fills == ()
    assert result.realized_pnl == Decimal("0")


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


_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


class _Id:
    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        return None

    def predict(self, raw: float) -> float:
        return raw


def test_replay_records_signal_probs() -> None:
    group = MarketGroup(group_id="g", market_ids=("a", "b", "c"))
    prices = {"a": "0.50", "b": "0.30", "c": "0.28"}
    # In event-driven replay a leg's group is only complete once every leg has
    # appeared. Legs arrive a, b, c (group completes at c); then a re-quotes so
    # leg "a" is also evaluated against the now-complete group.
    schedule = [
        ("a", _T0),
        ("b", _T0 + timedelta(minutes=1)),
        ("c", _T0 + timedelta(minutes=2)),
        ("a", _T0 + timedelta(minutes=3)),
    ]
    events = [
        event_from_quote(Quote(market_id=mid, ts=ts, price=Decimal(prices[mid])))
        for mid, ts in schedule
    ]
    limits = RiskLimits(
        bankroll=Decimal("25"),
        kelly_fraction=0.25,
        max_position_fraction=0.2,
        max_position_usd=Decimal("5"),
    )
    strategy = ConsistencyStrategy(
        groups=[group],
        costs=CostInputs(
            spread=Decimal("0.01"),
            fee_rate=Decimal("0"),
            gas_usd=Decimal("0"),
            model_error_margin=Decimal("0"),
        ),
        notional_hint=Decimal("10"),
        calibrator=_Id(),
        limits=limits,
    )
    result = replay(events, strategy, limits)
    recorded = dict(result.signal_probs)
    # the de-vigged fair for each leg evaluated against the full basket (sum 1.08)
    assert recorded["c"] == pytest.approx(0.28 / 1.08)
    assert recorded["a"] == pytest.approx(0.50 / 1.08)
