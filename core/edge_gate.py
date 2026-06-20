"""Edge gate: abstain-by-default. Acts only when the edge clears the hurdle."""

from __future__ import annotations

from core.models import GateResult, Side, TradeCandidate


def decide(candidate: TradeCandidate, q: float, hurdle: float) -> GateResult:
    """Decide whether to act on a candidate given calibrated prob q and hurdle.

    The cost-gate law: never act when abs(edge) < hurdle. A zero edge is also
    treated as abstain even if the hurdle is zero — there is no expected
    profit to act on.
    """
    edge = q - float(candidate.price)
    if abs(edge) < hurdle or edge == 0.0:
        return GateResult.abstain(reason=f"edge {edge:.4f} below hurdle {hurdle:.4f}")
    side = Side.BUY_YES if edge > 0 else Side.BUY_NO
    return GateResult.act(side=side, edge=edge)
