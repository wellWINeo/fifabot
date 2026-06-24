"""Operator one-off (non-gate): approve USDC/CTF allowances for the EOA wallet
on the Polymarket exchange. Run once per wallet before trading. Requires real
env credentials; never runs in CI.
"""

from __future__ import annotations

import os


def main() -> None:  # pragma: no cover - operator-run network path
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

    client = ClobClient(
        host="https://clob.polymarket.com",
        key=os.environ["WALLET_PRIVATE_KEY"],
        chain_id=80002,
        signature_type=0,
    )
    client.set_api_creds(client.create_or_derive_api_creds())
    client.update_balance_allowance(
        BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    print("allowances updated")


if __name__ == "__main__":  # pragma: no cover
    main()
