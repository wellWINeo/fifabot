"""Risk-state persistence. The pure core computes the halt; the store persists
the tripped flag at the edge so a restarted process comes up halted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from core.risk import RiskState


class RiskStore(Protocol):
    def load(self) -> RiskState | None: ...
    def save(self, state: RiskState) -> None: ...


class InMemoryRiskStore:
    def __init__(self) -> None:
        self._state: RiskState | None = None

    def load(self) -> RiskState | None:
        return self._state

    def save(self, state: RiskState) -> None:
        self._state = state


class FileRiskStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> RiskState | None:
        if not self._path.exists():
            return None
        return RiskState.model_validate_json(self._path.read_text())

    def save(self, state: RiskState) -> None:
        self._path.write_text(state.model_dump_json())
