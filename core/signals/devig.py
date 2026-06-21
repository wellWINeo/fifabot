"""De-vig primitive: normalize values that should sum to 1.0.

Shared by S2 (Polymarket linked-group YES prices) and, in Phase 4, the
reference-odds adapter (decimal book odds passed as 1/odds). Pure float math.
"""

from __future__ import annotations

from collections.abc import Sequence


def overround(values: Sequence[float]) -> float:
    """Sum of values that should total ~1.0; deviation is the arbitrage edge."""
    if not values:
        raise ValueError("values must be non-empty")
    if any(v <= 0.0 for v in values):
        raise ValueError("values must be positive")
    return float(sum(values))


def devig(values: Sequence[float]) -> list[float]:
    """Normalize values to sum to 1.0, preserving their ratios."""
    total = overround(values)
    return [v / total for v in values]
