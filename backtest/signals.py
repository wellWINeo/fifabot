# backtest/signals.py
"""Phase 3 signal strategies: thin Strategy wrappers over the pure core.

They read the as-of MarketView, call the pure signal math, build a
TradeCandidate, and run the Phase 1 decision pipeline. No financial logic lives
here -- gating/sizing stay the core's job.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from backtest.strategy import Strategy
from backtest.view import MarketView
from core.calibration import Calibrator
from core.decision import evaluate
from core.models import CostInputs, Decision, RiskLimits, Side, TradeCandidate
from core.signals.consistency import scan_consistency
from core.signals.divergence import divergence
from data.events import MarketEvent, MarketGroup
from llm.agent import HypothesisAgent, MarketFeatures


class DivergenceStrategy:
    """S1: act when Polymarket diverges from the sharp reference fair price."""

    def __init__(
        self,
        *,
        costs: CostInputs,
        notional_hint: Decimal,
        calibrator: Calibrator,
        limits: RiskLimits,
    ) -> None:
        self._costs = costs
        self._notional = notional_hint
        self._calibrator = calibrator
        self._limits = limits

    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        ref = view.reference_at(event.market_id, event.ts)
        if ref is None:
            return None
        result = divergence(float(event.quote.price), float(ref))
        candidate = TradeCandidate(
            price=event.quote.price,
            raw_prob=result.fair,
            costs=self._costs,
            notional_hint=self._notional,
        )
        return evaluate(candidate, self._calibrator, self._limits)


class ConsistencyStrategy:
    """S2: de-vig a market's mutually-exclusive group; act on per-leg mispricing."""

    def __init__(
        self,
        *,
        groups: Sequence[MarketGroup],
        costs: CostInputs,
        notional_hint: Decimal,
        calibrator: Calibrator,
        limits: RiskLimits,
    ) -> None:
        self._group_of: dict[str, MarketGroup] = {}
        for group in groups:
            for market_id in group.market_ids:
                self._group_of[market_id] = group
        self._costs = costs
        self._notional = notional_hint
        self._calibrator = calibrator
        self._limits = limits

    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        group = self._group_of.get(event.market_id)
        if group is None:
            return None
        prices: list[float] = []
        for market_id in group.market_ids:
            price = view.latest_price(market_id)
            if price is None:
                return None
            prices.append(float(price))
        result = scan_consistency(prices)
        idx = group.market_ids.index(event.market_id)
        candidate = TradeCandidate(
            price=event.quote.price,
            raw_prob=result.fair_legs[idx],
            costs=self._costs,
            notional_hint=self._notional,
        )
        return evaluate(candidate, self._calibrator, self._limits)


@dataclass(frozen=True)
class NamedSignal:
    source: str
    strategy: Strategy
    promoted: bool


@dataclass(frozen=True)
class SignalDecision:
    source: str
    market_id: str
    ts: datetime
    action: str
    side: Side | None
    p_fair: float | None
    promoted: bool
    agreement: bool


def _acting_side(decision: Decision | None) -> Side | None:
    if decision is None or decision.gate.action != "act":
        return None
    return decision.gate.side


class CompositeStrategy:
    """Priority-precedence composer: first promoted non-abstain signal acts.

    Every sub-signal's decision is logged (for walk-forward Brier/EV evidence),
    but only a promoted signal may produce the acting decision. Unpromoted (S3
    shadow) signals are recorded, never traded.
    """

    def __init__(
        self,
        signals: Sequence[NamedSignal],
        log: list[SignalDecision] | None = None,
    ) -> None:
        self._signals = list(signals)
        self._log = log

    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        evaluated: list[tuple[NamedSignal, Decision | None]] = [
            (sig, sig.strategy.on_event(event, view)) for sig in self._signals
        ]
        sides = [s for _, d in evaluated if (s := _acting_side(d)) is not None]
        agreement = any(sides.count(s) >= 2 for s in sides)

        if self._log is not None:
            for sig, decision in evaluated:
                action = "act" if _acting_side(decision) is not None else "abstain"
                self._log.append(
                    SignalDecision(
                        source=sig.source,
                        market_id=event.market_id,
                        ts=event.ts,
                        action=action,
                        side=_acting_side(decision),
                        p_fair=decision.prob if decision is not None else None,
                        promoted=sig.promoted,
                        agreement=agreement,
                    )
                )

        for sig, decision in evaluated:
            if sig.promoted and _acting_side(decision) is not None:
                return decision
        return None


class ShadowForecastStrategy:
    """S3 forecast via the hypothesis agent. Always composed as UNPROMOTED:
    it produces a calibrated Decision for logging but never opens a position.
    """

    def __init__(
        self,
        *,
        agent: HypothesisAgent,
        costs: CostInputs,
        notional_hint: Decimal,
        calibrator: Calibrator,
        limits: RiskLimits,
    ) -> None:
        self._agent = agent
        self._costs = costs
        self._notional = notional_hint
        self._calibrator = calibrator
        self._limits = limits

    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        ref = view.reference_at(event.market_id, event.ts)
        features = MarketFeatures(
            market_id=event.market_id,
            yes_price=float(event.quote.price),
            reference_fair=float(ref) if ref is not None else None,
        )
        hypothesis = self._agent.hypothesize(features)
        if hypothesis is None:
            return None
        candidate = TradeCandidate(
            price=event.quote.price,
            raw_prob=hypothesis.p_fair,
            costs=self._costs,
            notional_hint=self._notional,
        )
        return evaluate(candidate, self._calibrator, self._limits)
