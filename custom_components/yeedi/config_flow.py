"""Config flow for Yeedi integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuthResult, YeediApiClient, YeediApiError
from .const import CONF_BASE_URL, CONF_POLL_INTERVAL, CONF_REGION, DEFAULT_POLL_INTERVAL, DOMAIN


async def _async_validate_input(hass: HomeAssistant, data: dict[str, str]) -> AuthResult:
    """Validate the user input allows us to connect."""

    session = async_get_clientsession(hass)
    client = YeediApiClient(
        session=session,
        base_url=data.get(CONF_BASE_URL),
        region=data.get(CONF_REGION),
    )
    return await client.async_login(data[CONF_USERNAME], data[CONF_PASSWORD], data.get(CONF_REGION))


class YeediConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Yeedi."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, str] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                auth = await _async_validate_input(self.hass, user_input)
            except YeediApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - HA flow needs broad handling
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_REGION: user_input.get(CONF_REGION),
                        CONF_BASE_URL: user_input.get(CONF_BASE_URL),
                        "access_token": auth.access_token,
                        "refresh_token": auth.refresh_token,
                        "expires_at": auth.expires_at.isoformat() if auth.expires_at else None,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_REGION): str,
                vol.Optional(CONF_BASE_URL): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, _entry_data: dict[str, str]):
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, str] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                auth = await _async_validate_input(self.hass, user_input)
            except YeediApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                entry = self._reauth_entry
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        "access_token": auth.access_token,
                        "refresh_token": auth.refresh_token,
                        "expires_at": auth.expires_at.isoformat() if auth.expires_at else None,
                    },
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_REGION): str,
                vol.Optional(CONF_BASE_URL): str,
            }
        )
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return YeediOptionsFlowHandler(config_entry)


class YeediOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Yeedi options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, str] | None = None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_REGION,
                    default=self._entry.options.get(CONF_REGION, self._entry.data.get(CONF_REGION, "")),
                ): str,
                vol.Optional(
                    CONF_BASE_URL,
                    default=self._entry.options.get(CONF_BASE_URL, self._entry.data.get(CONF_BASE_URL, "")),
                ): str,
                vol.Optional(
                    CONF_POLL_INTERVAL,
                    default=self._entry.options.get(
                        CONF_POLL_INTERVAL, int(DEFAULT_POLL_INTERVAL.total_seconds())
                    ),
                ): vol.Coerce(int),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
