"""Sizing: fractional Kelly, hard caps, abstain-is-zero (property)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.edge_gate import decide
from core.models import CostInputs, GateResult, RiskLimits, Side, TradeCandidate
from core.sizing import kelly_fraction, size


def _candidate(price: str) -> TradeCandidate:
    return TradeCandidate(
        price=Decimal(price),
        raw_prob=0.5,
        costs=CostInputs(
            spread=Decimal("0"),
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
        max_position_fraction=0.2,  # cap = 5
        max_position_usd=Decimal("4"),  # strictly smallest cap
    )


def test_kelly_fraction_yes_and_no() -> None:
    # YES: (0.6-0.5)/(1-0.5) = 0.2
    assert kelly_fraction(0.6, 0.5, Side.BUY_YES) == pytest.approx(0.2)
    # NO: (0.5-0.4)/0.5 = 0.2
    assert kelly_fraction(0.4, 0.5, Side.BUY_NO) == pytest.approx(0.2)


def test_kelly_fraction_clamps_negative_to_zero() -> None:
    assert kelly_fraction(0.4, 0.5, Side.BUY_YES) == 0.0


@pytest.mark.parametrize("p", [0.0, 1.0])
def test_kelly_fraction_rejects_boundary_price(p: float) -> None:
    with pytest.raises(ValueError):
        kelly_fraction(0.5, p, Side.BUY_YES)


def test_abstain_sizes_to_zero() -> None:
    gate = GateResult.abstain(reason="x")
    result = size(_candidate("0.50"), gate, _limits())
    assert result.stake_usd == Decimal("0")
    assert result.shares == Decimal("0")


def test_cap_binds_and_is_recorded() -> None:
    # Big edge -> uncapped Kelly stake 5.625; max_position_usd=4 is the smallest cap.
    gate = decide(_candidate("0.50"), q=0.95, hurdle=0.01)
    result = size(_candidate("0.50"), gate, _limits())
    assert result.stake_usd == Decimal("4")
    assert result.binding_cap == "max_position_usd"


_edge_prices = st.integers(min_value=1, max_value=9).map(lambda n: Decimal(n) / 1000)
_edge_q = st.floats(min_value=0.0, max_value=1.0)


@given(_edge_prices, _edge_q)
def test_kelly_fraction_bounded_near_edges(price: Decimal, q: float) -> None:
    for side in (Side.BUY_YES, Side.BUY_NO):
        f = kelly_fraction(q, float(price), side)
        assert 0.0 <= f <= 1.0


_price = st.integers(min_value=1, max_value=99).map(lambda n: Decimal(n) / 100)
_q = st.floats(min_value=0.0, max_value=1.0)


@given(_price, _q)
def test_stake_never_exceeds_caps_or_bankroll(price: Decimal, q: float) -> None:
    candidate = _candidate(str(price))
    limits = _limits()
    gate = decide(candidate, q, hurdle=0.0)
    result = size(candidate, gate, limits)
    cap = min(
        Decimal(str(limits.max_position_fraction)) * limits.bankroll,
        limits.max_position_usd,
        limits.bankroll,
    )
    assert result.stake_usd >= Decimal("0")
    assert result.stake_usd <= cap
