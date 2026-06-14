"""Yeedi integration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from .api import AuthResult, YeediApiClient
from .const import (
    CONF_BASE_URL,
    CONF_POLL_INTERVAL,
    CONF_REGION,
    DATA_CLIENT,
    DATA_COORDINATOR,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)


def _parse_expires_at(value: str | float | int | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


async def async_setup_entry(hass, entry) -> bool:
    """Set up Yeedi from a config entry."""

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from .coordinator import YeediDataUpdateCoordinator

    session = async_get_clientsession(hass)
    base_url = entry.options.get(CONF_BASE_URL) or entry.data.get(CONF_BASE_URL)
    if base_url == "":
        base_url = None
    region = entry.options.get(CONF_REGION) or entry.data.get(CONF_REGION)
    client = YeediApiClient(session=session, base_url=base_url, region=region)

    access_token = entry.data.get("access_token")
    refresh_token = entry.data.get("refresh_token")
    expires_at = _parse_expires_at(entry.data.get("expires_at"))
    if access_token:
        client.set_auth(AuthResult(access_token, refresh_token, expires_at))

    poll_seconds = entry.options.get(CONF_POLL_INTERVAL)
    update_interval = DEFAULT_POLL_INTERVAL
    if poll_seconds is not None:
        update_interval = timedelta(seconds=float(poll_seconds))
    coordinator = YeediDataUpdateCoordinator(hass, client, update_interval=update_interval)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_CLIENT: client,
        DATA_COORDINATOR: coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass, entry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_migrate_entry(hass, entry) -> bool:
    """Migrate config entry to a new version."""
    _LOGGER.debug("Migrating %s from version %s", entry.title, entry.version)

    if entry.version == 1:
        # No migration steps yet — version 1 is the initial schema
        pass

    _LOGGER.debug("Migration to version %s successful", entry.version)
    return True
