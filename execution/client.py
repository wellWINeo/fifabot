"""Execution client seam: the only place the py-clob-client SDK is touched.

ExecutionClient is the Protocol the venue depends on; FakeExecutionClient is
the in-test double; ClobExecutionClient wraps py-clob-client v2 (EOA / signature
type 0, funder == signer) with a lazy SDK import so tests never load it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from execution.orders import OrderRequest


class Allowances(BaseModel):
    model_config = ConfigDict(frozen=True)

    usdc: Decimal
    ctf: Decimal


class OrderStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_id: str
    state: str
    filled_size: Decimal = Decimal(0)
    avg_fill_price: Decimal = Decimal(0)
    fees_paid: Decimal = Decimal(0)


class ExecutionClient(Protocol):
    def allowances(self) -> Allowances: ...
    def place(self, order: OrderRequest) -> str: ...
    def cancel(self, order_id: str) -> None: ...
    def status(self, order_id: str) -> OrderStatus: ...


@dataclass
class FakeExecutionClient:
    usdc_allowance: Decimal = Decimal("1000")
    ctf_allowance: Decimal = Decimal("1000")
    next_id: str = "ord-1"
    placed: list[OrderRequest] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    status_sequence: list[OrderStatus] = field(default_factory=list)
    _status_idx: int = field(default=0, init=False)

    def allowances(self) -> Allowances:
        return Allowances(usdc=self.usdc_allowance, ctf=self.ctf_allowance)

    def place(self, order: OrderRequest) -> str:
        self.placed.append(order)
        return self.next_id

    def cancel(self, order_id: str) -> None:
        self.cancelled.append(order_id)

    def status(self, order_id: str) -> OrderStatus:
        if self.status_sequence:
            idx = min(self._status_idx, len(self.status_sequence) - 1)
            self._status_idx += 1
            return self.status_sequence[idx]
        return OrderStatus(order_id=order_id, state="open")


class ClobExecutionClient:
    """Real CLOB client (EOA / type 0). Network path — never used in unit tests.

    The exact py-clob-client v2 call shapes are confirmed against the live SDK by
    the Amoy probe script, not by the offline gate. Construct only with real
    credentials from env.
    """

    def __init__(self, *, host: str, private_key: str, chain_id: int) -> None:
        from py_clob_client.client import ClobClient as _Sdk  # lazy import

        self._sdk = _Sdk(
            host=host, key=private_key, chain_id=chain_id, signature_type=0
        )
        self._sdk.set_api_creds(self._sdk.create_or_derive_api_creds())

    def allowances(self) -> Allowances:
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

        usdc = self._sdk.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        return Allowances(
            usdc=Decimal(str(usdc["allowance"])),
            ctf=Decimal(str(usdc["allowance"])),
        )

    def place(self, order: OrderRequest) -> str:
        from py_clob_client.clob_types import OrderArgs
        from py_clob_client.order_builder.constants import BUY

        args = OrderArgs(
            token_id=order.token_id,
            price=float(order.price),
            size=float(order.size),
            side=BUY,
        )
        signed = self._sdk.create_order(args)
        resp = self._sdk.post_order(signed)
        return str(resp["orderID"])

    def cancel(self, order_id: str) -> None:
        self._sdk.cancel(order_id)

    def status(self, order_id: str) -> OrderStatus:
        resp = self._sdk.get_order(order_id)
        return OrderStatus(
            order_id=order_id,
            state=str(resp.get("status", "unknown")),
            filled_size=Decimal(str(resp.get("size_matched", "0"))),
            avg_fill_price=Decimal(str(resp.get("price", "0"))),
            fees_paid=Decimal(str(resp.get("fee", "0"))),
        )
