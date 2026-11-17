"""Media player entities: one per DTNR tuner."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import features
from .const import ATTR_PRESET, SERVICE_STORE_PRESET, TUNERS
from .coordinator import ElanDtnrConfigEntry, ElanDtnrCoordinator
from .entity import ElanDtnrEntity
from .protocol import (
    PRESET_MAX,
    PRESET_MIN,
    Band,
    TunerState,
    cmd_band,
    cmd_preset_step,
    cmd_recall_preset,
    cmd_store_preset,
    cmd_tune,
    parse_media_id,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElanDtnrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a media player for each tuner."""
    coordinator = entry.runtime_data
    async_add_entities(ElanDtnrMediaPlayer(coordinator, tuner) for tuner in TUNERS)

    # Storing overwrites the tuner's preset memory, so it's opt-in.
    if not features.enabled(features.STORE_PRESET):
        return
    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_STORE_PRESET,
        {
            vol.Required(ATTR_PRESET): vol.All(
                vol.Coerce(int), vol.Range(min=PRESET_MIN, max=PRESET_MAX)
            )
        },
        "async_store_preset",
    )


class ElanDtnrMediaPlayer(ElanDtnrEntity, MediaPlayerEntity):
    """One DTNR tuner.

    It has no on/off of its own: power is the whole unit's (the unit's power
    switch, switch.py), and any command switches the unit on first.
    """

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_media_content_type = MediaType.CHANNEL
    _attr_source_list = [band.name for band in Band]
    _attr_supported_features = (
        MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.PLAY_MEDIA
    )

    def __init__(self, coordinator: ElanDtnrCoordinator, tuner: int) -> None:
        """Initialise."""
        super().__init__(coordinator, tuner, "media_player")

    @property
    def _on_state(self) -> TunerState | None:
        state = self.tuner_state
        return state if state is not None and state.power else None

    @property
    def state(self) -> MediaPlayerState | None:
        """Playing while the unit is on, off while it's off.

        A tuner that's on is always playing its station. Home Assistant also
        shows previous and next (here preset down and up) only for a player
        that's playing or paused.
        """
        if (state := self.tuner_state) is None:
            return None
        return MediaPlayerState.PLAYING if state.power else MediaPlayerState.OFF

    @property
    def source(self) -> str | None:
        """The band, AM or FM."""
        if (state := self._on_state) is None or state.band is None:
            return None
        return state.band.name

    @property
    def media_title(self) -> str | None:
        """The station, e.g. 106.3 FM."""
        if (state := self._on_state) is None:
            return None
        return state.frequency_label

    @property
    def media_channel(self) -> str | None:
        """The preset, if one is selected."""
        if (state := self._on_state) is None or state.preset is None:
            return None
        return f"Preset {state.preset}"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Frequency and preset, for templates and automations."""
        if (state := self._on_state) is None:
            return None
        return {"frequency": state.frequency, "preset": state.preset}

    async def async_select_source(self, source: str) -> None:
        """Switch band; the tuner returns to the last station on it."""
        try:
            band = Band[source]
        except KeyError as err:
            raise ServiceValidationError(f"Unknown source {source!r}") from err
        await self.coordinator.async_tuner_command(
            self.tuner, lambda _: cmd_band(self.tuner, band)
        )

    async def async_media_next_track(self) -> None:
        """Next preset."""
        await self.coordinator.async_tuner_command(
            self.tuner, lambda _: cmd_preset_step(self.tuner, True)
        )

    async def async_media_previous_track(self) -> None:
        """Previous preset."""
        await self.coordinator.async_tuner_command(
            self.tuner, lambda _: cmd_preset_step(self.tuner, False)
        )

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Tune to a station: '101.1', 'FM 101.1', 'AM 1010', 'preset 5'."""
        try:
            target = parse_media_id(media_id)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

        def build(state: TunerState) -> str:
            band = target.band or state.band or Band.FM
            if target.preset is not None:
                return cmd_recall_preset(self.tuner, band, target.preset)
            if target.frequency is not None:
                return cmd_tune(self.tuner, band, target.frequency)
            return cmd_band(self.tuner, band)

        await self.coordinator.async_tuner_command(self.tuner, build)

    async def async_store_preset(self, preset: int) -> None:
        """Store the station the tuner is on now in a preset slot.

        The station comes from a fresh query, not the last poll, since the
        store command also tunes and would otherwise retune to a stale
        station. A unit that's off is refused rather than switched on.
        """

        def build(state: TunerState) -> str:
            if not state.power or state.band is None or state.frequency is None:
                raise ServiceValidationError(
                    f"Tuner {self.tuner} must be on to store a preset"
                )
            return cmd_store_preset(self.tuner, state.band, state.frequency, preset)

        await self.coordinator.async_tuner_command(self.tuner, build, power_on=False)
