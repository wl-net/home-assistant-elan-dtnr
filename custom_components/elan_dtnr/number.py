"""Frequency entities: FM and AM, per tuner."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import UnitOfFrequency
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import TUNERS
from .coordinator import ElanDtnrConfigEntry, ElanDtnrCoordinator
from .entity import ElanDtnrEntity
from .protocol import (
    AM_MAX_KHZ,
    AM_MIN_KHZ,
    AM_STEP_KHZ,
    FM_MAX_MHZ,
    FM_MIN_MHZ,
    FM_STEP_MHZ,
    Band,
    cmd_tune,
)

PARALLEL_UPDATES = 0

_LIMITS = {
    Band.FM: (FM_MIN_MHZ, FM_MAX_MHZ, FM_STEP_MHZ, UnitOfFrequency.MEGAHERTZ),
    Band.AM: (AM_MIN_KHZ, AM_MAX_KHZ, AM_STEP_KHZ, UnitOfFrequency.KILOHERTZ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElanDtnrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add FM and AM frequency entities for each tuner."""
    coordinator = entry.runtime_data
    async_add_entities(
        ElanDtnrFrequency(coordinator, tuner, band) for tuner in TUNERS for band in Band
    )


class ElanDtnrFrequency(ElanDtnrEntity, NumberEntity):
    """The frequency on one band; setting it switches to that band and tunes.

    Shows the current frequency while the tuner is on this band, and the last
    one seen on it otherwise.
    """

    _attr_device_class = NumberDeviceClass.FREQUENCY
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: ElanDtnrCoordinator, tuner: int, band: Band) -> None:
        """Initialise."""
        key = f"{band.name.lower()}_frequency"
        super().__init__(coordinator, tuner, key)
        self._attr_translation_key = key
        self.band = band
        low, high, step, unit = _LIMITS[band]
        self._attr_native_min_value = low
        self._attr_native_max_value = high
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._last: float | None = None
        self._remember()

    @callback
    def _remember(self) -> None:
        state = self.tuner_state
        if state is not None and state.power and state.band is self.band:
            self._last = state.frequency

    @callback
    def _handle_coordinator_update(self) -> None:
        self._remember()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float | None:
        """Current (or last-known) frequency on this band."""
        return self._last

    async def async_set_native_value(self, value: float) -> None:
        """Tune to the frequency."""
        await self.coordinator.async_tuner_command(
            self.tuner, lambda _: cmd_tune(self.tuner, self.band, value)
        )
