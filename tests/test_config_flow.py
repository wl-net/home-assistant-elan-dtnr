"""Config flow tests."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_DEVICE, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.elan_dtnr.const import DOMAIN

from .fake_dtnr import FakeDTNR

NO_PORTS = patch("custom_components.elan_dtnr.config_flow._list_ports", return_value=[])
NO_SETUP = patch("custom_components.elan_dtnr.async_setup_entry", return_value=True)


async def test_user_flow_over_socket(hass: HomeAssistant, fake: FakeDTNR) -> None:
    with NO_PORTS:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.FORM

    with NO_SETUP:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: f" {fake.url} ", CONF_NAME: "Rack tuner"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Rack tuner"
    assert result["data"] == {CONF_DEVICE: fake.url}
    assert fake.received == ["Q1"]


async def test_user_flow_no_response(hass: HomeAssistant, fake: FakeDTNR) -> None:
    fake.mute = True
    with NO_PORTS:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: fake.url}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_response"}

    # Fixing the bridge and resubmitting succeeds.
    fake.mute = False
    with NO_PORTS, NO_SETUP:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: fake.url}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_cannot_connect(hass: HomeAssistant, socket_enabled: None) -> None:
    with NO_PORTS:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_DEVICE: "socket://127.0.0.1:1"}
        )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_lists_ports(hass: HomeAssistant) -> None:
    """The real port lister runs without error (whatever ports exist)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
