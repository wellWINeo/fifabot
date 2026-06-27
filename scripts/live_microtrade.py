"""Operator-run single live micro-trade harness (Phase 6 gate).

run_microtrade is pure orchestration over injected collaborators and is fully
unit-tested offline. main() wires the real CLOB SDK + env and is operator-run
only (never in CI). SELL/close and the continuous live loop are deferred.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from core.models import CostInputs, Side
from core.risk import RiskConfig, RiskState
from data.events import Market
from execution.client import ExecutionClient
from execution.journal import Journal, build_journal
from execution.lifecycle import poll_to_terminal
from execution.orders import OrderRequest
from execution.reconcile import reconcile
from execution.store import FileRiskStore, RiskStore
from execution.venue import ClobVenue, ExecutionVenue


def run_microtrade(
    *,
    order: OrderRequest,
    market: Market,
    venue: ExecutionVenue,
    client: ExecutionClient,
    journal: Journal,
    store: RiskStore,
    risk_config: RiskConfig,
    costs: CostInputs,
    now: datetime,
    sleep: Callable[[float], None],
    max_attempts: int,
    slippage_threshold: Decimal,
) -> int:
    state = store.load() or RiskState.start(now)
    state, result = venue.place(order, market, state, risk_config, now)
    store.save(state)
    journal.order_attempted(order, result)
    if result.status != "placed" or result.order_id is None:
        journal.aborted(result.reason or "order not placed")
        return 1

    order_id = result.order_id
    status, terminal = poll_to_terminal(
        client,
        order_id,
        max_attempts=max_attempts,
        sleep=sleep,
        on_poll=lambda polled, attempt: journal.status_polled(
            order_id, polled, attempt
        ),
    )
    if not terminal:
        client.cancel(order_id)
        journal.aborted("poll timeout")
        return 1

    journal.order_terminal(status, terminal)
    report = reconcile(order, status, costs)
    journal.reconciliation(report)
    print(
        f"filled {report.filled_size}/{report.intended_size} "
        f"@ avg {report.avg_fill_price} (intended {report.intended_price}); "
        f"slippage {report.slippage}; cost_delta {report.cost_delta:.4f}"
    )
    if report.fill_ratio <= 0 or report.slippage > slippage_threshold:
        return 1
    return 0


def main() -> None:  # pragma: no cover - operator-run network path
    from execution.client import ClobExecutionClient

    if os.environ.get("LIVE_CONFIRM") != "1":
        raise SystemExit("refusing to run: set LIVE_CONFIRM=1 to place a live order")
    chain_id = int(os.environ["POLY_CHAIN_ID"])  # explicit; no default
    client = ClobExecutionClient(
        host=os.environ.get("CLOB_HOST", "https://clob.polymarket.com"),
        private_key=os.environ["WALLET_PRIVATE_KEY"],
        chain_id=chain_id,
    )
    token_id = os.environ["MICRO_TOKEN_ID"]
    market = Market(
        market_id=os.environ.get("MICRO_MARKET_ID", "microtrade"),
        question="micro-trade",
        token_ids=(token_id,),
        tick_size=Decimal(os.environ.get("MICRO_TICK", "0.01")),
        minimum_order_size=Decimal(os.environ.get("MICRO_MIN_SIZE", "5")),
    )
    side = Side.BUY_NO if os.environ.get("MICRO_SIDE") == "no" else Side.BUY_YES
    order = OrderRequest(
        market_id=market.market_id,
        token_id=token_id,
        side=side,
        price=Decimal(os.environ["MICRO_PRICE"]),
        size=Decimal(os.environ["MICRO_SIZE"]),
    )
    risk_config = RiskConfig(
        max_position_usd=Decimal(os.environ.get("RISK_MAX_POSITION", "25")),
        max_daily_loss_usd=Decimal(os.environ.get("RISK_MAX_DAILY_LOSS", "10")),
        max_orders_per_run=int(os.environ.get("RISK_MAX_ORDERS", "5")),
        resubmit_window_seconds=float(os.environ.get("RISK_RESUBMIT_WINDOW", "60")),
        max_orders_per_market_in_window=int(os.environ.get("RISK_MAX_PER_MARKET", "3")),
    )
    costs = CostInputs(
        spread=Decimal(os.environ.get("COST_SPREAD", "0.02")),
        fee_rate=Decimal(os.environ.get("COST_FEE", "0.0")),
        gas_usd=Decimal(os.environ.get("COST_GAS", "0.0")),
        model_error_margin=Decimal(os.environ.get("COST_MARGIN", "0.0")),
    )
    journal = build_journal(Path(os.environ.get("JOURNAL_PATH", "microtrade.jsonl")))
    store = FileRiskStore(Path(os.environ.get("RISK_STATE_PATH", "risk_state.json")))
    code = run_microtrade(
        order=order,
        market=market,
        venue=ClobVenue(client),
        client=client,
        journal=journal,
        store=store,
        risk_config=risk_config,
        costs=costs,
        now=datetime.now(UTC),
        sleep=time.sleep,
        max_attempts=int(os.environ.get("POLL_MAX_ATTEMPTS", "30")),
        slippage_threshold=Decimal(os.environ.get("SLIPPAGE_THRESHOLD", "0.02")),
    )
    sys.exit(code)


if __name__ == "__main__":  # pragma: no cover
    main()
