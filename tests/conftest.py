"""Shared fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from custom_components.elan_dtnr import client as client_mod
from custom_components.elan_dtnr import coordinator as coordinator_mod

from .fake_dtnr import FakeDTNR


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components/elan_dtnr."""


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod, "QUERY_TIMEOUT", 0.3)
    monkeypatch.setattr(client_mod, "COMMAND_GAP", 0)
    monkeypatch.setattr(coordinator_mod, "POWER_TIMEOUT", 0.5)
    monkeypatch.setattr(coordinator_mod, "POWER_POLL_INTERVAL", 0.05)
    monkeypatch.delenv("FEATURES", raising=False)


@pytest.fixture
def store_preset_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """FEATURES=store_preset. Request it before ``entry``: it's read at setup."""
    monkeypatch.setenv("FEATURES", "store_preset")


@pytest.fixture
async def fake(socket_enabled: None) -> AsyncIterator[FakeDTNR]:
    server = await FakeDTNR().start()
    yield server
    await server.stop()
