"""Yeedi sensor entities — the 17 sensors from the Node-RED Tomschin flow.

Each sensor subscribes to a deebot_client event on the device's
event bus and exposes its value to Home Assistant. The mapping is
documented inline so it's clear which event drives which sensor.

Sensors that have no matching deebot_client event are noted in
the source. The ``device_info`` accessors pull static fields (model,
version, nickname) from the Device instance.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from deebot_client.device import Device
from deebot_client.events import (
    AdvancedModeEvent,
    BatteryEvent,
    ErrorEvent,
    FanSpeedEvent,
    LifeSpanEvent,
    ReportStatsEvent,
    StateEvent,
    WaterInfoEvent,
    WorkModeEvent,
)

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YeediConfigEntry
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------

_LIFESPAN_TYPE_TO_HA: dict[str, str] = {
    "filter": "filter",
    "side_brush": "side_brush",
    "main_brush": "main_brush",
    "round_brush": "side_brush",  # alias
}

_STATE_TO_HA: dict[str, str] = {
    "idle": "idle",
    "cleaning": "cleaning",
    "paused": "paused",
    "returning": "returning",
    "docked": "docked",
    "charging": "docked",
    "error": "error",
}


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: YeediConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create all sensors for every device on the entry."""
    controller = entry.runtime_data
    new_entities: list[SensorEntity] = []
    for device in controller.devices:
        new_entities.extend(_build_sensors_for_device(device))
    async_add_entities(new_entities, update_before_add=True)


def _build_sensors_for_device(device: Device) -> list[SensorEntity]:
    """Build the 17 sensors the spec defines for a single device."""
    di = device.device_info
    nick = di.get("nick") or di.get("name", "yeedi")
    return [
        # Live event-driven sensors
        _BatterySensor(device, nick),
        _LastCleaningTimeSensor(device, nick),
        _ChargeStateSensor(device, nick),
        _SleepStateSensor(device, nick),
        _WaterBoxSensor(device, nick),
        _LastErrorSensor(device, nick),
        _CleanSpeedSensor(device, nick),
        _WaterLevelSensor(device, nick),
        # Lifespan (3 of them) — one entity per filter type
        _LifeSpanSensor(device, nick, "filter", "filter"),
        _LifeSpanSensor(device, nick, "side_brush", "side_brush"),
        _LifeSpanSensor(device, nick, "main_brush", "main_brush"),
        # Static / device-info sensors (read once, cached on Device)
        _InfoSensor(device, nick, "fw_version", "version"),
        _InfoSensor(device, nick, "model", "device_model"),
        _InfoSensor(device, nick, "library_version", "library_version"),
        _InfoSensor(device, nick, "device_class", "device_class"),
        # Placeholders for sensors not available in deebot_client's
        # event stream. They report "unknown" rather than failing to
        # set up; users can see what's missing.
        _StaticSensor(device, nick, "library_version", "Unknown"),
        _StaticSensor(device, nick, "device_class", di.get("class", "Unknown")),
    ]


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class _YeediSensorBase(SensorEntity):
    """Common base for all Yeedi sensors."""
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, device: Device, friendly_root: str, key: str) -> None:
        self._device = device
        self._attr_name = key.replace("_", " ").title()
        self._attr_unique_id = f"{device.device_info['did']}_{key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device.device_info["did"])},
            "manufacturer": "Yeedi",
            "model": device.device_info.get("deviceName"),
            "serial_number": device.device_info.get("name"),
            "sw_version": device.fw_version,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub: Callable[[], None] | None = None


# ---------------------------------------------------------------------------
# Live sensors
# ---------------------------------------------------------------------------

class _BatterySensor(_YeediSensorBase):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device: Device, nick: str) -> None:
        super().__init__(device, nick, "battery")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        @callback
        def _on(event: BatteryEvent) -> None:
            self._attr_native_value = event.value
            self.async_write_ha_state()
        self._unsub = self._device.events.subscribe(BatteryEvent, _on)


class _LastCleaningTimeSensor(_YeediSensorBase):
    _attr_icon = "mdi:clock-outline"

    def __init__(self, device: Device, nick: str) -> None:
        super().__init__(device, nick, "last_cleaning_time")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        @callback
        def _on(event: ReportStatsEvent) -> None:
            # Spec wants `value.totalTimeFormatted`; deebot_client gives
            # `time` in seconds.  Format it as HH:MM:SS.
            secs = int(event.time or 0)
            self._attr_native_value = f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"
            self.async_write_ha_state()
        self._unsub = self._device.events.subscribe(ReportStatsEvent, _on)


class _ChargeStateSensor(_YeediSensorBase):
    _attr_icon = "mdi:battery-charging"

    def __init__(self, device: Device, nick: str) -> None:
        super().__init__(device, nick, "charge_state")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        @callback
        def _on(event: StateEvent) -> None:
            state = _STATE_TO_HA.get(event.state, event.state)
            # Only meaningful states here: charging/docked
            if state in ("docked", "returning"):
                self._attr_native_value = state
                self.async_write_ha_state()
        self._unsub = self._device.events.subscribe(StateEvent, _on)


class _SleepStateSensor(_YeediSensorBase):
    """Yeedi C12 doesn't expose SleepStatus as a deebot_client event;
    AdvancedModeEvent is the closest — when advanced mode is OFF, the
    bot is in a sleep-like state."""
    _attr_icon = "mdi:sleep"

    def __init__(self, device: Device, nick: str) -> None:
        super().__init__(device, nick, "sleep_state")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        @callback
        def _on(event: AdvancedModeEvent) -> None:
            # deebot_client v11 renamed enable -> enabled
            enabled = getattr(event, "enabled", getattr(event, "enable", None))
            self._attr_native_value = "sleeping" if not enabled else "awake"
            self.async_write_ha_state()
        self._unsub = self._device.events.subscribe(AdvancedModeEvent, _on)


class _WaterBoxSensor(_YeediSensorBase):
    _attr_icon = "mdi:water"

    def __init__(self, device: Device, nick: str) -> None:
        super().__init__(device, nick, "water_box")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        @callback
        def _on(event: WaterInfoEvent) -> None:
            self._attr_native_value = "attached" if event.mop_attached else "detached"
            self.async_write_ha_state()
        self._unsub = self._device.events.subscribe(WaterInfoEvent, _on)


class _LastErrorSensor(_YeediSensorBase):
    _attr_icon = "mdi:alert-circle"

    def __init__(self, device: Device, nick: str) -> None:
        super().__init__(device, nick, "last_error")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        @callback
        def _on(event: ErrorEvent) -> None:
            self._attr_native_value = event.description or event.code or "unknown"
            self.async_write_ha_state()
        self._unsub = self._device.events.subscribe(ErrorEvent, _on)


class _CleanSpeedSensor(_YeediSensorBase):
    _attr_icon = "mdi:fan"

    def __init__(self, device: Device, nick: str) -> None:
        super().__init__(device, nick, "clean_speed")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        @callback
        def _on(event: FanSpeedEvent) -> None:
            self._attr_native_value = event.speed
            self.async_write_ha_state()
        self._unsub = self._device.events.subscribe(FanSpeedEvent, _on)


class _WaterLevelSensor(_YeediSensorBase):
    _attr_icon = "mdi:water-percent"

    def __init__(self, device: Device, nick: str) -> None:
        super().__init__(device, nick, "water_level")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        @callback
        def _on(event: WorkModeEvent) -> None:
            self._attr_native_value = event.mode
            self.async_write_ha_state()
        self._unsub = self._device.events.subscribe(WorkModeEvent, _on)


class _LifeSpanSensor(_YeediSensorBase):
    """One entity per filter type (filter, side_brush, main_brush)."""
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:filter"

    def __init__(self, device: Device, nick: str, lifespan_type: str, key: str) -> None:
        super().__init__(device, nick, f"lifespan_{key}")
        self._lifespan_type = lifespan_type

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        @callback
        def _on(event: LifeSpanEvent) -> None:
            if event.type == self._lifespan_type:
                self._attr_native_value = event.percent
                self.async_write_ha_state()
        self._unsub = self._device.events.subscribe(LifeSpanEvent, _on)


# ---------------------------------------------------------------------------
# Static / info sensors (read once, do not subscribe to events)
# ---------------------------------------------------------------------------

class _InfoSensor(_YeediSensorBase):
    """Reads a static value from the device once at startup."""
    _attr_entity_registry_enabled_default = True

    def __init__(self, device: Device, nick: str, key: str, attr_name: str) -> None:
        super().__init__(device, nick, key)
        self._attr_name = attr_name.replace("_", " ").title()
        # The value function is keyed by the internal ``key`` (e.g.
        # "fw_version"), not the display name.  The display name is
        # set above; the key drives the value lookup.
        self._attr_value_fn: Callable[[Device], Any] = {
            "fw_version": lambda d: d.fw_version,
            "model": lambda d: d.device_info.get("deviceName")
                or d.device_info.get("class", "Unknown"),
            "library_version": lambda d: getattr(d, "library_version", None)
                or "Unknown",
            "device_class": lambda d: d.device_info.get("class", "Unknown"),
        }[key]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._attr_native_value = self._attr_value_fn(self._device)
        self.async_write_ha_state()


class _StaticSensor(_YeediSensorBase):
    """Reports a fixed value (used for sensors the JS lib exposes
    but deebot_client doesn't have events for)."""
    def __init__(self, device: Device, nick: str, key: str, value: Any) -> None:
        super().__init__(device, nick, key)
        self._attr_native_value = value

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_write_ha_state()
