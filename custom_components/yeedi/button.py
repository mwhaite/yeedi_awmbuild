"""Yeedi button entities — the 5 buttons from the Node-RED Tomschin flow.

Each button, when pressed, sends a command to the device. The
button does NOT track state — it just fires.

Mapping (from reverse_engineered_spec.yaml section 6):
- yeedi_start_automatic_cleaning    -> Clean (start)
- yeedi_return_to_charging_station  -> Charge
- yeedi_pause_the_cleaning          -> Pause
- yeedi_find_me                     -> FindMe ("30" = sound 30)
- yeedi_backwards                   -> MoveBackward
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from deebot_client.commands.json import Charge, Clean, PlaySound
from deebot_client.commands.json.clean import CleanAction
from deebot_client.device import Device

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YeediConfigEntry
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YeediConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create all buttons for every device on the entry."""
    controller = entry.runtime_data
    new_entities: list[ButtonEntity] = []
    for device in controller.devices:
        new_entities.extend(_build_buttons_for_device(device))
    async_add_entities(new_entities)


def _build_buttons_for_device(device: Device) -> list[ButtonEntity]:
    nick = device.device_info.get("nick") or device.device_info.get("name", "yeedi")
    buttons: list[ButtonEntity] = [
        _CommandButton(device, nick, "start_automatic_cleaning", "Start cleaning",
                       lambda: Clean(CleanAction.START)),
        _CommandButton(device, nick, "return_to_charging_station", "Return to dock",
                       lambda: Charge()),
        _CommandButton(device, nick, "pause_the_cleaning", "Pause cleaning",
                       lambda: Clean(CleanAction.PAUSE)),
        _CommandButton(device, nick, "find_me", "Find me",
                       lambda: PlaySound()),
    ]
    # The 5th button in the spec (MoveBackward) is not in the
    # deebot_client command set; the Python port never shipped it.
    # We skip it rather than emit a button that fails on press.
    return buttons


class _CommandButton(ButtonEntity):
    """Generic Yeedi button that fires a deebot_client command on press."""
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(
        self,
        device: Device,
        friendly_root: str,
        key: str,
        label: str,
        command_factory: Callable[[], object],
    ) -> None:
        self._device = device
        self._command_factory = command_factory
        self._attr_name = label
        self._attr_unique_id = f"{device.device_info['did']}_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_info["did"])},
            "manufacturer": "Yeedi",
            "model": device.device_info.get("deviceName"),
            "serial_number": device.device_info.get("name"),
            "sw_version": device.fw_version,
        }

    async def async_press(self) -> None:
        try:
            await self._device.execute_command(self._command_factory())
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to press button on %s: %s", self._attr_name, err)
