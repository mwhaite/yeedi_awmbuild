"""Data update coordinator for Yeedi."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import asyncio
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import YeediApiClient, YeediApiError, YeediDevice
from .const import DEFAULT_POLL_INTERVAL, LOGGER


class YeediDataUpdateCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinator for polling Yeedi device status."""

    def __init__(self, hass: HomeAssistant, client: YeediApiClient, update_interval: timedelta | None = None) -> None:
        super().__init__(
            hass,
            LOGGER,
            name="yeedi",
            update_interval=update_interval or DEFAULT_POLL_INTERVAL,
        )
        self._client = client
        self.devices: dict[str, YeediDevice] = {}

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            if self._client.expires_at and datetime.now(UTC) >= self._client.expires_at:
                await self._client.async_refresh_token()
            devices = await self._client.async_list_devices()
            statuses: dict[str, dict[str, Any]] = {}
            self.devices = {device.device_id: device for device in devices}
            # Always fetch fresh status for all devices to avoid stale data
            all_ids = [device.device_id for device in devices]
            results = await asyncio.gather(
                *[self._client.async_get_device_status(did) for did in all_ids],
                return_exceptions=True,
            )
            device_map = {device.device_id: device for device in devices}
            for device_id, result in zip(all_ids, results):
                if isinstance(result, Exception):
                    # Fall back to potentially stale status from list response
                    device = device_map[device_id]
                    if device.status is not None:
                        statuses[device_id] = device.status
                    else:
                        raise UpdateFailed(
                            f"Failed to fetch status for {device_id}: {result}"
                        ) from result
                else:
                    statuses[device_id] = result
            return statuses
        except YeediApiError as err:
            raise UpdateFailed(str(err)) from err
