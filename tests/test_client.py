"""Client tests against the TCP emulator."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from custom_components.elan_dtnr.client import (
    DTNRClient,
    DTNRConnectionError,
    DTNRTimeoutError,
)
from custom_components.elan_dtnr.protocol import Band, TunerState

from .fake_dtnr import FakeDTNR


@pytest.fixture
async def client(fake: FakeDTNR) -> AsyncIterator[DTNRClient]:
    c = DTNRClient(fake.url)
    yield c
    await c.disconnect()


async def test_query_and_send(client: DTNRClient, fake: FakeDTNR) -> None:
    state = await client.query(1)
    assert (state.band, state.frequency) == (Band.FM, 106.3)
    await client.send("T1B2F0971")
    assert (await client.query(1)).frequency == 97.1
    assert fake.received == ["Q1", "T1B2F0971", "Q1"]
    assert fake.connections == 1  # one connection reused


async def test_query_off_tuner(client: DTNRClient, fake: FakeDTNR) -> None:
    fake.tuners[2].power = False
    assert await client.query(2) == TunerState(tuner=2, power=False)


async def test_reconnects_after_bridge_drops(client: DTNRClient, fake: FakeDTNR) -> None:
    await client.query(1)
    await fake.drop_clients()
    assert (await client.query(1)).frequency == 106.3
    assert fake.connections == 2


async def test_timeout_then_recycle(client: DTNRClient, fake: FakeDTNR) -> None:
    """A dead TLS leg behind stunnel looks like silence on a live socket."""
    await client.query(1)
    fake.mute = True
    with pytest.raises(DTNRTimeoutError):
        await client.query(1)
    assert fake.connections == 1  # one timeout alone doesn't reconnect
    fake.mute = False
    # The tuner answers again, but the streak counter is reset only on
    # success; the next query succeeds without needing a recycle.
    assert (await client.query(1)).power
    fake.mute = True
    with pytest.raises(DTNRTimeoutError):
        await client.query(1)
    with pytest.raises(DTNRTimeoutError):
        await client.query(1)  # second in a row: reconnects, retries, still silent
    assert fake.connections == 2


async def test_unsolicited_status(fake: FakeDTNR) -> None:
    fake.push = True
    c = DTNRClient(fake.url)
    seen: list[TunerState] = []
    c.on_state = seen.append
    try:
        await c.send("T2B1F0680")
        await asyncio.sleep(0.1)
    finally:
        await c.disconnect()
    assert [(s.tuner, s.frequency) for s in seen] == [(2, 680)]


async def test_connect_failure(socket_enabled: None) -> None:
    c = DTNRClient("socket://127.0.0.1:1")
    with pytest.raises(DTNRConnectionError):
        await c.query(1)


async def test_bad_url(socket_enabled: None) -> None:
    with pytest.raises(DTNRConnectionError):
        await DTNRClient("socket://nohostport").query(1)
