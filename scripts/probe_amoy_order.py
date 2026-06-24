"""Operator probe (non-gate): post one signed order on Amoy plus a self-counter
order and watch it settle. A mechanical pre-check that a signed order is
accepted on testnet — NOT a real-fill test (that is the Phase 6 mainnet
micro-trade). Requires real env credentials; never runs in CI.
"""

from __future__ import annotations

import os
from decimal import Decimal

from core.models import Side
from execution.orders import OrderRequest


def build_probe_orders(
    token_id: str, price: Decimal, size: Decimal
) -> tuple[OrderRequest, OrderRequest]:
    buy = OrderRequest(
        market_id="probe",
        token_id=token_id,
        side=Side.BUY_YES,
        price=price,
        size=size,
    )
    counter = OrderRequest(
        market_id="probe",
        token_id=token_id,
        side=Side.BUY_NO,
        price=price,
        size=size,
    )
    return buy, counter


def main() -> None:  # pragma: no cover - operator-run network path
    from execution.client import ClobExecutionClient

    client = ClobExecutionClient(
        host="https://clob.polymarket.com",
        private_key=os.environ["WALLET_PRIVATE_KEY"],
        chain_id=80002,
    )
    token_id = os.environ["PROBE_TOKEN_ID"]
    buy, counter = build_probe_orders(token_id, Decimal("0.50"), Decimal("5"))
    print("placing buy:", client.place(buy))
    print("placing counter:", client.place(counter))


if __name__ == "__main__":  # pragma: no cover
    main()
