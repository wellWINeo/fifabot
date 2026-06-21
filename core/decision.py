"""Pure decision pipeline: calibrate -> cost -> gate -> size.

This is the in-core composition of the pure functions. It is NOT Phase 4
assembly (which wires live signals, IO, and asyncio). No network here.
"""

from __future__ import annotations

from core.calibration import Calibrator
from core.cost_model import round_trip_cost
from core.edge_gate import decide
from core.models import Decision, RiskLimits, TradeCandidate
from core.sizing import size


def evaluate(
    candidate: TradeCandidate, calibrator: Calibrator, limits: RiskLimits
) -> Decision:
    q = calibrator.predict(candidate.raw_prob)
    hurdle = round_trip_cost(candidate.costs, candidate.notional_hint)
    gate = decide(candidate, q, hurdle)
    sizing = size(candidate, gate, limits)
    return Decision(gate=gate, sizing=sizing, prob=q)
