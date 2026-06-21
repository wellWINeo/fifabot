"""Typed domain models for the trading core.

Decimal for money/prices (order-boundary: exact tick alignment and accounting);
float for the statistical/decision math handled elsewhere.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_TICK = Decimal("0.01")


class Side(StrEnum):
    BUY_YES = "buy_yes"
    BUY_NO = "buy_no"


class CostInputs(BaseModel):
    model_config = ConfigDict(frozen=True)

    spread: Decimal = Field(ge=0)
    fee_rate: Decimal = Field(ge=0)
    gas_usd: Decimal = Field(ge=0)
    model_error_margin: Decimal = Field(ge=0)


class TradeCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    price: Decimal = Field(gt=0, lt=1)
    raw_prob: float = Field(ge=0.0, le=1.0)
    costs: CostInputs
    notional_hint: Decimal = Field(gt=0)
    tick_size: Decimal = Field(default=DEFAULT_TICK, gt=0)

    @model_validator(mode="after")
    def _price_on_tick(self) -> Self:
        if self.price % self.tick_size != 0:
            raise ValueError(
                f"price {self.price} is not a multiple of tick {self.tick_size}"
            )
        return self


class RiskLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    bankroll: Decimal = Field(gt=0)
    kelly_fraction: float = Field(gt=0.0, le=1.0)
    max_position_fraction: float = Field(gt=0.0, le=1.0)
    max_position_usd: Decimal = Field(gt=0)


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: Literal["act", "abstain"]
    side: Side | None = None
    edge: float | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _side_and_edge_match_action(self) -> Self:
        has_side_and_edge = self.side is not None and self.edge is not None
        if self.action == "act" and not has_side_and_edge:
            raise ValueError("act requires both side and edge")
        has_side_or_edge = self.side is not None or self.edge is not None
        if self.action == "abstain" and has_side_or_edge:
            raise ValueError("abstain must not set side or edge")
        return self

    @classmethod
    def act(cls, side: Side, edge: float) -> GateResult:
        return cls(action="act", side=side, edge=edge)

    @classmethod
    def abstain(cls, reason: str) -> GateResult:
        return cls(action="abstain", reason=reason)


class SizingResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    stake_usd: Decimal = Field(ge=0)
    shares: Decimal = Field(ge=0)
    binding_cap: str | None = None


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)

    gate: GateResult
    sizing: SizingResult
    prob: float | None = None


class Fill(BaseModel):
    model_config = ConfigDict(frozen=True)

    side: Side
    entry_price: Decimal = Field(gt=0, lt=1)
    exit_price: Decimal = Field(gt=0, lt=1)
    shares: Decimal = Field(ge=0)
    costs_usd: Decimal = Field(ge=0)


class CalibrationSample(BaseModel):
    model_config = ConfigDict(frozen=True)

    raw_prob: float = Field(ge=0.0, le=1.0)
    outcome: int = Field(ge=0, le=1)
