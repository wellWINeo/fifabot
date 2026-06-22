from datetime import UTC, datetime
from decimal import Decimal

from backtest.signals import CompositeStrategy, NamedSignal, SignalDecision
from backtest.view import MarketView
from core.models import Decision, GateResult, Side, SizingResult
from data.events import MarketEvent, Quote, event_from_quote

_T0 = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


def _event() -> MarketEvent:
    return event_from_quote(Quote(market_id="m", ts=_T0, price=Decimal("0.50")))


def _act(side: Side, prob: float) -> Decision:
    return Decision(
        gate=GateResult.act(side=side, edge=0.1),
        sizing=SizingResult(stake_usd=Decimal("4"), shares=Decimal("8")),
        prob=prob,
    )


def _abstain() -> Decision:
    return Decision(
        gate=GateResult.abstain(reason="aligned"),
        sizing=SizingResult(stake_usd=Decimal("0"), shares=Decimal("0")),
        prob=0.5,
    )


class _Fixed:
    def __init__(self, decision: Decision | None) -> None:
        self._decision = decision

    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None:
        return self._decision


def _view() -> MarketView:
    return MarketView(_T0, {"m": [_event().quote]}, None)


def test_precedence_first_promoted_acting_wins() -> None:
    log: list[SignalDecision] = []
    composite = CompositeStrategy(
        [
            NamedSignal("S2", _Fixed(_abstain()), promoted=True),
            NamedSignal("S1", _Fixed(_act(Side.BUY_YES, 0.7)), promoted=True),
            NamedSignal("S3", _Fixed(_act(Side.BUY_NO, 0.2)), promoted=False),
        ],
        log=log,
    )
    decision = composite.on_event(_event(), _view())
    assert decision is not None and decision.gate.side is Side.BUY_YES  # S1 wins
    assert [r.source for r in log] == ["S2", "S1", "S3"]


def test_unpromoted_signal_logs_but_never_acts() -> None:
    log: list[SignalDecision] = []
    composite = CompositeStrategy(
        [
            NamedSignal("S2", _Fixed(_abstain()), promoted=True),
            NamedSignal("S3", _Fixed(_act(Side.BUY_YES, 0.9)), promoted=False),
        ],
        log=log,
    )
    assert composite.on_event(_event(), _view()) is None  # S3 cannot act
    s3 = next(r for r in log if r.source == "S3")
    assert s3.action == "act" and s3.promoted is False


def test_agreement_flag_set_when_two_signals_share_side() -> None:
    log: list[SignalDecision] = []
    composite = CompositeStrategy(
        [
            NamedSignal("S2", _Fixed(_act(Side.BUY_YES, 0.7)), promoted=True),
            NamedSignal("S1", _Fixed(_act(Side.BUY_YES, 0.65)), promoted=True),
        ],
        log=log,
    )
    composite.on_event(_event(), _view())
    assert all(r.agreement for r in log)


def test_all_abstain_returns_none() -> None:
    composite = CompositeStrategy(
        [
            NamedSignal("S2", _Fixed(_abstain()), promoted=True),
            NamedSignal("S1", _Fixed(None), promoted=True),
        ]
    )
    assert composite.on_event(_event(), _view()) is None
