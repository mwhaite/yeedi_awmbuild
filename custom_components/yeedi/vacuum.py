"""Yeedi vacuum entity platform for Home Assistant.

This module implements vacuum entities for Yeedi robot vacuums.
Each discovered Yeedi device gets a ``YeediVacuum`` entity that exposes
standard Home Assistant vacuum services and attributes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from deebot_client.capabilities import Capabilities
from deebot_client.commands.json.clean import Clean, CleanAction
from deebot_client.commands.json.charge import Charge
from deebot_client.commands.json.fan_speed import SetFanSpeed
from deebot_client.commands.json.play_sound import PlaySound
from deebot_client.device import Device
from deebot_client.events import (
    AvailabilityEvent,
    BatteryEvent,
    FanSpeedEvent,
    StateEvent,
)

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import YeediConfigEntry
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State mapping – deebot_client states → Home Assistant VacuumActivity
# ---------------------------------------------------------------------------
_STATE_TO_VACUUM: dict[str, VacuumActivity] = {
    "idle": VacuumActivity.IDLE,
    "cleaning": VacuumActivity.CLEANING,
    "paused": VacuumActivity.PAUSED,
    "returning": VacuumActivity.RETURNING,
    "docked": VacuumActivity.DOCKED,
    "error": VacuumActivity.ERROR,
}


# ---------------------------------------------------------------------------
# Entity setup
# ---------------------------------------------------------------------------
async def async_setup_entry(
    hass: HomeAssistant,
    entry: YeediConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Yeedi vacuum entities from a config entry.

    Called by Home Assistant when the integration's vacuum platform is loaded.
    Creates a ``YeediVacuum`` entity for every device discovered by the
    config-entry controller.
    """
    controller = entry.runtime_data
    async_add_entities(
        [YeediVacuum(device) for device in controller.devices],
        update_before_add=True,
    )


# ---------------------------------------------------------------------------
# YeediVacuum entity
# ---------------------------------------------------------------------------
class YeediVacuum(StateVacuumEntity):
    """Yeedi vacuum entity.

    Wraps a :class:`deebot_client.device.Device` and exposes it as a
    Home Assistant ``StateVacuumEntity``.
    """

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(self, device: Device) -> None:
        """Initialize the Yeedi vacuum entity.

        Args:
            device: The deebot_client Device instance that this entity
                represents.
        """
        self._device = device
        device_info = device.device_info

        # Unique id / name
        self._attr_unique_id = device_info["did"]
        self._attr_name = device_info.get("nick") or device_info.get("name", "Yeedi Vacuum")

        # ------------------------------------------------------------------
        # Supported features – start with the base set and extend based on
        # the device's reported capabilities.
        # ------------------------------------------------------------------
        features = (
            VacuumEntityFeature.START
            | VacuumEntityFeature.STOP
            | VacuumEntityFeature.PAUSE
            | VacuumEntityFeature.RETURN_HOME
            | VacuumEntityFeature.LOCATE
            | VacuumEntityFeature.STATE
            | VacuumEntityFeature.SEND_COMMAND
        )

        caps: Capabilities = device.capabilities

        # Fan speed
        if caps.fan_speed:
            features |= VacuumEntityFeature.FAN_SPEED
            self._attr_fan_speed_list = list(caps.fan_speed.levels.keys())

        # Area clean (rooms / rooms+map)
        if caps.clean.action.area and hasattr(VacuumEntityFeature, "CLEAN_AREA"):
            features |= VacuumEntityFeature.CLEAN_AREA

        self._attr_supported_features = features

        # ------------------------------------------------------------------
        # Device registry info
        # ------------------------------------------------------------------
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_info["did"])},
            "manufacturer": "Yeedi",
            "model": device_info.get("deviceName"),
            "serial_number": device_info.get("name"),
            "sw_version": device.fw_version,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added to Home Assistant.

        Subscribes to the deebot_client event bus so that state changes
        pushed by the robot over MQTT are reflected in the HA entity.
        """
        await super().async_added_to_hass()

        self._unsubs: list[Callable[[], None]] = []

        @callback
        def _on_state(event: StateEvent) -> None:
            """Handle StateEvent from deebot_client."""
            self._attr_activity = _STATE_TO_VACUUM.get(
                event.state, VacuumActivity.IDLE
            )
            self.async_write_ha_state()

        @callback
        def _on_battery(event: BatteryEvent) -> None:
            """Handle BatteryEvent from deebot_client."""
            self._attr_battery_level = event.value
            self.async_write_ha_state()

        @callback
        def _on_fan_speed(event: FanSpeedEvent) -> None:
            """Handle FanSpeedEvent from deebot_client."""
            self._attr_fan_speed = event.speed
            self.async_write_ha_state()

        @callback
        def _on_availability(event: AvailabilityEvent) -> None:
            """Handle AvailabilityEvent from deebot_client."""
            self._attr_available = event.available
            self.async_write_ha_state()

        # Subscribe to deebot_client events
        self._unsubs.append(
            self._device.events.subscribe(StateEvent, _on_state)
        )
        self._unsubs.append(
            self._device.events.subscribe(BatteryEvent, _on_battery)
        )
        self._unsubs.append(
            self._device.events.subscribe(FanSpeedEvent, _on_fan_speed)
        )
        self._unsubs.append(
            self._device.events.subscribe(AvailabilityEvent, _on_availability)
        )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when the entity is removed from Home Assistant."""
        for unsub in getattr(self, "_unsubs", []):
            unsub()
        await super().async_will_remove_from_hass()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def activity(self) -> VacuumActivity | None:
        """Return the current vacuum activity.

        Maps to one of the :class:`VacuumActivity` enum values based on the
        last ``StateEvent`` received from the device.
        """
        return self._attr_activity

    # ------------------------------------------------------------------
    # Service commands
    # ------------------------------------------------------------------
    async def async_start(self) -> None:
        """Start or resume cleaning.

        Sends the ``CleanAction.START`` command to the robot.
        """
        try:
            await self._device.execute_command(Clean(CleanAction.START))
        except Exception as err:
            _LOGGER.error("Failed to start %s: %s", self.entity_id, err)

    async def async_stop(self, **kwargs: Any) -> None:
        """Stop the vacuum cleaner.

        Sends the ``CleanAction.STOP`` command to the robot.
        """
        try:
            await self._device.execute_command(Clean(CleanAction.STOP))
        except Exception as err:
            _LOGGER.error("Failed to stop %s: %s", self.entity_id, err)

    async def async_pause(self) -> None:
        """Pause the cleaning task.

        Sends the ``CleanAction.PAUSE`` command to the robot.
        """
        try:
            await self._device.execute_command(Clean(CleanAction.PAUSE))
        except Exception as err:
            _LOGGER.error("Failed to pause %s: %s", self.entity_id, err)

    async def async_return_to_base(self, **kwargs: Any) -> None:
        """Set the vacuum cleaner to return to the dock.

        Sends the ``Charge`` command to the robot.
        """
        try:
            await self._device.execute_command(Charge())
        except Exception as err:
            _LOGGER.error(
                "Failed to return %s to base: %s", self.entity_id, err
            )

    async def async_locate(self, **kwargs: Any) -> None:
        """Locate the vacuum cleaner.

        Sends the ``PlaySound`` command so the robot emits a sound.
        """
        try:
            await self._device.execute_command(PlaySound())
        except Exception as err:
            _LOGGER.error("Failed to locate %s: %s", self.entity_id, err)

    async def async_send_command(
        self,
        command: str,
        params: dict[str, Any] | list[Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Send a custom command to the vacuum cleaner.

        Dispatches the command through the device's capabilities if a
        matching capability is found, otherwise logs a warning.

        Args:
            command: The name of the custom command.
            params: Optional parameters for the command.
        """
        try:
            caps: Capabilities = self._device.capabilities

            # Handle area cleaning commands
            if command in ("spot_area", "custom_area") and params:
                _LOGGER.info(
                    "Area cleaning command '%s' with params %s on %s - "
                    "use the built-in CLEAN_AREA feature instead",
                    command,
                    params,
                    self.entity_id,
                )
                return

            # Try to dispatch through custom command capability
            if caps.custom_command:
                await self._device.execute_command(
                    caps.custom_command.command(command, params or {})
                )
                return

            _LOGGER.warning(
                "Custom command '%s' with params %s is not supported by %s",
                command,
                params,
                self.entity_id,
            )
        except Exception as err:
            _LOGGER.error(
                "Failed to send command '%s' to %s: %s",
                command,
                self.entity_id,
                err,
            )

    async def async_set_fan_speed(self, fan_speed: str, **kwargs: Any) -> None:
        """Set the fan speed of the vacuum cleaner.

        Args:
            fan_speed: The desired fan speed level. Must be one of the
                values in ``fan_speed_list``.
        """
        try:
            if not self._device.capabilities.fan_speed:
                _LOGGER.warning(
                    "Fan speed control is not supported by %s", self.entity_id
                )
                return

            await self._device.execute_command(SetFanSpeed(fan_speed))
        except Exception as err:
            _LOGGER.error(
                "Failed to set fan speed on %s: %s", self.entity_id, err
            )
