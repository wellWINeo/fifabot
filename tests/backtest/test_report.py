"""Walk-forward report: per-split aggregation of P&L and ROI."""

from collections.abc import Mapping
from decimal import Decimal

import pytest

from backtest.engine import BacktestResult
from backtest.report import (
    SignalScore,
    WalkForwardReport,
    aggregate,
    calibration_samples,
    score_signals,
)
from core.models import CalibrationSample


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


def test_calibration_samples_joins_probs_with_outcomes() -> None:
    probs = [("a", 0.8), ("b", 0.3), ("c", 0.5)]
    outcomes: Mapping[str, int] = {"a": 1, "b": 0}  # "c" unlabeled -> dropped
    samples = calibration_samples(probs, outcomes)
    assert samples == [
        CalibrationSample(raw_prob=0.8, outcome=1),
        CalibrationSample(raw_prob=0.3, outcome=0),
    ]


def test_score_signals_perfect_predictions_zero_brier() -> None:
    samples = [
        CalibrationSample(raw_prob=1.0, outcome=1),
        CalibrationSample(raw_prob=0.0, outcome=0),
    ]
    score = score_signals(samples, bins=2)
    assert isinstance(score, SignalScore)
    assert score.brier == 0.0


def test_score_signals_overconfident_wrong_high_brier() -> None:
    samples = [CalibrationSample(raw_prob=1.0, outcome=0)]
    assert score_signals(samples, bins=2).brier == 1.0
