# tests/core/signals/test_base.py
import pytest
from pydantic import ValidationError

from core.signals.base import SignalOutput


def test_signal_output_construction() -> None:
    out = SignalOutput(
        p_fair=0.62,
        source="S2",
        rationale="overround 1.05",
        group_id="evt-1",
        overround=1.05,
    )
    assert out.p_fair == 0.62
    assert out.group_id == "evt-1"


def test_signal_output_rejects_out_of_range_prob() -> None:
    with pytest.raises(ValidationError):
        SignalOutput(p_fair=1.5, source="S1", rationale="x")


def test_signal_output_is_frozen() -> None:
    out = SignalOutput(p_fair=0.5, source="S1", rationale="x")
    with pytest.raises(ValidationError):
        out.p_fair = 0.6  # type: ignore[misc]
