"""Calibration: fitted-guard, range, monotonicity, Brier reduction."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from core.calibration import IsotonicCalibrator, PlattCalibrator
from core.models import CalibrationSample


def _brier(probs: list[float], outcomes: list[int]) -> float:
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes, strict=True)) / len(probs)


def _monotone_samples() -> list[CalibrationSample]:
    samples: list[CalibrationSample] = []
    for i in range(200):
        raw = i / 199
        outcome = 1 if (i % 100) < int(raw * 100) else 0
        samples.append(CalibrationSample(raw_prob=raw, outcome=outcome))
    return samples


@pytest.mark.parametrize("cls", [IsotonicCalibrator, PlattCalibrator])
def test_predict_before_fit_raises(cls: type) -> None:
    with pytest.raises(RuntimeError):
        cls().predict(0.5)


@pytest.mark.parametrize("cls", [IsotonicCalibrator, PlattCalibrator])
def test_predict_in_unit_range(cls: type) -> None:
    cal = cls()
    cal.fit(_monotone_samples())
    for raw in (0.0, 0.25, 0.5, 0.75, 1.0):
        out = cal.predict(raw)
        assert 0.0 <= out <= 1.0


@pytest.mark.parametrize("cls", [IsotonicCalibrator, PlattCalibrator])
@given(
    a=st.floats(min_value=0.0, max_value=1.0),
    b=st.floats(min_value=0.0, max_value=1.0),
)
def test_monotonic(cls: type, a: float, b: float) -> None:
    cal = cls()
    cal.fit(_monotone_samples())
    lo, hi = sorted((a, b))
    assert cal.predict(lo) <= cal.predict(hi) + 1e-9


def test_isotonic_reduces_brier_on_overconfident_inputs() -> None:
    samples: list[CalibrationSample] = []
    raws: list[float] = []
    outcomes: list[int] = []
    for i in range(400):
        outcome = i % 2  # exactly 50% base rate
        raw = 0.95 if outcome == 1 else 0.05  # overconfident, but correct direction
        # Flip a known fraction so raw is not perfectly separating.
        if i % 5 == 0:
            outcome = 1 - outcome
        samples.append(CalibrationSample(raw_prob=raw, outcome=outcome))
        raws.append(raw)
        outcomes.append(outcome)

    cal = IsotonicCalibrator()
    cal.fit(samples)
    calibrated = [cal.predict(r) for r in raws]

    assert _brier(calibrated, outcomes) <= _brier(raws, outcomes)
