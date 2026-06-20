"""Scoring and P&L metrics. Float for scores; Decimal for cash (exact)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from core.models import Fill


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes length mismatch")
    if not probs:
        raise ValueError("empty input")
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes, strict=True)) / len(probs)


def calibration_curve(
    probs: Sequence[float], outcomes: Sequence[int], bins: int
) -> list[tuple[float, float, int]]:
    if bins <= 0:
        raise ValueError("bins must be positive")
    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes length mismatch")
    buckets: list[list[tuple[float, int]]] = [[] for _ in range(bins)]
    for p, o in zip(probs, outcomes, strict=True):
        idx = min(bins - 1, int(p * bins))
        buckets[idx].append((p, o))
    curve: list[tuple[float, float, int]] = []
    for bucket in buckets:
        if not bucket:
            continue
        n = len(bucket)
        mean_pred = sum(p for p, _ in bucket) / n
        mean_obs = sum(o for _, o in bucket) / n
        curve.append((mean_pred, mean_obs, n))
    return curve


def realized_pnl(fills: Sequence[Fill]) -> Decimal:
    total = Decimal(0)
    for f in fills:
        total += (f.exit_price - f.entry_price) * f.shares - f.costs_usd
    return total


def roi(pnl: Decimal, deployed: Decimal) -> float:
    if deployed <= 0:
        raise ValueError("deployed must be positive")
    return float(pnl / deployed)
