"""The ELAN DTNR digital dual tuner integration."""

from __future__ import annotations

from homeassistant.const import CONF_DEVICE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .client import DTNRClient
from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import ElanDtnrConfigEntry, ElanDtnrCoordinator

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ElanDtnrConfigEntry) -> bool:
    """Set up a DTNR from a config entry."""
    coordinator = ElanDtnrCoordinator(hass, entry, DTNRClient(entry.data[CONF_DEVICE]))
    try:
        # Raises ConfigEntryNotReady if the tuner can't be reached, so Home
        # Assistant retries (e.g. while the serial bridge is still starting).
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.client.disconnect()
        raise
    entry.runtime_data = coordinator

    # The unit itself; each tuner is its own device under it.
    unit = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        model=MODEL,
        name=entry.title,
    )
    coordinator.unit_device_id = unit.id

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ElanDtnrConfigEntry) -> bool:
    """Unload a config entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
    return unloaded
