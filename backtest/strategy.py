"""The Strategy seam: how signals plug into the replay harness.

Phase 2 ships only synthetic strategies (in tests). Phase 3 signals and the
Phase 4 assembled pipeline implement this same Protocol.
"""

from __future__ import annotations

from typing import Protocol

from backtest.view import MarketView
from core.models import Decision
from data.events import MarketEvent


class Strategy(Protocol):
    def on_event(self, event: MarketEvent, view: MarketView) -> Decision | None: ...
