"""Button platform for Yeedi integration.

5 buttons, one per ha-button node in the Node-RED flow.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BUTTONS, DEVICE_MANUFACTURER, DEVICE_MODEL, DEVICE_NAME, DOMAIN
from .coordinator import YeediCoordinator

from . import DOMAIN as _DOMAIN  # noqa: F401  (avoid circular issues; same module)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the 5 button entities."""
    coordinator: YeediCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    client = hass.data[DOMAIN][entry.entry_id]["client"]
    entities: list[ButtonEntity] = []
    for device_did, device in coordinator.devices.items():
        for spec in BUTTONS:
            entities.append(
                YeediButton(coordinator, client, device_did, device, spec)
            )
    async_add_entities(entities)


class YeediButton(CoordinatorEntity[YeediCoordinator], ButtonEntity):
    """One button per ha-button node in the flow."""

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
        self._attr_unique_id = f"{device_did}__btn__{spec['name']}"
        self._command = spec["command"]
        self._arg = spec.get("arg")

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_did)},
            "name": DEVICE_NAME,
            "manufacturer": DEVICE_MANUFACTURER,
            "model": DEVICE_MODEL,
        }

    async def async_press(self) -> None:
        args: dict[str, Any] = {}
        if self._arg is not None:
            args["arg"] = self._arg
        await self._client.async_send_command(self._device, self._command, args)