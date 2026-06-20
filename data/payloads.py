"""Raw Polymarket API payload models (the wire shapes we consume).

Tolerant of unknown fields (extra="ignore"); missing required fields raise.
These are parsed into canonical records in data/gamma.py and data/clob.py.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class GammaPricePoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    t: int
    p: float


class GammaPriceHistory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    history: list[GammaPricePoint]


class GammaMarket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    question: str
    clobTokenIds: list[str] = Field(default_factory=list)  # noqa: N815
    tickSize: Decimal = Decimal("0.01")  # noqa: N815
    active: bool = True
    closed: bool = False


class ClobBookLevel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    price: Decimal
    size: Decimal


class ClobBook(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    market: str
    asks: list[ClobBookLevel] = Field(default_factory=list)
    bids: list[ClobBookLevel] = Field(default_factory=list)
    timestamp: int | None = None


class ClobPricePoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    t: int
    p: Decimal


class ClobPriceHistory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    history: list[ClobPricePoint]
