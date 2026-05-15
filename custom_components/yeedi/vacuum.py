"""Vacuum entity for Yeedi devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.vacuum import (
    STATE_CLEANING,
    STATE_DOCKED,
    STATE_ERROR,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_RETURNING,
    VacuumEntity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import YeediApiClient
from .const import DATA_CLIENT, DATA_COORDINATOR, DOMAIN
from .coordinator import YeediDataUpdateCoordinator

STATUS_MAP = {
    "cleaning": STATE_CLEANING,
    "returning": STATE_RETURNING,
    "docked": STATE_DOCKED,
    "charging": STATE_DOCKED,
    "paused": STATE_PAUSED,
    "idle": STATE_IDLE,
    "error": STATE_ERROR,
}


class YeediVacuumEntity(CoordinatorEntity[YeediDataUpdateCoordinator], VacuumEntity):
    """Representation of a Yeedi vacuum."""

    def __init__(
        self, coordinator: YeediDataUpdateCoordinator, client: YeediApiClient, device_id: str
    ) -> None:
        super().__init__(coordinator)
        self._client = client
        self._device_id = device_id
        self._attr_unique_id = device_id

    @property
    def device_info(self) -> dict[str, Any] | None:
        device = self.coordinator.devices.get(self._device_id)
        if not device:
            return None
        return {
            "identifiers": {("yeedi", self._device_id)},
            "name": device.name,
            "model": device.model,
            "manufacturer": "Yeedi",
        }

    @property
    def name(self) -> str | None:
        device = self.coordinator.devices.get(self._device_id)
        return device.name if device else None

    @property
    def supported_features(self) -> VacuumEntityFeature:
        features = (
            VacuumEntityFeature.START
            | VacuumEntityFeature.STOP
            | VacuumEntityFeature.PAUSE
            | VacuumEntityFeature.RETURN_HOME
        )
        if self._supports_locate():
            features |= VacuumEntityFeature.LOCATE
        if self.fan_speed_list:
            features |= VacuumEntityFeature.FAN_SPEED
        return features

    @property
    def battery_level(self) -> int | None:
        status = self._status
        if not status:
            return None
        battery = status.get("battery")
        if battery is None:
            battery = status.get("battery_level")
        try:
            return int(battery)
        except (TypeError, ValueError):
            return None

    @property
    def state(self) -> str | None:
        status = self._status
        if not status:
            return None
        raw = str(status.get("status") or status.get("state") or "").lower()
        return STATUS_MAP.get(raw)

    @property
    def fan_speed(self) -> str | None:
        status = self._status
        if not status:
            return None
        return status.get("fan_speed") or status.get("fanSpeed")

    @property
    def fan_speed_list(self) -> list[str]:
        device = self.coordinator.devices.get(self._device_id)
        if not device:
            return []
        raw_list = device.raw.get("fan_speeds") or device.raw.get("fan_speed_list") or []
        return [str(item) for item in raw_list if item]

    async def async_start(self) -> None:
        await self._client.async_send_command(self._device_id, "start")

    async def async_pause(self) -> None:
        await self._client.async_send_command(self._device_id, "pause")

    async def async_stop(self) -> None:
        await self._client.async_send_command(self._device_id, "stop")

    async def async_return_to_base(self) -> None:
        await self._client.async_send_command(self._device_id, "return_to_base")

    async def async_locate(self) -> None:
        await self._client.async_send_command(self._device_id, "locate")

    async def async_set_fan_speed(self, fan_speed: str) -> None:
        await self._client.async_send_command(
            self._device_id, "set_fan_speed", {"fan_speed": fan_speed}
        )

    @property
    def _status(self) -> dict[str, Any] | None:
        return self.coordinator.data.get(self._device_id) if self.coordinator.data else None

    def _supports_locate(self) -> bool:
        device = self.coordinator.devices.get(self._device_id)
        if not device:
            return False
        return "locate" in {cap.lower() for cap in device.capabilities}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up Yeedi vacuum entities from a config entry."""

    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: YeediDataUpdateCoordinator = data[DATA_COORDINATOR]
    client: YeediApiClient = data[DATA_CLIENT]

    entities = [
        YeediVacuumEntity(coordinator, client, device_id) for device_id in coordinator.devices
    ]
    async_add_entities(entities)
