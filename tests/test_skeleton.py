"""Smoke test: every top-level package imports cleanly."""

import importlib

PACKAGES = ("core", "data", "backtest", "llm", "execution", "app")


def test_packages_import() -> None:
    for name in PACKAGES:
        assert importlib.import_module(name) is not None
