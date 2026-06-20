"""Pure decision pipeline: compose calibrate -> cost -> gate -> size."""

from collections.abc import Sequence
from decimal import Decimal

from core.decision import evaluate
from core.models import CalibrationSample, CostInputs, RiskLimits, Side, TradeCandidate


class _FixedCalibrator:
    """Test double: returns a preset calibrated probability."""

    def __init__(self, value: float) -> None:
        self._value = value

    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        return None

    def predict(self, raw: float) -> float:
        return self._value


def _candidate(spread: str) -> TradeCandidate:
    return TradeCandidate(
        price=Decimal("0.50"),
        raw_prob=0.5,
        costs=CostInputs(
            spread=Decimal(spread),
            fee_rate=Decimal("0"),
            gas_usd=Decimal("0"),
            model_error_margin=Decimal("0"),
        ),
        notional_hint=Decimal("10"),
    )


def _limits() -> RiskLimits:
    return RiskLimits(
        bankroll=Decimal("25"),
        kelly_fraction=0.25,
        max_position_fraction=0.2,
        max_position_usd=Decimal("5"),
    )


def test_abstains_below_hurdle_with_zero_size() -> None:
    # q=0.52, price=0.50 -> edge 0.02; hurdle = spread 0.05 -> abstain.
    decision = evaluate(_candidate("0.05"), _FixedCalibrator(0.52), _limits())
    assert decision.gate.action == "abstain"
    assert decision.sizing.stake_usd == Decimal("0")
    assert decision.sizing.shares == Decimal("0")


def test_acts_above_hurdle_with_capped_nonzero_size() -> None:
    # q=0.70, price=0.50 -> edge 0.20; hurdle = spread 0.01 -> act long, capped.
    decision = evaluate(_candidate("0.01"), _FixedCalibrator(0.70), _limits())
    assert decision.gate.action == "act"
    assert decision.gate.side is Side.BUY_YES
    assert decision.sizing.stake_usd > Decimal("0")
    assert decision.sizing.stake_usd <= Decimal("5")  # max_position_usd cap
