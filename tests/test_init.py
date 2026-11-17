"""Setup, entities and services, end to end against the emulator."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_TITLE,
    MediaPlayerEntityFeature,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    CONF_DEVICE,
    STATE_OFF,
    STATE_ON,
    STATE_PLAYING,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from custom_components.elan_dtnr import features
from custom_components.elan_dtnr.const import DOMAIN

from .fake_dtnr import AM, FM, FakeDTNR

T1 = "media_player.elan_dtnr_tuner_1"
T2 = "media_player.elan_dtnr_tuner_2"
POWER = "switch.elan_dtnr_power"


@pytest.fixture
async def entry(hass: HomeAssistant, fake: FakeDTNR) -> AsyncIterator[MockConfigEntry]:
    entry = MockConfigEntry(
        domain=DOMAIN, title="ELAN DTNR", data={CONF_DEVICE: fake.url}, unique_id=fake.url
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    yield entry
    if entry.state is ConfigEntryState.LOADED:
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)


async def _advance(hass: HomeAssistant, seconds: float) -> None:
    """Move the clock, once nothing is in flight.

    async_fire_time_changed also fires pending asyncio timers, so moving the
    clock mid-query would time the query out early.
    """
    await hass.async_block_till_done(wait_background_tasks=True)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done(wait_background_tasks=True)


async def _settle(hass: HomeAssistant) -> None:
    """Let the post-command debounced refresh run."""
    await _advance(hass, 1)


async def _call(hass: HomeAssistant, domain: str, service: str, data: dict) -> None:
    await hass.services.async_call(domain, service, data, blocking=True)
    await _settle(hass)


async def test_setup_creates_entities(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert entry.state is ConfigEntryState.LOADED

    t1 = hass.states.get(T1)
    assert t1.state == STATE_PLAYING
    assert t1.attributes[ATTR_INPUT_SOURCE] == "FM"
    assert t1.attributes[ATTR_MEDIA_TITLE] == "106.3 FM"
    assert hass.states.get(T2).attributes[ATTR_MEDIA_TITLE] == "1550 AM"

    assert hass.states.get("number.elan_dtnr_tuner_1_fm_frequency").state == "106.3"
    assert hass.states.get("number.elan_dtnr_tuner_1_am_frequency").state == "unknown"
    assert hass.states.get("number.elan_dtnr_tuner_2_am_frequency").state == "1550.0"
    assert hass.states.get("sensor.elan_dtnr_tuner_1_reception").state == "stereo"
    assert hass.states.get("sensor.elan_dtnr_tuner_2_reception").state == "mono"
    assert hass.states.get("select.elan_dtnr_tuner_1_preset").state == "unknown"
    assert hass.states.get("button.elan_dtnr_tuner_1_seek_up") is not None
    assert hass.states.get(POWER).state == STATE_ON

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert sorted(d.name for d in devices) == [
        "ELAN DTNR",
        "ELAN DTNR Tuner 1",
        "ELAN DTNR Tuner 2",
    ]


async def test_power(hass: HomeAssistant, entry: MockConfigEntry, fake: FakeDTNR) -> None:
    """Power is the unit's: X0/X1 switch both tuners."""
    await _call(hass, "switch", "turn_off", {ATTR_ENTITY_ID: POWER})
    assert "X0" in fake.received
    assert hass.states.get(POWER).state == STATE_OFF
    assert hass.states.get(T1).state == STATE_OFF
    assert hass.states.get(T2).state == STATE_OFF
    assert hass.states.get("sensor.elan_dtnr_tuner_1_reception").state == "unknown"
    # The last FM frequency is still shown while off.
    assert hass.states.get("number.elan_dtnr_tuner_1_fm_frequency").state == "106.3"

    await _call(hass, "switch", "turn_on", {ATTR_ENTITY_ID: POWER})
    assert "X1" in fake.received
    assert hass.states.get(POWER).state == STATE_ON
    assert hass.states.get(T1).state == STATE_PLAYING
    assert hass.states.get(T2).state == STATE_PLAYING
    # Never the per-tuner form, which a real unit ignores.
    assert not any(c.startswith("T") and "X" in c for c in fake.received)


async def test_tuners_have_no_power_of_their_own(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    supported = hass.states.get(T1).attributes[ATTR_SUPPORTED_FEATURES]
    assert not supported & MediaPlayerEntityFeature.TURN_ON
    assert not supported & MediaPlayerEntityFeature.TURN_OFF
    assert supported & MediaPlayerEntityFeature.NEXT_TRACK


async def test_power_on_waits_for_slow_unit(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeDTNR
) -> None:
    """The unit can still answer X0 just after X1; that mustn't show as off."""
    fake.set_power(False)
    fake.power_on_delay = 0.2
    await _advance(hass, 11)
    assert hass.states.get(T1).state == STATE_OFF

    await _call(hass, "switch", "turn_on", {ATTR_ENTITY_ID: POWER})
    assert "X1" in fake.received
    assert hass.states.get(T1).state == STATE_PLAYING
    assert hass.states.get(T1).attributes[ATTR_MEDIA_TITLE] == "106.3 FM"


async def test_power_on_gives_up(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeDTNR
) -> None:
    fake.set_power(False)
    fake.power_on_delay = 60
    with pytest.raises(HomeAssistantError, match="did not turn on"):
        await hass.services.async_call(
            "switch", "turn_on", {ATTR_ENTITY_ID: POWER}, blocking=True
        )


async def test_power_on_gave_up_then_unit_comes_on(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeDTNR
) -> None:
    """A late unit shows as on at the refresh after the failed command."""
    fake.set_power(False)
    fake.power_on_delay = 0.8  # past POWER_TIMEOUT (0.5 in the tests)
    with pytest.raises(HomeAssistantError, match="did not turn on"):
        await hass.services.async_call(
            "switch", "turn_on", {ATTR_ENTITY_ID: POWER}, blocking=True
        )
    await asyncio.sleep(0.6)
    await _advance(hass, 11)
    assert hass.states.get(POWER).state == "on"
    assert hass.states.get(T2).state == STATE_PLAYING


async def test_play_media_powers_on_and_tunes(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeDTNR
) -> None:
    fake.set_power(False)
    fake.power_on_delay = 0.2  # tuning before it's on would be ignored
    await _call(
        hass,
        "media_player",
        "play_media",
        {ATTR_ENTITY_ID: T1, ATTR_MEDIA_CONTENT_TYPE: "channel", ATTR_MEDIA_CONTENT_ID: "AM 680"},
    )
    assert "X1" in fake.received
    assert "T1B1F0680" in fake.received
    state = hass.states.get(T1)
    assert state.state == STATE_PLAYING
    assert state.attributes[ATTR_MEDIA_TITLE] == "680 AM"
    assert state.attributes[ATTR_INPUT_SOURCE] == "AM"
    assert hass.states.get("number.elan_dtnr_tuner_1_am_frequency").state == "680.0"
    # Switching the unit on brought tuner 2 on too.
    assert hass.states.get(T2).state == STATE_PLAYING


async def test_play_media_rejects_garbage(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {ATTR_ENTITY_ID: T1, ATTR_MEDIA_CONTENT_TYPE: "channel", ATTR_MEDIA_CONTENT_ID: "kexp"},
            blocking=True,
        )


async def test_band_frequency_preset(
    hass: HomeAssistant, store_preset_enabled: None, entry: MockConfigEntry, fake: FakeDTNR
) -> None:
    await _call(hass, "media_player", "select_source", {ATTR_ENTITY_ID: T2, ATTR_INPUT_SOURCE: "FM"})
    assert "T2B2" in fake.received
    assert hass.states.get(T2).attributes[ATTR_INPUT_SOURCE] == "FM"

    await _call(
        hass, "number", "set_value",
        {ATTR_ENTITY_ID: "number.elan_dtnr_tuner_2_fm_frequency", "value": 97.1},
    )
    assert "T2B2F0971" in fake.received

    await _call(hass, DOMAIN, "store_preset", {ATTR_ENTITY_ID: T2, "preset": 2})
    assert "T2B2F0971L02" in fake.received
    assert fake.tuners[2].presets[FM] == {2: 971}

    await _call(
        hass, "number", "set_value",
        {ATTR_ENTITY_ID: "number.elan_dtnr_tuner_2_fm_frequency", "value": 101.5},
    )
    await _call(
        hass, "select", "select_option",
        {ATTR_ENTITY_ID: "select.elan_dtnr_tuner_2_preset", "option": "2"},
    )
    assert "T2B2P02" in fake.received
    assert hass.states.get("select.elan_dtnr_tuner_2_preset").state == "2"
    assert hass.states.get(T2).attributes[ATTR_MEDIA_TITLE] == "97.1 FM"


def test_features_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not features.enabled("store_preset")
    monkeypatch.setenv("FEATURES", "other, Store_Preset")
    assert features.enabled("store_preset")
    monkeypatch.setenv("FEATURES", "store_presets")
    assert not features.enabled("store_preset")


async def test_store_preset_off_by_default(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert hass.states.get(T1).state == STATE_PLAYING
    assert not hass.services.has_service(DOMAIN, "store_preset")


async def test_store_preset_uses_current_station(
    hass: HomeAssistant, store_preset_enabled: None, entry: MockConfigEntry, fake: FakeDTNR
) -> None:
    """A front-panel retune since the last poll is what gets stored."""
    fake.tuners[1].freq[FM] = 1011
    await _call(hass, DOMAIN, "store_preset", {ATTR_ENTITY_ID: T1, "preset": 3})
    assert "T1B2F1011L03" in fake.received
    assert fake.tuners[1].presets[FM] == {3: 1011}
    assert hass.states.get(T1).attributes[ATTR_MEDIA_TITLE] == "101.1 FM"


async def test_store_preset_refuses_when_off(
    hass: HomeAssistant, store_preset_enabled: None, entry: MockConfigEntry, fake: FakeDTNR
) -> None:
    fake.set_power(False)  # switched off since the last poll
    with pytest.raises(ServiceValidationError, match="must be on"):
        await hass.services.async_call(
            DOMAIN, "store_preset", {ATTR_ENTITY_ID: T1, "preset": 3}, blocking=True
        )
    assert not [c for c in fake.received if "L" in c]
    assert not fake.tuners[1].power


async def test_buttons_and_tracks(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeDTNR
) -> None:
    await _call(hass, "button", "press", {ATTR_ENTITY_ID: "button.elan_dtnr_tuner_1_step_up"})
    assert "T1M1" in fake.received
    assert hass.states.get("number.elan_dtnr_tuner_1_fm_frequency").state == "106.5"

    await _call(hass, "button", "press", {ATTR_ENTITY_ID: "button.elan_dtnr_tuner_1_seek_down"})
    assert "T1A0" in fake.received
    await _advance(hass, 5)  # the seek's second, delayed refresh

    fake.tuners[1].presets[FM] = {1: 1011, 2: 1025}
    await _call(hass, "media_player", "media_next_track", {ATTR_ENTITY_ID: T1})
    assert "T1N1" in fake.received
    assert hass.states.get(T1).attributes[ATTR_MEDIA_TITLE] == "101.1 FM"
    await _call(hass, "media_player", "media_previous_track", {ATTR_ENTITY_ID: T1})
    assert "T1N0" in fake.received


async def test_unavailable_then_recovers(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeDTNR
) -> None:
    fake.mute = True
    await _advance(hass, 11)
    assert hass.states.get(T1).state == STATE_UNAVAILABLE

    fake.mute = False
    await fake.drop_clients()
    await _advance(hass, 22)
    assert hass.states.get(T1).state == STATE_PLAYING


async def test_unsolicited_updates(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeDTNR
) -> None:
    fake.push = True
    fake.tuners[1].band = AM
    # Any command makes the emulator push tuner 1's status.
    await entry.runtime_data.client.send("T1B1")
    await asyncio.sleep(0.1)  # the reader is a plain asyncio task
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(T1).attributes[ATTR_INPUT_SOURCE] == "AM"


async def test_setup_retries_when_bridge_down(hass: HomeAssistant, socket_enabled: None) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_DEVICE: "socket://127.0.0.1:1"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_closes_connection(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeDTNR
) -> None:
    client = entry.runtime_data.client
    assert client.connected
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not client.connected


async def test_command_during_poll_is_not_overwritten(
    hass: HomeAssistant, entry: MockConfigEntry, fake: FakeDTNR
) -> None:
    """A poll that started before a command must not leave pre-command state.

    HA's debouncer drops refresh requests made while a refresh is running, so
    the command's own read-back (not a requested refresh) has to settle it.
    """
    coordinator = entry.runtime_data
    poll = hass.async_create_task(coordinator.async_refresh())
    await asyncio.sleep(0)  # let the poll send its first query
    await hass.services.async_call(
        "media_player",
        "play_media",
        {ATTR_ENTITY_ID: T1, ATTR_MEDIA_CONTENT_TYPE: "channel", ATTR_MEDIA_CONTENT_ID: "88.5"},
        blocking=True,
    )
    await poll
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(T1).attributes[ATTR_MEDIA_TITLE] == "88.5 FM"
