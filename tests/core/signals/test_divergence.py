# tests/core/signals/test_divergence.py
import pytest

from core.signals.divergence import divergence


def test_positive_edge_when_pm_underprices() -> None:
    result = divergence(pm_yes=0.50, ref_fair=0.62)
    assert result.fair == 0.62
    assert result.raw_edge == pytest.approx(0.12)


def test_negative_edge_when_pm_overprices() -> None:
    assert divergence(pm_yes=0.70, ref_fair=0.60).raw_edge == pytest.approx(-0.10)


def test_zero_edge_when_aligned() -> None:
    assert divergence(pm_yes=0.55, ref_fair=0.55).raw_edge == pytest.approx(0.0)
