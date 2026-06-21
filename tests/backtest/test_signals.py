# tests/backtest/test_signals.py
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

from backtest.signals import ConsistencyStrategy, DivergenceStrategy
from backtest.view import MarketView
from core.models import CalibrationSample, CostInputs, RiskLimits, Side
from data.events import MarketGroup, Quote, event_from_quote
from data.reference import ReplayReference

_TS = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


class _Identity:
    """Calibrator test double: predict(x) == x."""

    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        return None

    def predict(self, raw: float) -> float:
        return raw


def _costs(spread: str = "0.01") -> CostInputs:
    return CostInputs(
        spread=Decimal(spread),
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


def _divergence_strategy(spread: str = "0.01") -> DivergenceStrategy:
    return DivergenceStrategy(
        costs=_costs(spread),
        notional_hint=Decimal("10"),
        calibrator=_Identity(),
        limits=_limits(),
    )


def _view(pm_price: str, ref_price: str | None) -> tuple[MarketView, Quote]:
    quote = Quote(market_id="m", ts=_TS, price=Decimal(pm_price))
    ref = (
        ReplayReference([Quote(market_id="m", ts=_TS, price=Decimal(ref_price))])
        if ref_price is not None
        else ReplayReference([])
    )
    return MarketView(_TS, {"m": [quote]}, ref), quote


def test_divergence_acts_when_reference_diverges() -> None:
    view, quote = _view("0.50", "0.70")
    decision = _divergence_strategy().on_event(event_from_quote(quote), view)
    assert decision is not None
    assert decision.gate.action == "act"
    assert decision.gate.side is Side.BUY_YES


def test_divergence_abstains_when_aligned() -> None:
    view, quote = _view("0.50", "0.50")
    decision = _divergence_strategy().on_event(event_from_quote(quote), view)
    assert decision is not None
    assert decision.gate.action == "abstain"


def test_divergence_returns_none_without_reference() -> None:
    view, quote = _view("0.50", None)
    assert _divergence_strategy().on_event(event_from_quote(quote), view) is None


_GROUP = MarketGroup(group_id="g", market_ids=("a", "b", "c"))


def _consistency_strategy() -> ConsistencyStrategy:
    return ConsistencyStrategy(
        groups=[_GROUP],
        costs=_costs("0.01"),
        notional_hint=Decimal("10"),
        calibrator=_Identity(),
        limits=_limits(),
    )


def _group_view(prices: dict[str, str]) -> MarketView:
    quotes = {
        mid: [Quote(market_id=mid, ts=_TS, price=Decimal(p))]
        for mid, p in prices.items()
    }
    return MarketView(_TS, quotes, None)


def test_consistency_acts_on_overround_basket() -> None:
    # legs sum to 1.08 -> each YES overpriced -> de-vigged fair < price -> sell YES
    view = _group_view({"a": "0.50", "b": "0.30", "c": "0.28"})
    event = event_from_quote(Quote(market_id="a", ts=_TS, price=Decimal("0.50")))
    decision = _consistency_strategy().on_event(event, view)
    assert decision is not None
    assert decision.gate.action == "act"
    assert decision.gate.side is Side.BUY_NO


def test_consistency_abstains_on_balanced_basket() -> None:
    view = _group_view({"a": "0.34", "b": "0.33", "c": "0.33"})
    event = event_from_quote(Quote(market_id="a", ts=_TS, price=Decimal("0.34")))
    decision = _consistency_strategy().on_event(event, view)
    assert decision is not None
    assert decision.gate.action == "abstain"


def test_consistency_returns_none_for_incomplete_group() -> None:
    view = _group_view({"a": "0.50", "b": "0.30"})  # leg "c" missing
    event = event_from_quote(Quote(market_id="a", ts=_TS, price=Decimal("0.50")))
    assert _consistency_strategy().on_event(event, view) is None


def test_consistency_returns_none_for_unknown_market() -> None:
    view = _group_view({"z": "0.50"})
    event = event_from_quote(Quote(market_id="z", ts=_TS, price=Decimal("0.50")))
    assert _consistency_strategy().on_event(event, view) is None
