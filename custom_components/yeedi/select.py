"""Select platform for Yeedi integration.

2 selects, one per ha-select node in the Node-RED flow.
On async_select_option we mirror the flow's change-node rewrite:
    arg = $number(payload); payload = 'SetWaterLevel' | 'SetCleanSpeed'

The flow's trigger nodes fired the api-call-service select.select_option
to write the current value back to itself on every event. We do the same
once on platform setup (writes the startup_value from SENSORS).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_OPTION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEVICE_MANUFACTURER,
    DEVICE_MODEL,
    DEVICE_NAME,
    DOMAIN,
    SELECTS,
)
from .coordinator import YeediCoordinator

_LOGGER = logging.getLogger(__name__)


# Options exposed by each select. The flow leaves options=[] in the ha-select
# node (the JS lib decides the options dynamically per model). For the Yeedi
# 2 hybrid (the flow's documented model), the values are 1=low/medium and
# 1/2 for clean speed.
OPTION_OPTIONS: dict[str, list[str]] = {
    "Water level select": ["1", "2", "3"],   # low / medium / high
    "Clean Speed":        ["1", "2"],         # standard / max
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the 2 select entities."""
    coordinator: YeediCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    client = hass.data[DOMAIN][entry.entry_id]["client"]
    entities: list[SelectEntity] = []
    for device_did, device in coordinator.devices.items():
        for spec in SELECTS:
            ent = YeediSelect(coordinator, client, device_did, device, spec)
            entities.append(ent)
    async_add_entities(entities)


class YeediSelect(CoordinatorEntity[YeediCoordinator], SelectEntity):
    """One select per ha-select node in the flow."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: YeediCoordinator,
        client,
        device_did: str,
        device,
        spec: dict,
    ) -> None:
        super().__init__(coordinator)
        self._client = client
        self._device_did = device_did
        self._device = device
        self._attr_name = spec["name"]
        self._attr_unique_id = f"{device_did}__sel__{spec['name']}"
        self._attr_options = OPTION_OPTIONS.get(spec["name"], [])
        self._command = spec["command"]
        self._startup_value = spec.get("startup_value")
        self._writeback_done = False

    async def async_added_to_hass(self) -> None:
        """On startup, write the configured value back to the bot.

        Mirrors the flow's two trigger nodes that fired api-call-service
        select.select_option on every event. We only need to fire once.
        """
        await super().async_added_to_hass()
        if self._writeback_done or self._startup_value is None:
            return
        self._writeback_done = True
        try:
            await self._client.async_send_command(
                self._device, self._command, {"arg": self._startup_value}
            )
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Select startup write-back failed", exc_info=True)

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_did)},
            "name": DEVICE_NAME,
            "manufacturer": DEVICE_MANUFACTURER,
            "model": DEVICE_MODEL,
        }

    @property
    def current_option(self) -> str | None:
        event_for_value = {
            "Water level select": "WaterLevel",
            "Clean Speed": "CleanSpeed",
        }.get(self._attr_name)
        if not event_for_value:
            return None
        state = (self.coordinator.data or {}).get(self._device_did, {})
        val = state.get(event_for_value)
        if val is None:
            return None
        return str(val)

    async def async_select_option(self, option: str) -> None:
        args: dict[str, Any] = {"arg": option}
        await self._client.async_send_command(self._device, self._command, args)
        await self.coordinator.async_request_refresh()