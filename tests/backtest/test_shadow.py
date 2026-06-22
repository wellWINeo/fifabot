from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from backtest.signals import ShadowForecastStrategy
from backtest.view import MarketView
from core.models import CalibrationSample, CostInputs, RiskLimits
from data.events import Quote, event_from_quote
from llm.agent import HypothesisAgent, MarketFeatures
from llm.schema import HypothesisOutput

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


def _strategy(runner: Callable[[MarketFeatures], object]) -> ShadowForecastStrategy:
    return ShadowForecastStrategy(
        agent=HypothesisAgent(runner),
        costs=_costs(),
        notional_hint=Decimal("10"),
        calibrator=_Id(),
        limits=_limits(),
    )


def test_shadow_emits_decision_from_hypothesis() -> None:
    quote = Quote(market_id="m", ts=_T0, price=Decimal("0.50"))
    view = MarketView(_T0, {"m": [quote]}, None)
    strat = _strategy(
        lambda f: HypothesisOutput(p_fair=0.70, confidence=0.6, rationale="x")
    )
    decision = strat.on_event(event_from_quote(quote), view)
    assert decision is not None
    assert decision.gate.action == "act"  # 0.70 vs 0.50 clears the hurdle


def test_shadow_malformed_hypothesis_returns_none() -> None:
    quote = Quote(market_id="m", ts=_T0, price=Decimal("0.50"))
    view = MarketView(_T0, {"m": [quote]}, None)
    strat = _strategy(lambda f: {"p_fair": 5.0})  # invalid -> agent yields None
    assert strat.on_event(event_from_quote(quote), view) is None
