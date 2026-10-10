"""Yeedi integration setup."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .client import Device, YeediApiError, YeediClient
from .const import CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS, PLATFORMS

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DATA_CLIENT = "client"
DATA_COORDINATOR = "coordinator"


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Set up Yeedi from a config entry."""
    # All HA imports and the coordinator class are loaded lazily so the
    # package itself can be imported in tests / without a full HA runtime.
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from .coordinator import YeediCoordinator

    session = async_get_clientsession(hass)
    # Persist a stable deviceId per config entry so Yeedi's 1013 device-trust
    # check doesn't re-fire on every reload/restart.
    device_id = entry.data.get("device_id")
    if not device_id:
        import hashlib
        import time
        device_id = "ha-" + hashlib.md5(
            f"{entry.entry_id}-{time.time_ns()}".encode()
        ).hexdigest()[:12]
        new_data = {**entry.data, "device_id": device_id}
        hass.config_entries.async_update_entry(entry, data=new_data)
    client = YeediClient(session, device_id=device_id)

    try:
        _, devices = await client.async_login(
            email=entry.data["email"],
            password=entry.data["password"],
            country=entry.data["country"],
        )
    except YeediApiError as err:
        _LOGGER.error("Yeedi login failed: %s", err)
        return False

    poll_seconds = entry.options.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS)
    coordinator = YeediCoordinator(
        hass=hass,
        entry=entry,
        client=client,
        devices=devices,
        poll_seconds=poll_seconds,
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(entry.domain, {})[entry.entry_id] = {
        DATA_CLIENT: client,
        DATA_COORDINATOR: coordinator,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[entry.domain].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: "HomeAssistant", entry: "ConfigEntry") -> None:
    await hass.config_entries.async_reload(entry.entry_id)