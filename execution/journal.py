"""Structured JSONL trade journal.

One JSON object per line (ts, event, payload) via stdlib logging with a
JSON-lines formatter. Payloads come from pydantic model_dump(mode="json"), which
never includes credentials (OrderRequest/Result carry no secrets). Reusable by
the future live loop.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from execution.client import OrderStatus
from execution.orders import OrderRequest, OrderResult
from execution.reconcile import ReconciliationReport


class _JsonLinesFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = getattr(record, "payload", {})
        obj = {
            "ts": datetime.now(UTC).isoformat(),
            "event": record.getMessage(),
            **payload,
        }
        return json.dumps(obj, default=str)


class Journal:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        self._logger.info(event, extra={"payload": payload})

    def order_attempted(self, order: OrderRequest, result: OrderResult) -> None:
        self._emit(
            "order_attempted",
            {
                "order": order.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            },
        )

    def status_polled(self, order_id: str, status: OrderStatus, attempt: int) -> None:
        self._emit(
            "status_polled",
            {
                "order_id": order_id,
                "attempt": attempt,
                "status": status.model_dump(mode="json"),
            },
        )

    def order_terminal(self, status: OrderStatus, reached_terminal: bool) -> None:
        self._emit(
            "order_terminal",
            {
                "reached_terminal": reached_terminal,
                "status": status.model_dump(mode="json"),
            },
        )

    def reconciliation(self, report: ReconciliationReport) -> None:
        self._emit("reconciliation", {"report": report.model_dump(mode="json")})

    def aborted(self, reason: str) -> None:
        self._emit("aborted", {"reason": reason})


def build_journal(path: Path, *, name: str = "fifabot.microtrade") -> Journal:
    logger = logging.getLogger(f"{name}.{path}")
    logger.setLevel(logging.INFO)
    for old in logger.handlers:
        old.close()
    logger.handlers.clear()
    handler = logging.FileHandler(path)
    handler.setFormatter(_JsonLinesFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return Journal(logger)
