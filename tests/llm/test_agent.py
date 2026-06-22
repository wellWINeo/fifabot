from llm.agent import HypothesisAgent, MarketFeatures
from llm.schema import HypothesisOutput


def _features() -> MarketFeatures:
    return MarketFeatures(market_id="m", yes_price=0.5, reference_fair=0.6)


def test_agent_returns_valid_hypothesis() -> None:
    def runner(f: MarketFeatures) -> object:
        return HypothesisOutput(p_fair=0.6, confidence=0.7, rationale="ref higher")

    agent = HypothesisAgent(runner)
    out = agent.hypothesize(_features())
    assert out is not None and out.p_fair == 0.6


def test_agent_validates_dict_output() -> None:
    def runner(f: MarketFeatures) -> object:
        return {"p_fair": 0.55, "confidence": 0.4, "rationale": "ok"}

    assert HypothesisAgent(runner).hypothesize(_features()) == HypothesisOutput(
        p_fair=0.55, confidence=0.4, rationale="ok"
    )


def test_agent_malformed_output_returns_none_not_raise() -> None:
    def bad(f: MarketFeatures) -> object:
        return {"p_fair": 9.9, "confidence": "nope"}  # out of range / wrong type

    assert HypothesisAgent(bad).hypothesize(_features()) is None


def test_agent_runner_exception_returns_none() -> None:
    def boom(f: MarketFeatures) -> object:
        raise RuntimeError("model timeout")

    assert HypothesisAgent(boom).hypothesize(_features()) is None
