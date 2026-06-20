"""Metrics: Brier, calibration curve, P&L accounting balances (property)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.metrics import brier_score, calibration_curve, realized_pnl, roi
from core.models import Fill, Side


def test_brier_score_perfect_is_zero() -> None:
    assert brier_score([1.0, 0.0], [1, 0]) == 0.0


def test_brier_score_known_value() -> None:
    # ((0.5-1)^2 + (0.5-0)^2)/2 = 0.25
    assert brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)


def test_calibration_curve_bins() -> None:
    curve = calibration_curve([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1], bins=2)
    assert len(curve) == 2
    lo_pred, lo_obs, lo_n = curve[0]
    assert lo_obs == 0.0 and lo_n == 2
    hi_pred, hi_obs, hi_n = curve[1]
    assert hi_obs == 1.0 and hi_n == 2


def test_brier_score_length_mismatch() -> None:
    with pytest.raises(ValueError):
        brier_score([0.5], [1, 0])


def test_brier_score_empty() -> None:
    with pytest.raises(ValueError):
        brier_score([], [])


def test_calibration_curve_zero_bins() -> None:
    with pytest.raises(ValueError):
        calibration_curve([0.5], [1], bins=0)


def test_calibration_curve_skips_empty_bins() -> None:
    # All values land in the first bin of 5; the other 4 bins are empty
    # and must be omitted from the result rather than appearing as zeros.
    curve = calibration_curve([0.05, 0.1], [0, 1], bins=5)
    assert len(curve) == 1
    mean_pred, mean_obs, n = curve[0]
    assert n == 2
    assert mean_pred == pytest.approx(0.075)
    assert mean_obs == pytest.approx(0.5)


def test_calibration_curve_length_mismatch() -> None:
    with pytest.raises(ValueError):
        calibration_curve([0.5], [1, 0], bins=2)


def test_roi_nonpositive_deployed() -> None:
    with pytest.raises(ValueError):
        roi(Decimal("5"), Decimal("0"))


def test_roi() -> None:
    assert roi(Decimal("5"), Decimal("25")) == pytest.approx(0.2)


def test_round_trip_at_same_price_loses_only_costs() -> None:
    fill = Fill(
        side=Side.BUY_YES,
        entry_price=Decimal("0.50"),
        exit_price=Decimal("0.50"),
        shares=Decimal("10"),
        costs_usd=Decimal("0.30"),
    )
    assert realized_pnl([fill]) == Decimal("-0.30")


_price = st.integers(min_value=1, max_value=99).map(lambda n: Decimal(n) / 100)
_shares = st.integers(min_value=0, max_value=100).map(Decimal)
_costs = st.integers(min_value=0, max_value=500).map(lambda c: Decimal(c) / 100)


@given(
    entry=_price,
    exit_=_price,
    shares=_shares,
    costs=_costs,
    bankroll_start=st.integers(min_value=1, max_value=1000).map(Decimal),
)
def test_pnl_accounting_balances(
    entry: Decimal,
    exit_: Decimal,
    shares: Decimal,
    costs: Decimal,
    bankroll_start: Decimal,
) -> None:
    fill = Fill(
        side=Side.BUY_YES,
        entry_price=entry,
        exit_price=exit_,
        shares=shares,
        costs_usd=costs,
    )
    pnl = realized_pnl([fill])
    # Cashflow: pay cost basis + fees on entry, receive proceeds on exit.
    bankroll_end = bankroll_start - entry * shares - costs + exit_ * shares
    assert bankroll_end == bankroll_start + pnl  # exact Decimal equality


@given(st.lists(st.tuples(_price, _price, _shares, _costs), max_size=5))
def test_realized_pnl_is_additive(
    rows: list[tuple[Decimal, Decimal, Decimal, Decimal]],
) -> None:
    fills = [
        Fill(
            side=Side.BUY_YES,
            entry_price=e,
            exit_price=x,
            shares=s,
            costs_usd=c,
        )
        for e, x, s, c in rows
    ]
    total = realized_pnl(fills)
    piecewise = sum((realized_pnl([f]) for f in fills), Decimal(0))
    assert total == piecewise
