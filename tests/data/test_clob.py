"""CLOB adapter: parsers (fixtures) + REST client + WS client (fake connection)."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from data.clob import (
    ClobAuth,
    ClobClient,
    ClobWsClient,
    parse_book,
    parse_price_history,
    parse_ws_message,
)
from data.payloads import ClobBook, ClobPriceHistory

_FIX = Path(__file__).parent.parent / "fixtures" / "clob"


def test_parse_book_uses_mid_and_best_levels() -> None:
    raw = ClobBook.model_validate(json.loads((_FIX / "book.json").read_text()))
    quote = parse_book(raw)
    assert quote.market_id == "0xabc"
    assert quote.bid == Decimal("0.51")
    assert quote.ask == Decimal("0.53")
    assert quote.price == Decimal("0.52")  # mid of 0.51 / 0.53
    assert quote.ts == datetime.fromtimestamp(1718800000, tz=UTC)


def test_parse_price_history_decimal_quotes() -> None:
    raw = ClobPriceHistory.model_validate(
        json.loads((_FIX / "prices_history.json").read_text())
    )
    quotes = parse_price_history("111", raw)
    assert [q.price for q in quotes] == [Decimal("0.50"), Decimal("0.55")]
    assert quotes[0].market_id == "111"


def test_parse_ws_message_book_and_other() -> None:
    book_msg = json.loads((_FIX / "book.json").read_text())
    book_msg["event_type"] = "book"
    quote = parse_ws_message(book_msg)
    assert quote is not None and quote.price == Decimal("0.52")
    assert parse_ws_message({"event_type": "ack"}) is None


def test_clob_auth_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOB_API_KEY", "k")
    monkeypatch.setenv("CLOB_API_SECRET", "s")
    monkeypatch.setenv("CLOB_API_PASSPHRASE", "p")
    auth = ClobAuth.from_env()
    assert auth is not None and auth.api_key == "k"


def test_clob_auth_from_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLOB_API_KEY", raising=False)
    monkeypatch.delenv("CLOB_API_SECRET", raising=False)
    monkeypatch.delenv("CLOB_API_PASSPHRASE", raising=False)
    assert ClobAuth.from_env() is None


def test_fetch_book_over_mock_transport() -> None:
    async def _run() -> None:
        payload = json.loads((_FIX / "book.json").read_text())

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["token_id"] == "111"
            return httpx.Response(200, json=payload)

        transport = httpx.MockTransport(handler)
        async with ClobClient(
            transport=transport, base_url="http://t", retry_backoff=0.0
        ) as client:
            quote = await client.fetch_book("111")
        assert quote.price == Decimal("0.52")

    asyncio.run(_run())


class _FakeWs:
    def __init__(self, frames: list[str]) -> None:
        self._frames = frames
        self.sent: list[str] = []

    async def __aenter__(self) -> "_FakeWs":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[str]:
        for frame in self._frames:
            yield frame


def test_ws_stream_yields_quotes() -> None:
    async def _run() -> None:
        book_msg = json.loads((_FIX / "book.json").read_text())
        book_msg["event_type"] = "book"
        frames = [
            json.dumps(book_msg),
            json.dumps({"event_type": "ack"}),
        ]
        client = ClobWsClient(ws_connect=lambda url: _FakeWs(frames))
        quotes = [q async for q in client.stream(["0xabc"])]
        assert len(quotes) == 1
        assert quotes[0].price == Decimal("0.52")

    asyncio.run(_run())
