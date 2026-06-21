# llm/schema.py
"""Typed contract for the deferred LLM layer (Phase 4 builds the agent).

Defines the shape the pydantic-ai hypothesis generator / feature extractor will
emit. No agent and no pydantic-ai dependency in Phase 3 -- only the validated
output type, so malformed model output is rejected cleanly at the boundary.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HypothesisOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    p_fair: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
