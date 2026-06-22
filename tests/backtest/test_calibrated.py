from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backtest.calibrated import fit_split_calibrator, run_walk_forward
from backtest.engine import BacktestResult
from backtest.view import MarketView
from backtest.walkforward import Split
from core.calibration import Calibrator
from core.models import (
    CalibrationSample,
    Decision,
    GateResult,
    RiskLimits,
    SizingResult,
)
from data.events import MarketEvent, Quote, event_from_quote

_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def _limits() -> RiskLimits:
    return RiskLimits(
        bankroll=Decimal("25"),
        kelly_fraction=0.25,
        max_position_fraction=0.2,
        max_position_usd=Decimal("5"),
    )


def _events(probs: Sequence[float]) -> list[MarketEvent]:
    return [
        event_from_quote(
            Quote(
                market_id=f"m{i}",
                ts=_T0 + timedelta(minutes=i),
                price=Decimal("0.50"),
            )
        )
        for i, _ in enumerate(probs)
    ]


class _EmitProb:
    """Strategy double: emits a per-market p_fair as the Decision.prob."""

    def __init__(self, calibrator: Calibrator, probs: Mapping[str, float]) -> None:
        self._cal = calibrator
        self._probs = probs

    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        raw = self._probs.get(event.market_id)
        if raw is None:
            return None
        q = self._cal.predict(raw)
        return Decision(
            gate=GateResult.abstain(reason="probe"),
            sizing=SizingResult(stake_usd=Decimal("0"), shares=Decimal("0")),
            prob=q,
        )


class _SpyCalibrator:
    def __init__(self) -> None:
        self.fitted_raw: list[float] = []

    def fit(self, samples: Sequence[CalibrationSample]) -> None:
        self.fitted_raw.extend(s.raw_prob for s in samples)

    def predict(self, raw: float) -> float:
        return raw


def test_calibrator_fit_only_on_train_window_probs() -> None:
    # train markets emit 0.20; test markets emit 0.80. The fit must never see 0.80.
    probs = {"m0": 0.20, "m1": 0.20, "m2": 0.80, "m3": 0.80}
    events = _events([0.2, 0.2, 0.8, 0.8])
    outcomes: Mapping[str, int] = {"m0": 0, "m1": 0, "m2": 1, "m3": 1}
    spy = _SpyCalibrator()

    train = [e for e in events if e.market_id in ("m0", "m1")]
    fit_split_calibrator(
        train,
        make_strategy=lambda cal: _EmitProb(cal, probs),
        outcomes=outcomes,
        make_calibrator=lambda: spy,
        limits=_limits(),
    )
    assert spy.fitted_raw == [0.20, 0.20]
    assert 0.80 not in spy.fitted_raw  # no test-window leakage


def test_run_walk_forward_returns_one_result_per_split() -> None:
    probs = {f"m{i}": 0.5 for i in range(4)}
    events = _events([0.5] * 4)
    outcomes: Mapping[str, int] = {f"m{i}": i % 2 for i in range(4)}
    splits = [Split(train=range(0, 2), test=range(2, 4))]
    results = run_walk_forward(
        events,
        splits,
        make_strategy=lambda cal: _EmitProb(cal, probs),
        outcomes=outcomes,
        make_calibrator=lambda: _SpyCalibrator(),
        limits=_limits(),
    )
    assert len(results) == 1
    assert isinstance(results[0], BacktestResult)
