"""Walk-forward report: per-split aggregation of P&L and ROI."""

from decimal import Decimal

import pytest

from backtest.engine import BacktestResult
from backtest.report import WalkForwardReport, aggregate


def _result(pnl: str, roi_: float) -> BacktestResult:
    return BacktestResult(fills=(), realized_pnl=Decimal(pnl), roi=roi_)


def test_aggregate_sums_pnl_and_means_roi() -> None:
    report = aggregate([_result("1.50", 0.06), _result("-0.50", -0.02)])
    assert isinstance(report, WalkForwardReport)
    assert report.per_split_pnl == (Decimal("1.50"), Decimal("-0.50"))
    assert report.total_pnl == Decimal("1.00")
    assert report.mean_roi == pytest.approx(0.02)


def test_aggregate_empty() -> None:
    report = aggregate([])
    assert report.total_pnl == Decimal("0")
    assert report.mean_roi == 0.0
    assert report.per_split_pnl == ()
