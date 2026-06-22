"""Shadow S3 hypothesis agent. Model-agnostic and injectable so tests never hit
a live model. Malformed or raised output yields None (abstain) -- an unpromoted
research signal must never crash the trading loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from llm.schema import HypothesisOutput


@dataclass(frozen=True)
class MarketFeatures:
    market_id: str
    yes_price: float
    reference_fair: float | None


ModelRunner = Callable[[MarketFeatures], object]


class HypothesisAgent:
    def __init__(self, runner: ModelRunner) -> None:
        self._runner = runner

    def hypothesize(self, features: MarketFeatures) -> HypothesisOutput | None:
        try:
            raw = self._runner(features)
            if isinstance(raw, HypothesisOutput):
                return raw
            return HypothesisOutput.model_validate(raw)
        except (ValidationError, ValueError, TypeError, RuntimeError):
            return None


def build_pydantic_ai_runner(model: str) -> ModelRunner:
    """Live runner backed by pydantic-ai. Human-run; never exercised in tests."""
    from pydantic_ai import Agent

    agent: Agent[None, HypothesisOutput] = Agent(model, output_type=HypothesisOutput)

    def runner(features: MarketFeatures) -> object:
        prompt = (
            f"Market {features.market_id}: Polymarket YES={features.yes_price}, "
            f"reference fair={features.reference_fair}. Estimate the fair YES "
            "probability with confidence and a one-line rationale."
        )
        return agent.run_sync(prompt).output

    return runner
