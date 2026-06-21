# tests/llm/test_schema.py
import pytest
from pydantic import ValidationError

from llm.schema import HypothesisOutput


def test_valid_output_parses() -> None:
    out = HypothesisOutput.model_validate(
        {"p_fair": 0.61, "confidence": 0.4, "rationale": "lineup news"}
    )
    assert out.p_fair == 0.61


def test_missing_field_rejected() -> None:
    with pytest.raises(ValidationError):
        HypothesisOutput.model_validate({"p_fair": 0.61, "confidence": 0.4})


def test_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        HypothesisOutput.model_validate(
            {"p_fair": 0.6, "confidence": 0.4, "rationale": "x", "stray": 1}
        )


def test_out_of_range_prob_rejected() -> None:
    with pytest.raises(ValidationError):
        HypothesisOutput.model_validate(
            {"p_fair": 1.4, "confidence": 0.4, "rationale": "x"}
        )
