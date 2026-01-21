"""Diagnostics support for Yeedi."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_CLIENT, DATA_COORDINATOR, DOMAIN

REDACT_KEYS = {
    "password",
    "access_token",
    "refresh_token",
    "token",
    "authorization",
    "user_id",
    "device_id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = data.get(DATA_COORDINATOR)
    client = data.get(DATA_CLIENT)

    diagnostics = {
        "entry": async_redact_data(entry.as_dict(), REDACT_KEYS),
        "client": {
            "base_url": getattr(client, "_base_url", None),
            "region": getattr(client, "_region", None),
            "has_token": bool(getattr(client, "access_token", None)),
        },
        "coordinator": {
            "last_update_success": getattr(coordinator, "last_update_success", None),
            "device_count": len(getattr(coordinator, "devices", {}) or {}),
        },
    }

    return async_redact_data(diagnostics, REDACT_KEYS)
