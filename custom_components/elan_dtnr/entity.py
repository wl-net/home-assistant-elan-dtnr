"""Base entity for the ELAN DTNR."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import ElanDtnrCoordinator
from .protocol import TunerState


class ElanDtnrEntity(CoordinatorEntity[ElanDtnrCoordinator]):
    """An entity belonging to one of the two tuners."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ElanDtnrCoordinator, tuner: int, key: str) -> None:
        """Initialise."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self.tuner = tuner
        self._attr_unique_id = f"{entry.entry_id}_tuner{tuner}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_tuner{tuner}")},
            name=f"{entry.title} Tuner {tuner}",
            manufacturer=MANUFACTURER,
            model=MODEL,
        )
        if coordinator.unit_device_id is not None:
            self._attr_device_info["via_device_id"] = coordinator.unit_device_id

    @property
    def tuner_state(self) -> TunerState | None:
        """This tuner's latest state."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self.tuner)

    @property
    def available(self) -> bool:
        """Available while the tuner is answering."""
        return super().available and self.tuner_state is not None
