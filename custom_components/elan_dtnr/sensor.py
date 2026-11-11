"""Reception sensor, per tuner."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import TUNERS
from .coordinator import ElanDtnrConfigEntry, ElanDtnrCoordinator
from .entity import ElanDtnrEntity
from .protocol import Reception

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElanDtnrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a reception sensor for each tuner."""
    coordinator = entry.runtime_data
    async_add_entities(ElanDtnrReception(coordinator, tuner) for tuner in TUNERS)


class ElanDtnrReception(ElanDtnrEntity, SensorEntity):
    """No signal, mono or stereo."""

    _attr_translation_key = "reception"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [Reception.NO_SIGNAL, Reception.MONO, Reception.STEREO]
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ElanDtnrCoordinator, tuner: int) -> None:
        """Initialise."""
        super().__init__(coordinator, tuner, "reception")

    @property
    def native_value(self) -> str | None:
        """Reception status; unknown while the tuner is off."""
        if (state := self.tuner_state) is None:
            return None
        return state.reception
