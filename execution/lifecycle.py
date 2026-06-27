"""Poll an order's status to a terminal state.

Pure loop logic over the injected ExecutionClient and a sleep callable, so the
backoff cadence is real in production and a no-op recorder in tests.
"""

from __future__ import annotations

from collections.abc import Callable

from execution.client import ExecutionClient, OrderStatus

TERMINAL_STATES = frozenset({"matched", "filled", "cancelled", "unmatched", "expired"})


def poll_to_terminal(
    client: ExecutionClient,
    order_id: str,
    *,
    max_attempts: int,
    sleep: Callable[[float], None],
    interval: float = 1.0,
    on_poll: Callable[[OrderStatus, int], None] | None = None,
) -> tuple[OrderStatus, bool]:
    status = client.status(order_id)
    attempt = 1
    if on_poll is not None:
        on_poll(status, attempt)
    while status.state not in TERMINAL_STATES and attempt < max_attempts:
        sleep(interval)
        status = client.status(order_id)
        attempt += 1
        if on_poll is not None:
            on_poll(status, attempt)
    return status, status.state in TERMINAL_STATES
