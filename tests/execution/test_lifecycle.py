from decimal import Decimal

from execution.client import FakeExecutionClient, OrderStatus
from execution.lifecycle import poll_to_terminal


def test_poll_stops_at_terminal_state() -> None:
    seq = [
        OrderStatus(order_id="o", state="open"),
        OrderStatus(order_id="o", state="open"),
        OrderStatus(order_id="o", state="matched", filled_size=Decimal("5")),
    ]
    client = FakeExecutionClient(status_sequence=seq)
    sleeps: list[float] = []
    status, terminal = poll_to_terminal(
        client, "o", max_attempts=5, sleep=sleeps.append
    )
    assert terminal is True
    assert status.state == "matched"
    assert len(sleeps) == 2  # slept before the 2nd and 3rd polls


def test_poll_times_out_without_terminal() -> None:
    seq = [OrderStatus(order_id="o", state="open")] * 3
    client = FakeExecutionClient(status_sequence=seq)
    sleeps: list[float] = []
    status, terminal = poll_to_terminal(
        client, "o", max_attempts=3, sleep=sleeps.append
    )
    assert terminal is False
    assert status.state == "open"
    assert len(sleeps) == 2


def test_poll_invokes_on_poll_each_tick() -> None:
    seq = [
        OrderStatus(order_id="o", state="open"),
        OrderStatus(order_id="o", state="matched"),
    ]
    client = FakeExecutionClient(status_sequence=seq)
    ticks: list[int] = []
    poll_to_terminal(
        client,
        "o",
        max_attempts=5,
        sleep=lambda _: None,
        on_poll=lambda _status, attempt: ticks.append(attempt),
    )
    assert ticks == [1, 2]
