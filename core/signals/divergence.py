# core/signals/divergence.py
"""S1 lag/divergence: compare a Polymarket YES price to a sharp reference fair.

Actionability (is the gap big enough) is the cost gate's job downstream; this
only measures the signed gap and surfaces the reference as the fair estimate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DivergenceResult:
    fair: float
    raw_edge: float


def divergence(pm_yes: float, ref_fair: float) -> DivergenceResult:
    return DivergenceResult(fair=ref_fair, raw_edge=ref_fair - pm_yes)
