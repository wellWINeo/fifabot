"""Thin per-split aggregation of backtest results into a walk-forward report.

Reporting only — no new financial logic. Brier aggregation arrives once signals
emit probabilities (Phase 3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from backtest.engine import BacktestResult
from core.metrics import brier_score, calibration_curve
from core.models import CalibrationSample


@dataclass(frozen=True)
class WalkForwardReport:
    per_split_pnl: tuple[Decimal, ...]
    total_pnl: Decimal
    mean_roi: float


def aggregate(results: Sequence[BacktestResult]) -> WalkForwardReport:
    per_split_pnl = tuple(r.realized_pnl for r in results)
    total_pnl = sum(per_split_pnl, Decimal(0))
    mean_roi = sum(r.roi for r in results) / len(results) if results else 0.0
    return WalkForwardReport(
        per_split_pnl=per_split_pnl,
        total_pnl=total_pnl,
        mean_roi=mean_roi,
    )


@dataclass(frozen=True)
class SignalScore:
    brier: float
    curve: list[tuple[float, float, int]]


def calibration_samples(
    signal_probs: Sequence[tuple[str, float]], outcomes: Mapping[str, int]
) -> list[CalibrationSample]:
    """Join recorded (market_id, prob) with resolved 0/1 outcomes.

    Markets without an outcome label are dropped -- outcomes come from match
    resolution and are used only post-hoc, never during a decision.
    """
    return [
        CalibrationSample(raw_prob=prob, outcome=outcomes[market_id])
        for market_id, prob in signal_probs
        if market_id in outcomes
    ]


def score_signals(
    samples: Sequence[CalibrationSample], *, bins: int = 10
) -> SignalScore:
    probs = [s.raw_prob for s in samples]
    obs = [s.outcome for s in samples]
    return SignalScore(
        brier=brier_score(probs, obs), curve=calibration_curve(probs, obs, bins)
    )
