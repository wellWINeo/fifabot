import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.signals.devig import devig, overround


def test_overround_sums_values() -> None:
    assert overround([0.5, 0.3, 0.28]) == pytest.approx(1.08)


def test_devig_normalizes_to_one() -> None:
    assert sum(devig([0.5, 0.3, 0.28])) == pytest.approx(1.0)


def test_devig_preserves_fair_two_way() -> None:
    assert devig([0.6, 0.6]) == pytest.approx([0.5, 0.5])


def test_devig_empty_raises() -> None:
    with pytest.raises(ValueError):
        devig([])


def test_devig_nonpositive_raises() -> None:
    with pytest.raises(ValueError):
        devig([0.5, 0.0])


@given(st.lists(st.floats(min_value=1e-6, max_value=10.0), min_size=1, max_size=8))
def test_devig_always_sums_to_one(values: list[float]) -> None:
    assert sum(devig(values)) == pytest.approx(1.0)
