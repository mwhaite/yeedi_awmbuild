"""Yeedi select entities — the 2 selects from the Node-RED Tomschin flow.

- yeedi_water_level_select  -> SetWaterInfo (LOW/MEDIUM/HIGH/ULTRAHIGH)
- yeedi_clean_speed_select  -> SetFanSpeed  (QUIET/NORMAL/MAX/MAX_PLUS)

Both subscribe to the device's event bus for current value, and
on user change send the appropriate deebot_client command.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from deebot_client.commands.json import SetFanSpeed, SetWaterInfo
from deebot_client.commands.json.clean import CleanAreaV2, CleanMode
from deebot_client.commands.json.fan_speed import FanSpeedLevel
from deebot_client.commands.json.water_info import WaterAmount
from deebot_client.device import Device
from deebot_client.events import FanSpeedEvent, RoomsEvent, WaterInfoEvent

from homeassistant.components.select import SelectEntity
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
    """Create both selects for every device on the entry."""
    controller = entry.runtime_data
    new_entities: list[SelectEntity] = []
    for device in controller.devices:
        new_entities.extend(_build_selects_for_device(device))
    async_add_entities(new_entities, update_before_add=True)


def _build_selects_for_device(device: Device) -> list[SelectEntity]:
    nick = device.device_info.get("nick") or device.device_info.get("name", "yeedi")
    return [
        _WaterLevelSelect(device, nick),
        _CleanSpeedSelect(device, nick),
        _RoomSelect(device, nick),
    ]


class _WaterLevelSelect(SelectEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Water level"
    _attr_icon = "mdi:water-percent"
    _attr_options = [m.name.title() for m in WaterAmount]
    _attr_unique_id_suffix = "water_level"

    def __init__(self, device: Device, nick: str) -> None:
        self._device = device
        self._attr_unique_id = f"{device.device_info['did']}_water_level_select"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_info["did"])},
            "manufacturer": "Yeedi",
            "model": device.device_info.get("deviceName"),
            "serial_number": device.device_info.get("name"),
            "sw_version": device.fw_version,
        }
        self._unsub: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        @callback
        def _on(event: WaterInfoEvent) -> None:
            # deebot_client emits `amount` as a WaterAmount value;
            # display the named level to match the spec.
            try:
                self._attr_current_option = WaterAmount(event.amount).name.title()
            except ValueError:
                self._attr_current_option = None
            self.async_write_ha_state()
        self._unsub = self._device.events.subscribe(WaterInfoEvent, _on)

    async def async_select_option(self, option: str) -> None:
        try:
            amount = WaterAmount[option.upper()]
        except KeyError as exc:
            _LOGGER.error("Unknown water level: %s", option)
            return
        try:
            await self._device.execute_command(SetWaterInfo(amount=amount))
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to set water level to %s: %s", option, err)


class _CleanSpeedSelect(SelectEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Clean speed"
    _attr_icon = "mdi:fan"
    _attr_options = [
        FanSpeedLevel.QUIET.name.title(),
        FanSpeedLevel.NORMAL.name.title(),
        FanSpeedLevel.MAX.name.title(),
        FanSpeedLevel.MAX_PLUS.name.title(),
    ]
    _attr_unique_id_suffix = "clean_speed"

    def __init__(self, device: Device, nick: str) -> None:
        self._device = device
        self._attr_unique_id = f"{device.device_info['did']}_clean_speed_select"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_info["did"])},
            "manufacturer": "Yeedi",
            "model": device.device_info.get("deviceName"),
            "serial_number": device.device_info.get("name"),
            "sw_version": device.fw_version,
        }
        self._unsub: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        @callback
        def _on(event: FanSpeedEvent) -> None:
            try:
                self._attr_current_option = FanSpeedLevel(event.speed).name.title()
            except ValueError:
                self._attr_current_option = None
            self.async_write_ha_state()
        self._unsub = self._device.events.subscribe(FanSpeedEvent, _on)

    async def async_select_option(self, option: str) -> None:
        try:
            speed = FanSpeedLevel[option.upper()]
        except KeyError as exc:
            _LOGGER.error("Unknown fan speed: %s", option)
            return
        try:
            await self._device.execute_command(SetFanSpeed(speed=speed))
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to set fan speed to %s: %s", option, err)


class _RoomSelect(SelectEntity):
    """A select that lists the rooms the bot has mapped.

    The room list is populated from ``RoomsEvent``, which deebot_client
    fires when all rooms for the current map have been received. Until
    then, the select has no options and the user can't pick anything.
    When the user picks a room, we send ``CleanAreaV2`` with
    ``mode=spotArea`` and the room's id. (Custom areas are a separate
    flow that requires a drawn boundary; the spec only requires rooms.)
    """
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Clean room"
    _attr_icon = "mdi:floor-plan"
    _attr_options: list[str] = []

    def __init__(self, device: Device, nick: str) -> None:
        self._device = device
        self._attr_unique_id = f"{device.device_info['did']}_room_select"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_info["did"])},
            "manufacturer": "Yeedi",
            "model": device.device_info.get("deviceName"),
            "serial_number": device.device_info.get("name"),
            "sw_version": device.fw_version,
        }
        # Map of room id -> room name for command-back
        self._room_ids: dict[str, str] = {}
        self._unsub: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        @callback
        def _on(event: RoomsEvent) -> None:
            # Build the option list from the event and remember the
            # room id for each name so we can send the right command.
            self._attr_options = [r.name for r in event.rooms]
            self._room_ids = {r.name: r.id for r in event.rooms}
            if self._attr_options:
                # Don't auto-pick; leave current_option None so the
                # user must explicitly choose a room.
                self._attr_current_option = None
            self.async_write_ha_state()
        self._unsub = self._device.events.subscribe(RoomsEvent, _on)

    async def async_select_option(self, option: str) -> None:
        room_id = self._room_ids.get(option)
        if room_id is None:
            _LOGGER.error("Unknown room: %s", option)
            return
        try:
            await self._device.execute_command(
                CleanAreaV2(mode=CleanMode.SPOT_AREA, area=room_id)
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to clean room %s: %s", option, err)
