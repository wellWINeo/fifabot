"""Raw API payload models: tolerant parsing, required-field validation."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from data.payloads import (
    ClobBook,
    ClobPriceHistory,
    GammaMarket,
    GammaPriceHistory,
)


def test_gamma_market_parses_and_ignores_extra() -> None:
    m = GammaMarket.model_validate(
        {
            "id": "0xabc",
            "question": "Will Team A win?",
            "clobTokenIds": ["111", "222"],
            "tickSize": "0.01",
            "active": True,
            "closed": False,
            "unknownField": "ignored",
        }
    )
    assert m.id == "0xabc"
    assert m.clobTokenIds == ["111", "222"]
    assert m.tickSize == Decimal("0.01")


def test_gamma_market_requires_id() -> None:
    with pytest.raises(ValidationError):
        GammaMarket.model_validate({"question": "q", "clobTokenIds": []})


def test_gamma_price_history_parses() -> None:
    h = GammaPriceHistory.model_validate(
        {"history": [{"t": 1718800000, "p": 0.45}, {"t": 1718800600, "p": 0.47}]}
    )
    assert len(h.history) == 2
    assert h.history[0].p == 0.45


def test_clob_book_parses_levels() -> None:
    b = ClobBook.model_validate(
        {
            "market": "0xabc",
            "timestamp": 1718800000000,
            "asks": [{"price": "0.53", "size": "100"}],
            "bids": [{"price": "0.51", "size": "150"}],
        }
    )
    assert b.market == "0xabc"
    assert b.asks[0].price == Decimal("0.53")
    assert b.bids[0].size == Decimal("150")


def test_clob_price_history_parses_decimal() -> None:
    h = ClobPriceHistory.model_validate({"history": [{"t": 1718800000, "p": "0.50"}]})
    assert h.history[0].p == Decimal("0.50")
