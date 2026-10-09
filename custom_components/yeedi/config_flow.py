"""Config flow for the Yeedi integration."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import YeediApiError, YeediClient
from .const import (
    CONF_COUNTRY,
    CONF_EMAIL,
    CONF_LOGIN_HOST,
    CONF_PASSWORD,
    CONF_POLL_SECONDS,
    COUNTRIES,
    DEFAULT_LOGIN_HOST,
    DEFAULT_POLL_SECONDS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def _validate_input(hass: HomeAssistant, data: dict) -> dict:
    """Run the 3-step login. Returns auth info; raises on failure."""
    session = async_get_clientsession(hass)
    client = YeediClient(session)
    auth, devices = await client.async_login(
        email=data[CONF_EMAIL],
        password=data[CONF_PASSWORD],
        country=data[CONF_COUNTRY],
    )
    return {
        "title": data[CONF_EMAIL],
        "unique_id": auth.user_id,
        "devices": [d.did for d in devices],
    }


class YeediConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Yeedi config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await _validate_input(self.hass, user_input)
            except YeediApiError as err:
                _LOGGER.warning("Yeedi login failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Yeedi setup")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=info["title"],
                    data={
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_COUNTRY: user_input[CONF_COUNTRY],
                        CONF_LOGIN_HOST: user_input.get(
                            CONF_LOGIN_HOST, DEFAULT_LOGIN_HOST
                        ),
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_COUNTRY): vol.In(COUNTRIES),
                vol.Optional(
                    CONF_LOGIN_HOST, default=DEFAULT_LOGIN_HOST
                ): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return YeediOptionsFlowHandler(config_entry)


class YeediOptionsFlowHandler(config_entries.OptionsFlow):
    """Options: poll interval."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_POLL_SECONDS,
                        default=self._entry.options.get(
                            CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
                }
            ),
        )