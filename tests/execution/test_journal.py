import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.models import CostInputs, Side
from execution.client import OrderStatus
from execution.journal import build_journal
from execution.orders import OrderRequest, OrderResult
from execution.reconcile import reconcile


def _read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _order() -> OrderRequest:
    return OrderRequest(
        market_id="m",
        token_id="t",
        side=Side.BUY_YES,
        price=Decimal("0.50"),
        size=Decimal("10"),
    )


def test_journal_emits_one_json_object_per_event(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    journal = build_journal(path)
    journal.order_attempted(_order(), OrderResult(status="placed", order_id="o"))
    journal.aborted("done")
    records = _read(path)
    assert [r["event"] for r in records] == ["order_attempted", "aborted"]
    assert all("ts" in r for r in records)


def test_journal_records_reconciliation_payload(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    journal = build_journal(path)
    status = OrderStatus(
        order_id="o",
        state="matched",
        filled_size=Decimal("10"),
        avg_fill_price=Decimal("0.50"),
    )
    costs = CostInputs(
        spread=Decimal("0.02"),
        fee_rate=Decimal("0.01"),
        gas_usd=Decimal(0),
        model_error_margin=Decimal(0),
    )
    journal.reconciliation(reconcile(_order(), status, costs))
    record = _read(path)[0]
    assert record["event"] == "reconciliation"
    assert record["report"]["fill_ratio"] == "1"


def test_journal_does_not_leak_secrets(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    journal = build_journal(path)
    journal.order_attempted(_order(), OrderResult(status="placed", order_id="o"))
    raw = path.read_text().lower()
    assert "private" not in raw
    assert "wallet_private_key" not in raw
