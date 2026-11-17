"""The unit's power switch: the DTNR switches both tuners together."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ElanDtnrConfigEntry, ElanDtnrCoordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElanDtnrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the unit's power switch."""
    async_add_entities([ElanDtnrPower(entry.runtime_data)])


class ElanDtnrPower(CoordinatorEntity[ElanDtnrCoordinator], SwitchEntity):
    """On or off, for both tuners: the DTNR has no per-tuner power (X1/X0)."""

    _attr_has_entity_name = True
    _attr_translation_key = "power"

    def __init__(self, coordinator: ElanDtnrCoordinator) -> None:
        """Initialise, on the unit's own device (created in __init__.py)."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_power"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def is_on(self) -> bool | None:
        """On while the tuners report themselves on; they always agree."""
        if not self.coordinator.data:
            return None
        return any(state.power for state in self.coordinator.data.values())

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch the unit on (X1)."""
        await self.coordinator.async_set_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch the unit off (X0)."""
        await self.coordinator.async_set_power(False)
