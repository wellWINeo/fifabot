"""Index-based walk-forward splitter over the sorted-event index space [0, n).

Each Split's test window starts exactly where its train window ends, so there is
no overlap and no leakage. Index-based (not timedelta-based) for deterministic,
fence-post-free splitting; the caller maps ranges onto its sorted events.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Split:
    train: range
    test: range


def walk_forward_splits(
    n: int,
    *,
    train_size: int,
    test_size: int,
    step: int | None = None,
    expanding: bool = False,
) -> list[Split]:
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size and test_size must be positive")
    advance = step if step is not None else test_size
    if advance <= 0:
        raise ValueError("step must be positive")

    splits: list[Split] = []
    start = 0
    while True:
        train_end = start + train_size
        test_end = train_end + test_size
        if test_end > n:
            break
        train_start = 0 if expanding else start
        splits.append(
            Split(train=range(train_start, train_end), test=range(train_end, test_end))
        )
        start += advance
    return splits
