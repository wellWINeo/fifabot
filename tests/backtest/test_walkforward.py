"""Walk-forward splitter: ordered, non-overlapping, no leakage (property)."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from backtest.walkforward import Split, walk_forward_splits


def test_rolling_splits_basic() -> None:
    splits = walk_forward_splits(10, train_size=4, test_size=2)
    assert splits[0] == Split(train=range(0, 4), test=range(4, 6))
    assert splits[1] == Split(train=range(2, 6), test=range(6, 8))
    assert splits[2] == Split(train=range(4, 8), test=range(8, 10))
    assert len(splits) == 3


def test_expanding_anchors_train_at_zero() -> None:
    splits = walk_forward_splits(10, train_size=4, test_size=2, expanding=True)
    assert all(s.train.start == 0 for s in splits)
    assert splits[1].train == range(0, 6)


def test_rejects_nonpositive_sizes() -> None:
    with pytest.raises(ValueError):
        walk_forward_splits(10, train_size=0, test_size=2)


@given(
    n=st.integers(min_value=0, max_value=200),
    train_size=st.integers(min_value=1, max_value=50),
    test_size=st.integers(min_value=1, max_value=50),
)
def test_splits_are_ordered_and_leak_free(
    n: int, train_size: int, test_size: int
) -> None:
    splits = walk_forward_splits(n, train_size=train_size, test_size=test_size)
    for s in splits:
        assert s.test.start == s.train.stop
        assert s.train.start >= 0
        assert s.test.stop <= n
        assert len(s.train) == train_size
        assert len(s.test) == test_size
    for earlier, later in zip(splits, splits[1:], strict=False):
        assert later.test.start == earlier.test.stop
