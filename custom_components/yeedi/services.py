"""Services for the Yeedi integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN


def async_setup_services(hass: HomeAssistant) -> None:
    """Set up Yeedi services."""

    async def handle_custom_command(call: ServiceCall) -> None:
        """Handle custom command service call."""
        command = call.data.get("command", "")
        params = call.data.get("params", {})

        for controller in hass.data.get(DOMAIN, {}).values():
            for device in controller.devices:
                await device.execute_command(
                    device.capabilities.custom_command.command(command, params)
                )

    hass.services.async_register(
        DOMAIN,
        "custom_command",
        handle_custom_command,
        schema=vol.Schema(
            {
                vol.Required("command"): cv.string,
                vol.Optional("params", default={}): cv.match_all,
            }
        ),
    )
