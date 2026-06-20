"""Probability calibration: isotonic and Platt (logistic), behind one Protocol."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from core.models import CalibrationSample


class Calibrator(Protocol):
    def fit(self, samples: Sequence[CalibrationSample]) -> None: ...
    def predict(self, raw: float) -> float: ...


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


class IsotonicCalibrator:
    def __init__(self) -> None:
        self._model: IsotonicRegression | None = None

    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        x = np.array([s.raw_prob for s in samples], dtype=float)
        y = np.array([s.outcome for s in samples], dtype=float)
        model = IsotonicRegression(
            y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip"
        )
        model.fit(x, y)
        self._model = model

    def predict(self, raw: float) -> float:
        if self._model is None:
            raise RuntimeError("calibrator is not fitted")
        return _clip(float(self._model.predict([raw])[0]))


class PlattCalibrator:
    def __init__(self) -> None:
        self._model: LogisticRegression | None = None

    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        x = np.array([[s.raw_prob] for s in samples], dtype=float)
        y = np.array([s.outcome for s in samples], dtype=int)
        model = LogisticRegression()
        model.fit(x, y)
        self._model = model

    def predict(self, raw: float) -> float:
        if self._model is None:
            raise RuntimeError("calibrator is not fitted")
        return _clip(float(self._model.predict_proba([[raw]])[0, 1]))
