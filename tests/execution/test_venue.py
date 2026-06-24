from datetime import UTC, datetime
from decimal import Decimal

from core.models import Side
from core.risk import RiskConfig, RiskState
from data.events import Market
from execution.client import FakeExecutionClient
from execution.orders import OrderRequest
from execution.venue import ClobVenue, SimulatedVenue

_T0 = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)


def _market() -> Market:
    return Market(
        market_id="m",
        question="q",
        token_ids=("yes", "no"),
        tick_size=Decimal("0.01"),
        minimum_order_size=Decimal("5"),
    )


def _config(**overrides: object) -> RiskConfig:
    base: dict[str, object] = dict(
        max_position_usd=Decimal("100"),
        max_daily_loss_usd=Decimal("100"),
        max_orders_per_run=10,
        resubmit_window_seconds=60.0,
        max_orders_per_market_in_window=10,
    )
    base.update(overrides)
    return RiskConfig(**base)


def _order(price: str = "0.40", size: str = "10") -> OrderRequest:
    return OrderRequest(
        market_id="m",
        token_id="yes",
        side=Side.BUY_YES,
        price=Decimal(price),
        size=Decimal(size),
    )


def test_clob_venue_places_correct_payload() -> None:
    client = FakeExecutionClient()
    state, result = ClobVenue(client).place(
        _order(), _market(), RiskState.start(_T0), _config(), _T0
    )
    assert result.status == "placed"
    assert result.order_id == "ord-1"
    assert len(client.placed) == 1
    sent = client.placed[0]
    assert sent.token_id == "yes"
    assert sent.side is Side.BUY_YES
    assert sent.price == Decimal("0.40")
    assert sent.size == Decimal("10")
    assert sent.signature_type == 0


def test_off_tick_rejected_before_client_called() -> None:
    client = FakeExecutionClient()
    _, result = ClobVenue(client).place(
        _order(price="0.405"), _market(), RiskState.start(_T0), _config(), _T0
    )
    assert result.status == "rejected"
    assert "tick" in (result.reason or "")
    assert client.placed == []


def test_below_min_size_rejected_before_client_called() -> None:
    client = FakeExecutionClient()
    _, result = ClobVenue(client).place(
        _order(size="4"), _market(), RiskState.start(_T0), _config(), _T0
    )
    assert result.status == "rejected"
    assert "minimum" in (result.reason or "")
    assert client.placed == []


def test_insufficient_allowance_suppresses_market() -> None:
    client = FakeExecutionClient(usdc_allowance=Decimal("0.5"))  # < 0.40*10 = 4.0
    state, result = ClobVenue(client).place(
        _order(), _market(), RiskState.start(_T0), _config(), _T0
    )
    assert result.status == "rejected"
    assert "allowance" in (result.reason or "")
    assert "m" in state.halted_markets
    assert client.placed == []  # not posted


def test_order_count_breach_halts_and_blocks_further_orders() -> None:
    client = FakeExecutionClient()
    venue = ClobVenue(client)
    config = _config(max_orders_per_run=1)
    state = RiskState.start(_T0)
    state, first = venue.place(_order(), _market(), state, config, _T0)
    state, second = venue.place(_order(), _market(), state, config, _T0)
    assert first.status == "placed"
    assert second.status == "halted"
    assert state.halted is True
    assert len(client.placed) == 1  # the breaching order never reached the client


def test_simulated_venue_preflights_without_client() -> None:
    state, result = SimulatedVenue().place(
        _order(), _market(), RiskState.start(_T0), _config(), _T0
    )
    assert result.status == "placed"
    assert result.order_id == "sim"
    assert state.exposure["m"] == Decimal("4.0")
