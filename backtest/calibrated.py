"""Walk-forward calibration: fit one calibrator per split on the TRAIN window only.

A calibrator is a fitted transform, so fitting it on test-window outcomes is
look-ahead. We recover raw p_fair by running the train window through an identity
calibrator, join train-window outcomes, fit a fresh calibrator, then evaluate the
test window with it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from backtest.engine import BacktestResult, replay
from backtest.report import calibration_samples
from backtest.strategy import Strategy
from backtest.walkforward import Split
from core.calibration import Calibrator
from core.models import CalibrationSample, RiskLimits
from data.events import MarketEvent

StrategyFactory = Callable[[Calibrator], Strategy]


class _Identity:
    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        return None

    def predict(self, raw: float) -> float:
        return raw


def fit_split_calibrator(
    train_events: Sequence[MarketEvent],
    make_strategy: StrategyFactory,
    outcomes: Mapping[str, int],
    make_calibrator: Callable[[], Calibrator],
    limits: RiskLimits,
) -> Calibrator:
    raw_result = replay(train_events, make_strategy(_Identity()), limits)
    samples = calibration_samples(raw_result.signal_probs, outcomes)
    calibrator = make_calibrator()
    calibrator.fit(samples)
    return calibrator


def run_walk_forward(
    events: Sequence[MarketEvent],
    splits: Sequence[Split],
    make_strategy: StrategyFactory,
    outcomes: Mapping[str, int],
    make_calibrator: Callable[[], Calibrator],
    limits: RiskLimits,
) -> list[BacktestResult]:
    results: list[BacktestResult] = []
    for split in splits:
        train = list(events[split.train.start : split.train.stop])
        test = list(events[split.test.start : split.test.stop])
        calibrator = fit_split_calibrator(
            train, make_strategy, outcomes, make_calibrator, limits
        )
        results.append(replay(test, make_strategy(calibrator), limits))
    return results
