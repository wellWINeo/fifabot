"""Global test guards. Block real network so no unit test can reach a socket."""

import socket
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def _blocked(*args: object, **kwargs: object) -> None:
        raise RuntimeError("network access is disabled in tests")

    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    yield
