"""Domain model validation: tick alignment, bounds, tagged-union helpers."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.models import (
    CostInputs,
    GateResult,
    RiskLimits,
    Side,
    TradeCandidate,
)


def _costs() -> CostInputs:
    return CostInputs(
        spread=Decimal("0.01"),
        fee_rate=Decimal("0"),
        gas_usd=Decimal("0.05"),
        model_error_margin=Decimal("0.01"),
    )


def test_candidate_accepts_on_tick_price() -> None:
    c = TradeCandidate(
        price=Decimal("0.55"),
        raw_prob=0.6,
        costs=_costs(),
        notional_hint=Decimal("10"),
    )
    assert c.price == Decimal("0.55")
    assert c.tick_size == Decimal("0.01")


def test_candidate_rejects_off_tick_price() -> None:
    with pytest.raises(ValidationError):
        TradeCandidate(
            price=Decimal("0.555"),
            raw_prob=0.6,
            costs=_costs(),
            notional_hint=Decimal("10"),
        )


@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("1"), Decimal("1.5")])
def test_candidate_rejects_out_of_range_price(bad: Decimal) -> None:
    with pytest.raises(ValidationError):
        TradeCandidate(
            price=bad,
            raw_prob=0.6,
            costs=_costs(),
            notional_hint=Decimal("10"),
        )


def test_costs_reject_negative() -> None:
    with pytest.raises(ValidationError):
        CostInputs(
            spread=Decimal("-0.01"),
            fee_rate=Decimal("0"),
            gas_usd=Decimal("0"),
            model_error_margin=Decimal("0"),
        )


def test_risk_limits_reject_kelly_above_one() -> None:
    with pytest.raises(ValidationError):
        RiskLimits(
            bankroll=Decimal("25"),
            kelly_fraction=1.5,
            max_position_fraction=0.2,
            max_position_usd=Decimal("5"),
        )


def test_gate_result_constructors() -> None:
    act = GateResult.act(side=Side.BUY_YES, edge=0.05)
    assert act.action == "act"
    assert act.side is Side.BUY_YES
    assert act.edge == 0.05

    out = GateResult.abstain(reason="below hurdle")
    assert out.action == "abstain"
    assert out.side is None
    assert out.reason == "below hurdle"


def test_gate_result_rejects_act_without_side_and_edge() -> None:
    with pytest.raises(ValidationError):
        GateResult(action="act", side=None, edge=None)


def test_gate_result_rejects_abstain_with_side_or_edge() -> None:
    with pytest.raises(ValidationError):
        GateResult(action="abstain", side=Side.BUY_YES, edge=0.05)


def test_models_are_frozen() -> None:
    c = _costs()
    with pytest.raises(ValidationError):
        c.spread = Decimal("0.02")  # type: ignore[misc]
