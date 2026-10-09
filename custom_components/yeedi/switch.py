"""Switch platform for Yeedi integration.

1 switch (Do Not Disturb), mirroring the ha-switch node in the flow.
The flow's switch had two output ports: on -> EnableDoNotDisturb(20:00, 6:00),
off -> DisableDoNotDisturb.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEVICE_MANUFACTURER,
    DEVICE_MODEL,
    DEVICE_NAME,
    DOMAIN,
    SWITCHES,
)
from .coordinator import YeediCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the 1 switch entity."""
    coordinator: YeediCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    client = hass.data[DOMAIN][entry.entry_id]["client"]
    entities: list[SwitchEntity] = []
    for device_did, device in coordinator.devices.items():
        for spec in SWITCHES:
            entities.append(YeediSwitch(coordinator, client, device_did, device, spec))
    async_add_entities(entities)


class YeediSwitch(CoordinatorEntity[YeediCoordinator], SwitchEntity):
    """One switch per ha-switch node in the flow."""

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
        self._attr_unique_id = f"{device_did}__sw__{spec['name']}"
        self._on_command = spec["on_command"]
        self._on_arg = spec.get("on_arg")
        self._on_arg2 = spec.get("on_arg2")
        self._off_command = spec["off_command"]

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_did)},
            "name": DEVICE_NAME,
            "manufacturer": DEVICE_MANUFACTURER,
            "model": DEVICE_MODEL,
        }

    @property
    def is_on(self) -> bool | None:
        state = (self.coordinator.data or {}).get(self._device_did, {})
        v = state.get("DoNotDisturbEnabled")
        if v is None:
            return None
        return bool(int(v))

    async def async_turn_on(self, **kwargs: Any) -> None:
        args: dict[str, Any] = {}
        if self._on_arg is not None:
            args["arg"] = self._on_arg
        if self._on_arg2 is not None:
            args["arg2"] = self._on_arg2
        await self._client.async_send_command(
            self._device, self._on_command, args
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._client.async_send_command(
            self._device, self._off_command, {}
        )
        await self.coordinator.async_request_refresh()