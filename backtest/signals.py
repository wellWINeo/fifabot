# backtest/signals.py
"""Phase 3 signal strategies: thin Strategy wrappers over the pure core.

They read the as-of MarketView, call the pure signal math, build a
TradeCandidate, and run the Phase 1 decision pipeline. No financial logic lives
here -- gating/sizing stay the core's job.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from backtest.view import MarketView
from core.calibration import Calibrator
from core.decision import evaluate
from core.models import CostInputs, Decision, RiskLimits, TradeCandidate
from core.signals.consistency import scan_consistency
from core.signals.divergence import divergence
from data.events import MarketEvent, MarketGroup


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
