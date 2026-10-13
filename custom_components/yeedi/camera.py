"""Yeedi camera entities — render the bot's live map as an SVG.

The Tomschin Node-RED flow this integration was reverse-engineered
from explicitly does NOT use maps (enableGetMapsOnStartup=false).
We add the map camera anyway because:

1. The Yeedi C12 PRO PLUS uploads its map to the cloud when running.
2. deebot_client v11's ``Map`` class does the heavy lifting (assembling
   the 7z-compressed map pieces, decoding room polygons, decoding
   trace points, building an SVG).  This integration just exposes it.

The integration does NOT use a built-in HA ``Camera`` PNG because:
- SVG renders sharper in HA's UI at any zoom level
- The Map class already produces SVG via ``get_svg_map()``
- ``cairosvg`` is an optional runtime dep we don't want to force

Set ``content_type = "image/svg+xml"`` so HA serves the SVG directly.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from deebot_client.device import Device
from deebot_client.map import Map
from deebot_client.event_bus import EventBus

from homeassistant.components.camera import Camera
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
    """Create a map camera for every device on the entry."""
    controller = entry.runtime_data
    new_entities: list[Camera] = []
    for device in controller.devices:
        new_entities.append(_YeediMapCamera(device))
    async_add_entities(new_entities, update_before_add=True)


class _YeediMapCamera(Camera):
    """Render the bot's current map as an SVG camera entity."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Map"
    _attr_content_type = "image/svg+xml"

    def __init__(self, device: Device) -> None:
        super().__init__()
        self._device = device
        self._map: Map | None = None
        self._unsubscribers: list[Callable[[], None]] = []
        self._attr_unique_id = f"{device.device_info['did']}_map"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_info["did"])},
            "manufacturer": "Yeedi",
            "model": device.device_info.get("deviceName"),
            "serial_number": device.device_info.get("name"),
            "sw_version": device.fw_version,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Construct the Map and start it.  The Map class wires up
        # the right subscriptions to its event bus on init.
        bus: EventBus = self._device.events
        self._map = Map(
            execute_command=self._device.execute_command,
            event_bus=bus,
        )
        _LOGGER.debug("Map camera ready for %s", self._attr_name)

    async def async_will_remove_from_hass(self) -> None:
        # Map's __init__ subscribes to several events; we don't have
        # a teardown API on the Map class itself in v11, so the
        # references are dropped here.  The bus will be torn down
        # with the device.
        for unsub in self._unsubscribers:
            unsub()
        self._unsubscribers.clear()
        self._map = None
        await super().async_will_remove_from_hass()

    def camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return the current map as an SVG byte string, or None if
        the bot hasn't pushed a map yet."""
        if self._map is None:
            return None
        try:
            svg = self._map.get_svg_map()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Map not yet available for %s: %s", self._attr_name, err)
            return None
        if svg is None:
            return None
        return svg.encode("utf-8")
