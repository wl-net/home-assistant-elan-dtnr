"""Config flow for the ELAN DTNR."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_DEVICE, CONF_NAME
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .client import DTNRClient, DTNRConnectionError, DTNRTimeoutError
from .const import DEFAULT_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

BY_ID_DIR = Path("/dev/serial/by-id")


def _list_ports() -> list[SelectOptionDict]:
    """Local serial ports, by their stable /dev/serial/by-id name where there is one."""
    import serialx  # noqa: PLC0415

    by_id: dict[str, str] = {}
    if BY_ID_DIR.is_dir():
        for link in BY_ID_DIR.iterdir():
            by_id[str(link.resolve())] = str(link)

    options: list[SelectOptionDict] = []
    for info in serialx.list_serial_ports():
        device = by_id.get(info.resolved_device or info.device, info.device)
        description = " ".join(filter(None, (info.manufacturer, info.product)))
        label = f"{device} ({description})" if description else device
        options.append(SelectOptionDict(value=device, label=label))
    return options


async def validate_port(port: str) -> None:
    """Open the port and query tuner 1; raises on failure."""
    client = DTNRClient(port)
    try:
        await client.query(1)
    finally:
        await client.disconnect()


class ElanDtnrConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a DTNR on a serial port or TCP serial bridge."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the port."""
        errors: dict[str, str] = {}
        if user_input is not None:
            port = user_input[CONF_DEVICE].strip()
            await self.async_set_unique_id(port)
            self._abort_if_unique_id_configured()
            try:
                await validate_port(port)
            except DTNRConnectionError:
                errors["base"] = "cannot_connect"
            except DTNRTimeoutError:
                errors["base"] = "no_response"
            except Exception:
                _LOGGER.exception("Unexpected error checking %s", port)
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME) or DEFAULT_NAME,
                    data={CONF_DEVICE: port},
                )

        try:
            ports = await self.hass.async_add_executor_job(_list_ports)
        except Exception:  # noqa: BLE001 - listing is a convenience only
            _LOGGER.debug("Could not list serial ports", exc_info=True)
            ports = []

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEVICE, default=defaults.get(CONF_DEVICE, vol.UNDEFINED)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=ports,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_NAME, default=defaults.get(CONF_NAME, DEFAULT_NAME)
                ): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
