"""Config flow for Yeedi integration."""

from functools import partial
import logging
import secrets
import ssl
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientError
from deebot_client.exceptions import InvalidAuthenticationError, MqttError
from deebot_client.mqtt_client import MqttClient, create_mqtt_config
from deebot_client.util import md5
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_COUNTRY, CONF_MODE, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client, selector
from homeassistant.helpers.typing import VolDictType

from .const import (
    CONF_OVERRIDE_MQTT_URL,
    CONF_OVERRIDE_REST_URL,
    CONF_VERIFY_MQTT_CERTIFICATE,
    DOMAIN,
    InstanceMode,
)
from .yeedi_auth import YeediAuthenticator, create_yeedi_rest_config

_LOGGER = logging.getLogger(__name__)


async def _validate_input(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    """Validate the user input allows us to connect.

    Returns a dict of error messages (empty dict = success).
    """
    errors: dict[str, str] = {}

    # 1. Validate REST URL override if provided
    override_rest_url = user_input.get(CONF_OVERRIDE_REST_URL)
    if override_rest_url:
        parsed = urlparse(override_rest_url)
        if parsed.scheme not in ("http", "https"):
            errors[CONF_OVERRIDE_REST_URL] = "invalid_url"

    # 2. Validate MQTT URL override if provided
    override_mqtt_url = user_input.get(CONF_OVERRIDE_MQTT_URL)
    if override_mqtt_url:
        parsed = urlparse(override_mqtt_url)
        if parsed.scheme not in ("mqtt", "mqtts"):
            errors[CONF_OVERRIDE_MQTT_URL] = "invalid_url"

    if errors:
        return errors

    # 3. Generate device ID
    mode = InstanceMode(user_input[CONF_MODE])
    if mode == InstanceMode.SELF_HOSTED:
        device_id = f"HA-{hass.config.location_name}"
    else:
        device_id = secrets.token_hex(8)

    # 4. Create authenticator and test authentication
    session = aiohttp_client.async_get_clientsession(hass)
    country = user_input[CONF_COUNTRY]
    username = user_input[CONF_USERNAME]
    password = user_input[CONF_PASSWORD]

    rest_config = create_yeedi_rest_config(
        session,
        device_id=device_id,
        alpha_2_country=country,
        override_rest_url=override_rest_url,
    )

    authenticator = YeediAuthenticator(
        rest_config,
        username,
        md5(password),
    )

    try:
        # Test authentication
        await authenticator.authenticate()

        # Create SSL context based on verification preference
        verify_mqtt_certificate: bool = user_input.get(
            CONF_VERIFY_MQTT_CERTIFICATE, True
        )
        if verify_mqtt_certificate:
            ssl_context = ssl.create_default_context()
        else:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        # 5. Test MQTT connection
        mqtt_config = await hass.async_add_executor_job(
            partial(
                create_mqtt_config,
                device_id=device_id,
                country=country,
                override_mqtt_url=override_mqtt_url,
                ssl_context=ssl_context,
            )
        )
        mqtt_client = MqttClient(mqtt_config, authenticator)
        await mqtt_client.verify_config()

    except ClientError:
        errors["base"] = "cannot_connect"
    except InvalidAuthenticationError:
        errors["base"] = "invalid_auth"
    except MqttError:
        errors["base"] = "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected exception during validation")
        errors["base"] = "unknown"

    return errors


class YeediConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Yeedi."""

    VERSION = 1
    _mode: InstanceMode = InstanceMode.CLOUD

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step - mode selection."""
        if user_input is not None:
            self._mode = InstanceMode(user_input[CONF_MODE])
            return await self.async_step_auth()

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_MODE,
                    default=InstanceMode.CLOUD.value,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[InstanceMode.CLOUD.value, InstanceMode.SELF_HOSTED.value],
                        mode=selector.SelectSelectorMode.LIST,
                        translation_key=CONF_MODE,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
        )

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the authentication step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # 1. Abort if same username already configured
            self._async_abort_entries_match(
                {CONF_USERNAME: user_input[CONF_USERNAME]}
            )

            user_input[CONF_MODE] = self._mode.value

            # 2. Validate input
            errors = await _validate_input(self.hass, user_input)

            if not errors:
                # 3. Create entry with title = username
                data = {
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_COUNTRY: user_input[CONF_COUNTRY],
                    CONF_MODE: self._mode.value,
                    CONF_OVERRIDE_REST_URL: user_input.get(CONF_OVERRIDE_REST_URL),
                    CONF_OVERRIDE_MQTT_URL: user_input.get(CONF_OVERRIDE_MQTT_URL),
                    CONF_VERIFY_MQTT_CERTIFICATE: user_input.get(
                        CONF_VERIFY_MQTT_CERTIFICATE, True
                    ),
                }
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data=data,
                )

        # Build the form schema
        vol_schema: VolDictType = {
            vol.Required(CONF_USERNAME): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.TEXT,
                )
            ),
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(
                    type=selector.TextSelectorType.PASSWORD,
                )
            ),
            vol.Required(
                CONF_COUNTRY,
                default=self.hass.config.country,
            ): selector.CountrySelector(),
        }

        if self._mode == InstanceMode.SELF_HOSTED:
            vol_schema[vol.Optional(CONF_OVERRIDE_REST_URL)] = (
                selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                    )
                )
            )
            vol_schema[vol.Optional(CONF_OVERRIDE_MQTT_URL)] = (
                selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                    )
                )
            )
            vol_schema[
                vol.Optional(CONF_VERIFY_MQTT_CERTIFICATE, default=True)
            ] = selector.BooleanSelector()

        data_schema = vol.Schema(vol_schema)

        return self.async_show_form(
            step_id="auth",
            data_schema=data_schema,
            errors=errors,
        )
