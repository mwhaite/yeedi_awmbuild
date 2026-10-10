"""Coordinator that polls the Yeedi cloud for each bound device."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from .client import Device, YeediApiError, YeediClient
from .const import DEFAULT_POLL_SECONDS, DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.update_coordinator import (
        DataUpdateCoordinator,
        UpdateFailed,
    )

_LOGGER = logging.getLogger(__name__)


def _make_coordinator_class():
    """Return the real YeediCoordinator class.

    Imported lazily so this module is importable without a full HA runtime
    (test environments don't ship the full homeassistant package). The
    __init__.py imports this module at setup time only, when HA is loaded.
    """
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

    class YeediCoordinator(DataUpdateCoordinator):  # type: ignore[misc, valid-type]
        """Polls all bound devices; data shape is {did: state_dict}."""

        def __init__(
            self,
            hass: "HomeAssistant",
            entry: "ConfigEntry",
            client: YeediClient,
            devices: list[Device],
            poll_seconds: int = DEFAULT_POLL_SECONDS,
        ) -> None:
            super().__init__(
                hass,
                _LOGGER,
                name=f"{DOMAIN}.{entry.entry_id}",
                update_interval=timedelta(seconds=poll_seconds),
            )
            self._client = client
            self.devices: dict[str, Device] = {d.did: d for d in devices}

        async def _async_update_data(self) -> dict[str, dict]:
            from homeassistant.helpers.update_coordinator import UpdateFailed
            result: dict[str, dict] = {}
            for did, device in self.devices.items():
                try:
                    result[did] = await self._client.async_get_state(device)
                except YeediApiError as err:
                    raise UpdateFailed(f"poll failed for {did}: {err}") from err
            return result

    return YeediCoordinator


# Bind eagerly. The __init__.py and config_flow that import this class only do
# so at HA setup time, when the homeassistant package is fully available.
YeediCoordinator = _make_coordinator_class()
