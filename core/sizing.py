"""Position sizing: fractional Kelly with hard caps. Abstain = zero."""

from __future__ import annotations

from decimal import Decimal

from core.models import GateResult, RiskLimits, Side, SizingResult, TradeCandidate


def kelly_fraction(q: float, p: float, side: Side) -> float:
    """Full-Kelly fraction of bankroll for the bought token, clamped to [0, 1].

    Requires 0 < p < 1 (the price of the bought token).
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0, 1), got {p}")
    f = (q - p) / (1.0 - p) if side is Side.BUY_YES else (p - q) / p
    return max(0.0, min(1.0, f))


def size(
    candidate: TradeCandidate, gate: GateResult, limits: RiskLimits
) -> SizingResult:
    """Size a position from a gate decision under fractional Kelly + hard caps."""
    if gate.action == "abstain":
        return SizingResult(stake_usd=Decimal(0), shares=Decimal(0), binding_cap=None)

    # GateResult's model validator guarantees side/edge are set when action
    # is "act"; this narrows the type for mypy rather than guarding real input.
    if gate.side is None or gate.edge is None:
        raise AssertionError("act gate must have side and edge")
    p = float(candidate.price)
    q = gate.edge + p  # edge == q - p regardless of side
    f_star = kelly_fraction(q, p, gate.side)

    stake = Decimal(str(f_star * limits.kelly_fraction)) * limits.bankroll
    binding_cap: str | None = None
    caps = (
        (
            "max_position_fraction",
            Decimal(str(limits.max_position_fraction)) * limits.bankroll,
        ),
        ("max_position_usd", limits.max_position_usd),
        ("bankroll", limits.bankroll),
    )
    for name, cap in caps:
        if stake > cap:
            stake = cap
            binding_cap = name

    entry_price = (
        candidate.price if gate.side is Side.BUY_YES else Decimal(1) - candidate.price
    )
    shares = stake / entry_price
    return SizingResult(stake_usd=stake, shares=shares, binding_cap=binding_cap)
