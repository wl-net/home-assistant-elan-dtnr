"""Seek and step buttons, per tuner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import TUNERS
from .coordinator import ElanDtnrConfigEntry, ElanDtnrCoordinator
from .entity import ElanDtnrEntity
from .protocol import cmd_seek, cmd_step

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ElanDtnrButtonDescription(ButtonEntityDescription):
    """A button that sends one tuner command."""

    command: Callable[[int], str]
    slow: bool = False


BUTTONS: tuple[ElanDtnrButtonDescription, ...] = (
    ElanDtnrButtonDescription(
        key="seek_up",
        translation_key="seek_up",
        command=lambda tuner: cmd_seek(tuner, True),
        slow=True,
    ),
    ElanDtnrButtonDescription(
        key="seek_down",
        translation_key="seek_down",
        command=lambda tuner: cmd_seek(tuner, False),
        slow=True,
    ),
    ElanDtnrButtonDescription(
        key="step_up",
        translation_key="step_up",
        command=lambda tuner: cmd_step(tuner, True),
    ),
    ElanDtnrButtonDescription(
        key="step_down",
        translation_key="step_down",
        command=lambda tuner: cmd_step(tuner, False),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ElanDtnrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the buttons for each tuner."""
    coordinator = entry.runtime_data
    async_add_entities(
        ElanDtnrButton(coordinator, tuner, description)
        for tuner in TUNERS
        for description in BUTTONS
    )


class ElanDtnrButton(ElanDtnrEntity, ButtonEntity):
    """Seek or step one tuner."""

    entity_description: ElanDtnrButtonDescription

    def __init__(
        self,
        coordinator: ElanDtnrCoordinator,
        tuner: int,
        description: ElanDtnrButtonDescription,
    ) -> None:
        """Initialise."""
        super().__init__(coordinator, tuner, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Send the command."""
        await self.coordinator.async_tuner_command(
            self.tuner,
            lambda _: self.entity_description.command(self.tuner),
            slow=self.entity_description.slow,
        )
