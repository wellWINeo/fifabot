# core/signals/base.py
"""SignalOutput: the uniform per-market estimate a signal emits.

A signal abstains by returning None at the wrapper level; when it has an
opinion it produces a SignalOutput. group_id/overround carry S2 basket context
recorded for Phase 5 (not acted on in Phase 3).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SignalOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    p_fair: float = Field(ge=0.0, le=1.0)
    source: str
    rationale: str
    group_id: str | None = None
    overround: float | None = None
