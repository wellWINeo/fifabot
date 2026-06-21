# tests/core/signals/test_consistency.py
import pytest

from core.signals.consistency import scan_consistency


def test_overround_basket_flagged() -> None:
    result = scan_consistency([0.50, 0.30, 0.28])
    assert result.overround == pytest.approx(1.08)
    assert sum(result.fair_legs) == pytest.approx(1.0)


def test_fair_legs_index_aligned() -> None:
    result = scan_consistency([0.50, 0.30, 0.28])
    assert result.fair_legs[2] < result.fair_legs[1] < result.fair_legs[0]


def test_balanced_group_overround_near_one() -> None:
    result = scan_consistency([0.34, 0.33, 0.33])
    assert result.overround == pytest.approx(1.0)
    assert result.fair_legs == pytest.approx([0.34, 0.33, 0.33])
