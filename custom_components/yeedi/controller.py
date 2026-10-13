"""Yeedi controller for managing robot vacuum devices."""

from __future__ import annotations

import logging
import random
import ssl
import string
from collections.abc import Mapping
from functools import partial
from typing import Any

from deebot_client.api_client import ApiClient
from deebot_client.device import Device
from deebot_client.exceptions import DeebotError, InvalidAuthenticationError
from deebot_client.mqtt_client import MqttClient, create_mqtt_config
from deebot_client.util import md5

from homeassistant.const import CONF_COUNTRY, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client

from homeassistant.const import CONF_MODE

from .const import (
    CONF_OVERRIDE_MQTT_URL,
    CONF_OVERRIDE_REST_URL,
    CONF_VERIFY_MQTT_CERTIFICATE,
)
from .yeedi_auth import YeediAuthenticator, create_yeedi_rest_config

_LOGGER = logging.getLogger(__name__)


class YeediController:
    """Yeedi controller managing all devices for a config entry."""

    def __init__(self, hass: HomeAssistant, config: Mapping[str, Any]) -> None:
        """Initialize the Yeedi controller.

        Args:
            hass: The Home Assistant instance.
            config: Configuration mapping containing:
                - CONF_USERNAME: Account email/username.
                - CONF_PASSWORD: Plain-text password.
                - CONF_COUNTRY: 2-letter country code (e.g., "DE", "US").
                - "mode": "cloud" or "self_hosted".
                - "override_rest_url": Optional override for REST API URL.
                - "override_mqtt_url": Optional override for MQTT broker URL.
                - "verify_mqtt_certificate": Whether to verify MQTT TLS certs (default True).

        """
        self._hass = hass
        self._devices: list[Device] = []

        # Extract configuration values
        username: str = config[CONF_USERNAME]
        password: str = config[CONF_PASSWORD]
        country: str = config[CONF_COUNTRY]
        mode: str = config.get(CONF_MODE, "cloud")
        override_rest_url: str | None = config.get(CONF_OVERRIDE_REST_URL)
        override_mqtt_url: str | None = config.get(CONF_OVERRIDE_MQTT_URL)
        client_secret: str = ""  # kept for backward compat; ignored — secrets are baked into yeedi_auth
        auth_client_secret: str = ""

        # Generate device ID based on mode
        if mode == "self_hosted":
            device_id = f"HA-{hass.config.location_name}"
        else:
            device_id = "".join(
                random.choices(string.ascii_uppercase + string.digits, k=8)
            )

        # Create SSL context based on verification preference
        verify_mqtt_certificate: bool = config.get(
            CONF_VERIFY_MQTT_CERTIFICATE, True
        )
        if verify_mqtt_certificate:
            ssl_context = ssl.create_default_context()
        else:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        # Create aiohttp session
        session = aiohttp_client.async_get_clientsession(hass)

        # Create REST configuration for the authenticator
        rest_config = create_yeedi_rest_config(
            session,
            device_id=device_id,
            alpha_2_country=country,
            override_rest_url=override_rest_url,
            client_secret=client_secret,
            auth_client_secret=auth_client_secret,
        )

        # Create authenticator with MD5-hashed password
        self._authenticator = YeediAuthenticator(
            rest_config,
            account_id=username,
            password_hash=md5(password),
        )

        # Create API client for device discovery
        self._api_client = ApiClient(self._authenticator)

        # Store a partial for lazy MQTT config creation.
        self._mqtt_config_partial = partial(
            create_mqtt_config,
            device_id=device_id,
            country=country,
            override_mqtt_url=override_mqtt_url,
            ssl_context=ssl_context,
        )

        self._mqtt_client: MqttClient | None = None

    async def initialize(self) -> None:
        """Discover and initialize all Yeedi devices.

        Raises:
            ConfigEntryError: If credentials are invalid.
            ConfigEntryNotReady: If a device error occurs during setup.

        """
        try:
            discovered = await self._api_client.get_devices()
        except InvalidAuthenticationError as exc:
            raise ConfigEntryError("Invalid credentials") from exc
        except DeebotError as exc:
            raise ConfigEntryNotReady("Error during setup") from exc

        # Log warnings for unsupported devices
        for unsupported in discovered.not_supported:
            _LOGGER.warning(
                "Device '%s' (%s) is not supported",
                unsupported.name,
                unsupported.did,
            )

        # Initialize MQTT devices
        mqtt_client = await self._get_mqtt_client()
        for device_info in discovered.mqtt:
            device = Device(device_info, self._authenticator)
            await device.initialize(mqtt_client)
            self._devices.append(device)

            _LOGGER.debug(
                "Initialized device '%s' (%s)",
                device_info.name,
                device_info.did,
            )

    async def _get_mqtt_client(self) -> MqttClient:
        """Get or lazily create the MQTT client.

        Returns:
            The shared MqttClient instance.

        """
        if self._mqtt_client is None:
            mqtt_config = await self._hass.async_add_executor_job(
                self._mqtt_config_partial
            )
            self._mqtt_client = MqttClient(mqtt_config, self._authenticator)
            await self._mqtt_client.verify_config()
        return self._mqtt_client

    async def teardown(self) -> None:
        """Teardown all devices and disconnect from services."""
        # Teardown all devices
        for device in self._devices:
            await device.teardown()
        self._devices.clear()

        # Disconnect MQTT client
        if self._mqtt_client is not None:
            await self._mqtt_client.disconnect()
            self._mqtt_client = None

        # Teardown authenticator
        await self._authenticator.teardown()

    @property
    def devices(self) -> list[Device]:
        """Return the list of discovered devices."""
        return self._devices
