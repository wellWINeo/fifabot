# core/signals/consistency.py
"""S2 cross-market consistency: de-vig a mutually-exclusive group's YES prices.

`overround` deviating from 1.0 is the arbitrage edge; `fair_legs` are the
normalized per-leg fair probabilities, index-aligned with the input prices.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from core.signals.devig import devig, overround


@dataclass(frozen=True)
class ConsistencyResult:
    overround: float
    fair_legs: list[float]


def scan_consistency(yes_prices: Sequence[float]) -> ConsistencyResult:
    return ConsistencyResult(
        overround=overround(yes_prices), fair_legs=devig(yes_prices)
    )
