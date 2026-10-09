"""Sensor platform for Yeedi integration.

17 sensors, one per ha-sensor node in the Node-RED flow.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEVICE_MANUFACTURER,
    DEVICE_MODEL,
    DEVICE_NAME,
    DEVICE_SW_VERSION,
    DOMAIN,
    SENSORS,
)
from .coordinator import YeediCoordinator
from .entity import extract


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the 17 sensor entities."""
    coordinator: YeediCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    entities: list[SensorEntity] = []
    for device_did in coordinator.devices:
        for spec in SENSORS:
            entities.append(
                YeediSensor(coordinator, device_did, spec)
            )
    async_add_entities(entities)


class YeediSensor(CoordinatorEntity[YeediCoordinator], SensorEntity):
    """One sensor per (device, event, path) triple from the flow."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: YeediCoordinator,
        device_did: str,
        spec: dict,
    ) -> None:
        super().__init__(coordinator)
        self._device_did = device_did
        self._event = spec["event"]
        self._path = spec["path"]
        self._attr_name = spec["name"]
        self._attr_unique_id = f"{device_did}__{spec['name']}"
        if "device_class" in spec:
            self._attr_device_class = SensorDeviceClass(spec["device_class"])

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_did)},
            "name": DEVICE_NAME,
            "manufacturer": DEVICE_MANUFACTURER,
            "model": DEVICE_MODEL,
            "sw_version": DEVICE_SW_VERSION,
        }

    @property
    def native_value(self):
        state = (self.coordinator.data or {}).get(self._device_did, {})
        return extract(state, self._event, self._path)