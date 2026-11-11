"""Preset selection, per tuner."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import TUNERS
from .coordinator import ElanDtnrConfigEntry, ElanDtnrCoordinator
from .entity import ElanDtnrEntity
from .protocol import PRESET_MAX, PRESET_MIN, Band, TunerState, cmd_recall_preset

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElanDtnrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a preset select for each tuner."""
    coordinator = entry.runtime_data
    async_add_entities(ElanDtnrPreset(coordinator, tuner) for tuner in TUNERS)


class ElanDtnrPreset(ElanDtnrEntity, SelectEntity):
    """Recall one of the 30 presets on the current band."""

    _attr_translation_key = "preset"
    _attr_options = [str(n) for n in range(PRESET_MIN, PRESET_MAX + 1)]

    def __init__(self, coordinator: ElanDtnrCoordinator, tuner: int) -> None:
        """Initialise."""
        super().__init__(coordinator, tuner, "preset")

    @property
    def current_option(self) -> str | None:
        """The selected preset, or none when tuned off-preset."""
        state = self.tuner_state
        if state is None or not state.power or state.preset is None:
            return None
        return str(state.preset)

    async def async_select_option(self, option: str) -> None:
        """Recall the preset."""
        preset = int(option)

        def build(state: TunerState) -> str:
            return cmd_recall_preset(self.tuner, state.band or Band.FM, preset)

        await self.coordinator.async_tuner_command(self.tuner, build)
