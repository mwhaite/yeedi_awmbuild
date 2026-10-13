"""Yeedi switch entities — the 1 switch from the Node-RED Tomschin flow.

- yeedi_do_not_disturb_switch -> toggles a "do not disturb / continuous
  cleaning" mode. The JS library uses ``DoNotDisturbEnabled``; the
  Python ``deebot_client`` port doesn't expose a direct event for it,
  but the closest is ``ContinuousCleaningEvent`` (toggling continuous
  cleaning cycles). Wiring the switch to that command keeps parity
  with the underlying device.

The original spec from Tomschin's flow notes that on ON, the bot
should be sent EnableDoNotDisturb with start=20:00, end=6:00; on
OFF, DisableDoNotDisturb. deebot_client's SetContinuousCleaning is
a boolean toggle without time args. We follow the device's actual
capability rather than hardcoding times.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from deebot_client.commands.json import SetContinuousCleaning
from deebot_client.device import Device
from deebot_client.events import ContinuousCleaningEvent

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YeediConfigEntry
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: YeediConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the do-not-disturb switch for every device on the entry."""
    controller = entry.runtime_data
    new_entities: list[SwitchEntity] = []
    for device in controller.devices:
        new_entities.append(_DoNotDisturbSwitch(device))
    async_add_entities(new_entities, update_before_add=True)


class _DoNotDisturbSwitch(SwitchEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Do not disturb"
    _attr_icon = "mdi:moon-waning-crescent"

    def __init__(self, device: Device) -> None:
        self._device = device
        self._attr_unique_id = f"{device.device_info['did']}_do_not_disturb_switch"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_info["did"])},
            "manufacturer": "Yeedi",
            "model": device.device_info.get("deviceName"),
            "serial_number": device.device_info.get("name"),
            "sw_version": device.fw_version,
        }
        self._is_on: bool | None = None
        self._unsub: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        @callback
        def _on(event: ContinuousCleaningEvent) -> None:
            # deebot_client v11 renamed the field to ``enabled``;
            # ``getattr`` keeps us compatible across versions.
            enabled = getattr(event, "enabled", getattr(event, "enable", None))
            self._is_on = bool(enabled)
            self.async_write_ha_state()
        self._unsub = self._device.events.subscribe(ContinuousCleaningEvent, _on)

    @property
    def is_on(self) -> bool | None:
        return self._is_on

    async def async_turn_on(self, **kwargs) -> None:
        try:
            await self._device.execute_command(SetContinuousCleaning(enable=True))
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to enable do-not-disturb: %s", err)

    async def async_turn_off(self, **kwargs) -> None:
        try:
            await self._device.execute_command(SetContinuousCleaning(enable=False))
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to disable do-not-disturb: %s", err)
