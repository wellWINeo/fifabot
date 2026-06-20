"""Edge gate: abstain-by-default and the cost-gate law (property)."""

from decimal import Decimal

from hypothesis import example, given
from hypothesis import strategies as st

from core.edge_gate import decide
from core.models import CostInputs, Side, TradeCandidate


def _candidate(price: str) -> TradeCandidate:
    return TradeCandidate(
        price=Decimal(price),
        raw_prob=0.5,
        costs=CostInputs(
            spread=Decimal("0.01"),
            fee_rate=Decimal("0"),
            gas_usd=Decimal("0"),
            model_error_margin=Decimal("0"),
        ),
        notional_hint=Decimal("10"),
    )


def test_acts_long_when_edge_clears_hurdle() -> None:
    result = decide(_candidate("0.50"), q=0.60, hurdle=0.05)
    assert result.action == "act"
    assert result.side is Side.BUY_YES
    assert result.edge is not None and result.edge > 0


def test_acts_short_when_negative_edge_clears_hurdle() -> None:
    result = decide(_candidate("0.50"), q=0.40, hurdle=0.05)
    assert result.action == "act"
    assert result.side is Side.BUY_NO


def test_abstains_on_zero_edge() -> None:
    result = decide(_candidate("0.50"), q=0.50, hurdle=0.0)
    assert result.action == "abstain"


def test_abstains_when_edge_below_hurdle() -> None:
    result = decide(_candidate("0.50"), q=0.52, hurdle=0.05)
    assert result.action == "abstain"


_price = st.integers(min_value=1, max_value=99).map(lambda n: Decimal(n) / 100)
_q = st.floats(min_value=0.0, max_value=1.0)
_hurdle = st.floats(min_value=0.0, max_value=1.0)


@example(price=Decimal("0.50"), q=0.5, hurdle=0.0)
@given(_price, _q, _hurdle)
def test_cost_gate_is_law(price: Decimal, q: float, hurdle: float) -> None:
    candidate = TradeCandidate(
        price=price,
        raw_prob=0.5,
        costs=CostInputs(
            spread=Decimal("0"),
            fee_rate=Decimal("0"),
            gas_usd=Decimal("0"),
            model_error_margin=Decimal("0"),
        ),
        notional_hint=Decimal("10"),
    )
    result = decide(candidate, q, hurdle)
    edge = q - float(price)
    if abs(edge) < hurdle or edge == 0.0:
        assert result.action == "abstain"
    else:
        assert result.action == "act"
        assert result.edge is not None and abs(result.edge) >= hurdle
