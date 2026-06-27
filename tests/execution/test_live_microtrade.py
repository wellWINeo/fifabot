import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from core.models import CostInputs, Side
from core.risk import RiskConfig, RiskState, trip
from data.events import Market
from execution.client import FakeExecutionClient, OrderStatus
from execution.journal import build_journal
from execution.orders import OrderRequest
from execution.store import InMemoryRiskStore
from execution.venue import ClobVenue
from scripts.live_microtrade import run_microtrade

NOW = datetime(2026, 6, 24, tzinfo=UTC)


def _market() -> Market:
    return Market(
        market_id="m",
        question="q?",
        token_ids=("t",),
        tick_size=Decimal("0.01"),
        minimum_order_size=Decimal("5"),
    )


def _order() -> OrderRequest:
    return OrderRequest(
        market_id="m",
        token_id="t",
        side=Side.BUY_YES,
        price=Decimal("0.50"),
        size=Decimal("10"),
    )


def _risk_config() -> RiskConfig:
    return RiskConfig(
        max_position_usd=Decimal("25"),
        max_daily_loss_usd=Decimal("10"),
        max_orders_per_run=10,
        resubmit_window_seconds=60,
        max_orders_per_market_in_window=5,
    )


def _costs() -> CostInputs:
    return CostInputs(
        spread=Decimal("0.02"),
        fee_rate=Decimal("0.01"),
        gas_usd=Decimal(0),
        model_error_margin=Decimal(0),
    )


def _events(path: Path) -> list[str]:
    return [json.loads(line)["event"] for line in path.read_text().splitlines() if line]


def _run(path: Path, client: FakeExecutionClient, store: InMemoryRiskStore) -> int:
    return run_microtrade(
        order=_order(),
        market=_market(),
        venue=ClobVenue(client),
        client=client,
        journal=build_journal(path),
        store=store,
        risk_config=_risk_config(),
        costs=_costs(),
        now=NOW,
        sleep=lambda _: None,
        max_attempts=3,
        slippage_threshold=Decimal("0.05"),
    )


def test_microtrade_fills_and_reconciles(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    client = FakeExecutionClient(
        status_sequence=[
            OrderStatus(order_id="ord-1", state="open"),
            OrderStatus(
                order_id="ord-1",
                state="matched",
                filled_size=Decimal("10"),
                avg_fill_price=Decimal("0.50"),
                fees_paid=Decimal("0.01"),
            ),
        ]
    )
    code = _run(path, client, InMemoryRiskStore())
    assert code == 0
    events = _events(path)
    assert events[0] == "order_attempted"
    assert "status_polled" in events
    assert "reconciliation" in events
    assert client.placed and client.placed[0].token_id == "t"


def test_microtrade_aborts_when_risk_halted(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    store = InMemoryRiskStore()
    store.save(trip(RiskState.start(NOW), "manual kill switch"))
    client = FakeExecutionClient()
    code = _run(path, client, store)
    assert code == 1
    assert _events(path) == ["order_attempted", "aborted"]
    assert client.placed == []


def test_microtrade_accepts_favorable_fill(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    client = FakeExecutionClient(
        status_sequence=[
            OrderStatus(
                order_id="ord-1",
                state="matched",
                filled_size=Decimal("10"),
                avg_fill_price=Decimal("0.47"),  # below limit price = favorable for BUY
                fees_paid=Decimal("0"),
            )
        ]
    )
    code = _run(path, client, InMemoryRiskStore())
    assert code == 0  # negative slippage must not be flagged as failure


def test_microtrade_times_out_and_cancels(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    client = FakeExecutionClient(
        status_sequence=[OrderStatus(order_id="ord-1", state="open")] * 3
    )
    code = _run(path, client, InMemoryRiskStore())
    assert code == 1
    assert client.cancelled == ["ord-1"]
    assert _events(path)[-1] == "aborted"
