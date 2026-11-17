"""Polling coordinator for the ELAN DTNR."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import DTNRClient, DTNRError
from .const import (
    DOMAIN,
    POWER_POLL_INTERVAL,
    POWER_TIMEOUT,
    SCAN_INTERVAL,
    SEEK_REFRESH_DELAY,
    TUNERS,
)
from .protocol import TunerState, cmd_power

_LOGGER = logging.getLogger(__name__)

type ElanDtnrConfigEntry = ConfigEntry[ElanDtnrCoordinator]


class ElanDtnrCoordinator(DataUpdateCoordinator[dict[int, TunerState]]):
    """Polls both tuners and sends commands."""

    config_entry: ElanDtnrConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ElanDtnrConfigEntry, client: DTNRClient
    ) -> None:
        """Initialise."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        client.on_state = self._handle_unsolicited
        self.unit_device_id: str | None = None  # device registry id of the unit
        self._unsub_delayed_refresh: CALLBACK_TYPE | None = None
        # Held across a whole poll and a whole command, so a command can't land
        # between a poll's two queries and be overwritten by its stale half.
        self._lock = asyncio.Lock()

    async def _async_update_data(self) -> dict[int, TunerState]:
        async with self._lock:
            try:
                return {tuner: await self.client.query(tuner) for tuner in TUNERS}
            except DTNRError as err:
                raise UpdateFailed(str(err)) from err

    @callback
    def _handle_unsolicited(self, state: TunerState) -> None:
        """Take status lines the tuner sends without being asked."""
        if self.data is not None:
            self.async_set_updated_data({**self.data, state.tuner: state})

    async def async_tuner_command(
        self,
        tuner: int,
        build: Callable[[TunerState], str],
        *,
        power_on: bool = True,
        slow: bool = False,
    ) -> None:
        """Send a command built from the tuner's current state, then re-read it.

        The state passed to ``build`` is always freshly queried, never the last
        poll's. With ``power_on`` the unit is switched on first if needed,
        which brings both tuners on. ``slow`` adds a later refresh for commands
        such as seek that take a while.
        """
        async with self._lock:
            try:
                switched = False
                if power_on:
                    state, switched = await self._async_power(True, tuner)
                else:
                    state = await self.client.query(tuner)
                command = build(state)
                await self.client.send(command)
            except DTNRError as err:
                self._refresh_in_background()
                raise HomeAssistantError(f"Error talking to the DTNR: {err}") from err
            except HomeAssistantError:
                # The unit may have come on after we gave up waiting.
                self._refresh_in_background()
                raise
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err
            try:
                states = {tuner: await self.client.query(tuner)}
                if switched:
                    # Switching the unit on brought the other tuner on too.
                    states.update(await self._query_others(tuner))
            except DTNRError as err:
                # The command went out; only the read-back failed.
                _LOGGER.debug("Re-reading after %s failed: %s", command, err)
                states = {}
            if states:
                self.async_set_updated_data({**self.data, **states})
        if not states:
            await self.async_request_refresh()
        if slow:
            self._schedule_delayed_refresh()

    async def async_set_power(self, on: bool) -> None:
        """Switch the unit, both tuners, on or off; show it once it reports it."""
        async with self._lock:
            try:
                state, _ = await self._async_power(on)
                states = {TUNERS[0]: state, **await self._query_others(TUNERS[0])}
            except DTNRError as err:
                self._refresh_in_background()
                raise HomeAssistantError(f"Error talking to the DTNR: {err}") from err
            except HomeAssistantError:
                self._refresh_in_background()
                raise
            self.async_set_updated_data({**self.data, **states})

    async def _query_others(self, tuner: int) -> dict[int, TunerState]:
        """Query every tuner but the one already read."""
        return {other: await self.client.query(other) for other in TUNERS if other != tuner}

    @callback
    def _refresh_in_background(self) -> None:
        """Re-read both tuners once the lock is free, bypassing the debouncer."""
        self.config_entry.async_create_background_task(
            self.hass, self.async_refresh(), "elan_dtnr refresh after failure"
        )

    async def _async_power(
        self, on: bool, tuner: int = TUNERS[0]
    ) -> tuple[TunerState, bool]:
        """Switch the unit on or off if it isn't already, and wait until it says so.

        Power is the whole unit's (X1/X0 switch both tuners), so asking one
        tuner tells. It asks rather than trusting the last poll, since the unit
        may have been switched from its front panel or IR since, and a tuner
        ignores tuning while off. It keeps asking after X1 because the unit may
        take a moment to come on, so the first answer can still be X0. Returns
        that tuner's state, and whether the unit had to be switched.
        """
        state = await self.client.query(tuner)
        if state.power == on:
            return state, False
        await self.client.send(cmd_power(on))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + POWER_TIMEOUT
        while True:
            state = await self.client.query(tuner)
            if state.power == on or loop.time() >= deadline:
                break
            await asyncio.sleep(POWER_POLL_INTERVAL)
        if state.power != on:
            raise HomeAssistantError(f"The DTNR did not turn {'on' if on else 'off'}")
        return state, True

    @callback
    def _schedule_delayed_refresh(self) -> None:
        if self._unsub_delayed_refresh is not None:
            self._unsub_delayed_refresh()

        @callback
        def _refresh(_now: datetime) -> None:
            self._unsub_delayed_refresh = None
            self.config_entry.async_create_background_task(
                self.hass, self.async_request_refresh(), "elan_dtnr delayed refresh"
            )

        self._unsub_delayed_refresh = async_call_later(
            self.hass, SEEK_REFRESH_DELAY, _refresh
        )

    async def async_shutdown(self) -> None:
        """Stop polling and close the connection."""
        await super().async_shutdown()
        if self._unsub_delayed_refresh is not None:
            self._unsub_delayed_refresh()
            self._unsub_delayed_refresh = None
        await self.client.disconnect()
