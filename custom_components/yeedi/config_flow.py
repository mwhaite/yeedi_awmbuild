"""Config flow for the Yeedi integration.

Two-step flow:
  1. async_step_user  — email + password + country
     - happy path: 3-step login + device list
     - 1013 DeviceVerificationRequired: stash client, jump to step 2
  2. async_step_verify  — emailed 6-digit code
     - calls request_device_verification_code (sends email)
     - then verify_device(code) -> uid + accessToken
     - then complete_login -> portal token
     - then create the config entry
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import (
    DeviceVerificationRequired,
    YeediApiError,
    YeediClient,
)
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

_FLOW_DATA_KEY = f"{DOMAIN}_flow_data"


def _make_client(hass: HomeAssistant) -> YeediClient:
    """Create a YeediClient with a stable per-flow deviceId.

    The deviceId must be stable across the config flow's multiple steps
    (so step 2 uses the same deviceId that step 1 used to hit 1013), and
    it must persist into the config entry (so __init__.py reuses it).
    """
    session = async_get_clientsession(hass)
    flow_data = hass.data.setdefault(_FLOW_DATA_KEY, {})
    flow_id = flow_data.get("device_id")
    if flow_id is None:
        flow_id = "ha-" + hashlib.md5(
            f"{time.time_ns()}-{id(hass)}".encode()
        ).hexdigest()[:12]
        flow_data["device_id"] = flow_id
    return YeediClient(session, device_id=flow_id)


async def _validate_input(hass: HomeAssistant, client: YeediClient, data: dict) -> dict:
    """Run the 3-step login. Returns auth info; raises on failure."""
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

    def __init__(self) -> None:
        self._credentials: dict[str, Any] | None = None
        self._client: YeediClient | None = None

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Handle the initial step (email/password/country)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            client = _make_client(self.hass)
            try:
                info = await _validate_input(self.hass, client, user_input)
            except DeviceVerificationRequired:
                # Save credentials + client; jump to email verification step.
                self._credentials = dict(user_input)
                self._client = client
                # Fire off the verification email immediately so the user
                # receives it while they wait on the next form.
                try:
                    await client.request_device_verification_code(
                        email=user_input[CONF_EMAIL],
                        country=user_input[CONF_COUNTRY],
                    )
                except YeediApiError as err:
                    _LOGGER.warning("sendEmailVerifyCode failed: %s", err)
                    errors["base"] = "cannot_connect"
                    return self.async_show_form(
                        step_id="user", data_schema=self._user_schema(), errors=errors
                    )
                return await self.async_step_verify()
            except YeediApiError as err:
                _LOGGER.warning("Yeedi login failed: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Yeedi setup")
                errors["base"] = "unknown"
            else:
                return self._create_entry(info, user_input)

        return self.async_show_form(
            step_id="user", data_schema=self._user_schema(), errors=errors
        )

    async def async_step_verify(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Handle the 1013 email verification step.

        Shows a form for the user to enter the emailed code, then calls
        verify_device(code) to mint a uid+accessToken, then complete_login.
        """
        assert self._credentials is not None and self._client is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            code = (user_input.get("verification_code") or "").strip()
            if not code:
                errors["base"] = "verification_code_required"
            else:
                try:
                    verify_resp = await self._client.verify_device(
                        email=self._credentials[CONF_EMAIL],
                        code=code,
                        country=self._credentials[CONF_COUNTRY],
                    )
                    org = "ECOYDCN" if self._credentials[CONF_COUNTRY].upper() == "CN" else "ECOYDWW"
                    auth = await self._client.complete_login(
                        uid=verify_resp["uid"],
                        login_token=verify_resp["accessToken"],
                        org=org,
                        country=self._credentials[CONF_COUNTRY],
                    )
                    devices = await self._client._async_get_devices()  # noqa: SLF001
                except YeediApiError as err:
                    _LOGGER.warning("Yeedi verify_device failed: %s", err)
                    msg = str(err).lower()
                    if "1012" in msg or "incorrect" in msg:
                        errors["base"] = "invalid_verification_code"
                    else:
                        errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Unexpected error during Yeedi verify")
                    errors["base"] = "unknown"
                else:
                    info = {
                        "title": self._credentials[CONF_EMAIL],
                        "unique_id": auth.user_id,
                        "devices": [d.did for d in devices],
                    }
                    return self._create_entry(info, self._credentials)

        return self.async_show_form(
            step_id="verify",
            data_schema=vol.Schema({vol.Required("verification_code"): str}),
            errors=errors,
            description_placeholders={
                "email": self._credentials[CONF_EMAIL],
            },
        )

    def _user_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_COUNTRY): vol.In(COUNTRIES),
                vol.Optional(
                    CONF_LOGIN_HOST, default=DEFAULT_LOGIN_HOST
                ): str,
            }
        )

    def _create_entry(self, info: dict, user_input: dict) -> FlowResult:
        device_id = self._client.device_id if self._client else None
        return self.async_create_entry(
            title=info["title"],
            data={
                CONF_EMAIL: user_input[CONF_EMAIL],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_COUNTRY: user_input[CONF_COUNTRY],
                CONF_LOGIN_HOST: user_input.get(
                    CONF_LOGIN_HOST, DEFAULT_LOGIN_HOST
                ),
                "device_id": device_id,
            },
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
