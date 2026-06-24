from datetime import UTC, datetime
from pathlib import Path

from core.risk import RiskState, trip
from execution.store import FileRiskStore, InMemoryRiskStore

_T0 = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)


def test_in_memory_round_trips() -> None:
    store = InMemoryRiskStore()
    assert store.load() is None
    state = RiskState.start(_T0)
    store.save(state)
    assert store.load() == state


def test_file_store_round_trips(tmp_path: Path) -> None:
    store = FileRiskStore(tmp_path / "risk.json")
    assert store.load() is None
    store.save(RiskState.start(_T0))
    assert store.load() == RiskState.start(_T0)


def test_restart_while_halted_stays_halted(tmp_path: Path) -> None:
    path = tmp_path / "risk.json"
    FileRiskStore(path).save(trip(RiskState.start(_T0), "daily loss cap breached"))
    # a fresh process / fresh store instance loads the persisted halt
    reloaded = FileRiskStore(path).load()
    assert reloaded is not None
    assert reloaded.halted is True
    assert reloaded.halt_reason == "daily loss cap breached"
