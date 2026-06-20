"""Thin per-split aggregation of backtest results into a walk-forward report.

Reporting only — no new financial logic. Brier aggregation arrives once signals
emit probabilities (Phase 3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from backtest.engine import BacktestResult


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
