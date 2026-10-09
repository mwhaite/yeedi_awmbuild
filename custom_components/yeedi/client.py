"""HTTP polling client for Yeedi cloud portal API.

The Node-RED flow uses `node-red-contrib-ecovacs-deebot` which wraps the
Yeedi cloud REST API. We hit the same endpoints:

  - POST <portal>/users/user.do        loginByItToken -> user_access_token
  - POST <portal>/iot/devmanager.do    commands (Clean, Charge, etc.) and getDeviceList
                                       plus state queries (GetBattery, GetChargeState, ...)

This integration does NOT push events over MQTT. It polls. The 24 event
types the flow exposes are derived by issuing Get* commands and parsing
the response payloads.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)

# ----- URL templates (from library/constants.js of ecovacs-deebot.js) -----
#
# Auth (3-step):
#   Step 1: POST gl-{country}-api.yeedi.com/v1/private/{country}/EN/{deviceId}/
#           {appCode}/{appVersion}/{channel}/{deviceType}/user/login
#   Step 2: POST gl-{country}-openapi.yeedi.com/v1/global/auth/getAuthCode
#   Step 3: POST <portal>/users/user.do           -> user_access_token (long-lived)
#
# Steady-state (commands + polling):
#   POST <portal>/iot/devmanager.do               -> commands + Get* state

AUTH_GL_API = "https://gl-{country}-api.yeedi.com"
AUTH_GL_OPENAPI = "https://gl-{country}-openapi.yeedi.com"

# Continent lookup for portal host. AT=EU (the flow's country code).
COUNTRY_TO_CONTINENT = {
    "AT": "eu", "DE": "eu", "FR": "eu", "GB": "eu", "IT": "eu", "ES": "eu",
    "NL": "eu", "BE": "eu", "PL": "eu", "SE": "eu", "FI": "eu", "DK": "eu",
    "NO": "eu", "CH": "eu", "PT": "eu", "IE": "eu", "CZ": "eu", "GR": "eu",
    "HU": "eu", "RO": "eu", "SK": "eu", "BG": "eu", "HR": "eu", "SI": "eu",
    "LT": "eu", "LV": "eu", "EE": "eu", "LU": "eu", "MT": "eu", "CY": "eu",
    "US": "na", "CA": "na", "MX": "na",
    "BR": "sa", "AR": "sa", "CL": "sa",
    "AU": "ww", "NZ": "ww", "JP": "ww", "KR": "ww", "SG": "ww", "MY": "ww",
    "TH": "ww", "ID": "ww", "PH": "ww", "VN": "ww", "IN": "ww", "HK": "ww",
    "TW": "ww", "AE": "ww", "SA": "ww", "IL": "ww", "TR": "ww", "EG": "ww",
    "ZA": "ww", "RU": "ww", "UA": "ww",
    "CN": "cn",
}
CONTINENT_TO_HOST = {
    "eu": "https://api-app.dc-eu.ww.ecouser.net/api",
    "na": "https://api-app.dc-na.ww.ecouser.net/api",
    "sa": "https://api-app.dc-sa.ww.ecouser.net/api",
    "ww": "https://portal-ww.ecouser.net/api",
    "cn": "https://portal.ecouser.net/api",
}

# Yeedi-specific app credentials (public - published in the npm package, NOT user secrets).
AUTH_USERLOGIN_AUTH_APPKEY_YD = "1581917520081"
AUTH_USERLOGIN_SECRET_YD = "ed5b3dd9a0253de7d90305d077eb5fee"
AUTH_GETAUTH_AUTH_APPKEY_YD = "1581923437995"
AUTH_GETAUTH_SECRET_YD = "304a71592690995b2bb304e66b5ddee6"
APP_CODE = "yd_global_e"
APP_VERSION = "1.3.0"
CHANNEL = "google_play"
DEVICE_TYPE = "1"
REALM = "ecouser.net"

TIMEOUT = ClientTimeout(total=30)
POLL_INTERVAL = 60  # seconds - matches the existing DataUpdateCoordinator default


class YeediApiError(Exception):
    """Raised on any non-OK response from the Yeedi portal."""


@dataclass
class Device:
    """Discovered Yeedi vacuum bound to the account."""

    did: str
    device_class: str
    device_model: str
    device_name: str
    resource: str


@dataclass
class Auth:
    """Cached credentials after the 3-step login."""

    user_id: str
    user_access_token: str
    expires_at: float  # epoch seconds
    org: str = "ECOYDWW"
    country: str = ""


class YeediClient:
    """Polling HTTP client for Yeedi cloud."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session
        self._auth: Auth | None = None
        self._continent = "eu"
        self._device_id = "ha-" + hashlib.md5(
            str(time.time_ns()).encode()
        ).hexdigest()[:12]

    # ----- public API -------------------------------------------------------

    async def async_login(
        self, email: str, password: str, country: str
    ) -> tuple[Auth, list[Device]]:
        """3-step login + device discovery. Caches auth on the client."""
        self._continent = COUNTRY_TO_CONTINENT.get(country.upper(), "ww")
        country_upper = country.upper()
        country_lower = country.lower()

        # Step 1: user/login -> uid + accessToken
        step1_url = (
            f"{AUTH_GL_API.format(country=country_lower)}"
            f"/v1/private/{country_lower}/EN/{self._device_id}/{APP_CODE}"
            f"/{APP_VERSION}/{CHANNEL}/{DEVICE_TYPE}/user/login"
        )
        step1_password = hashlib.md5(password.encode("utf-8")).hexdigest()
        step1_body = {
            "account": email,
            "password": step1_password,
            "authTime": int(time.time()),
            "authAppKey": AUTH_USERLOGIN_AUTH_APPKEY_YD,
            "authAppSecret": AUTH_USERLOGIN_SECRET_YD,
        }
        async with self._session.post(step1_url, json=step1_body, timeout=TIMEOUT) as r:
            r.raise_for_status()
            step1 = await r.json()
        if "uid" not in step1 or "accessToken" not in step1:
            raise YeediApiError(f"step1/user/login missing fields: {step1}")
        uid = step1["uid"]

        # Step 2: getAuthCode -> authCode
        org = "ECOYDCN" if country_upper == "CN" else "ECOYDWW"
        step2_url = (
            f"{AUTH_GL_OPENAPI.format(country=country_lower)}/v1/global/auth/getAuthCode"
        )
        step2_body = {
            "clientId": uid,
            "clientType": org,
            "appId": AUTH_GETAUTH_AUTH_APPKEY_YD,
            "secret": AUTH_GETAUTH_SECRET_YD,
            "account": email,
            "country": "Chinese" if country_upper == "CN" else country_upper,
            "lang": "EN",
            "deviceId": self._device_id,
        }
        async with self._session.post(step2_url, json=step2_body, timeout=TIMEOUT) as r:
            r.raise_for_status()
            step2 = await r.json()
        if "authCode" not in step2:
            raise YeediApiError(f"getAuthCode missing authCode: {step2}")
        auth_code = step2["authCode"]

        # Step 3: loginByItToken -> user_access_token
        portal_base = CONTINENT_TO_HOST[self._continent]
        step3_url = f"{portal_base}/users/user.do"
        step3_body = {
            "edition": "ECOGLOBLE",
            "userId": uid,
            "token": auth_code,
            "realm": REALM,
            "resource": self._device_id,
            "org": org,
            "last": "",
            "country": "Chinese" if country_upper == "CN" else country_upper,
            "todo": "loginByItToken",
        }
        # Retry up to 3x on "set token error." (matches library behaviour).
        for _ in range(3):
            async with self._session.post(step3_url, json=step3_body, timeout=TIMEOUT) as r:
                r.raise_for_status()
                step3 = await r.json()
            if step3.get("result") == "ok":
                break
            if step3.get("result") == "fail" and step3.get("error") == "set token error.":
                continue
            raise YeediApiError(f"loginByItToken failed: {step3}")
        else:
            raise YeediApiError(f"loginByItToken: failed to login with token after 3 tries")

        validity_ms = int(step3.get("last", 604800000))
        expires_at = time.time() + (validity_ms / 1000) * 0.99
        final_uid = step3.get("userId", uid)

        self._auth = Auth(
            user_id=final_uid,
            user_access_token=step3["token"],
            expires_at=expires_at,
            org=org,
            country=country_upper,
        )

        # Step 4: getDeviceList
        devices = await self._async_get_devices()
        return self._auth, devices

    async def async_send_command(
        self,
        device: Device,
        command: str,
        args: dict[str, Any] | None = None,
    ) -> None:
        """Send a command to the bot via iot/devmanager.do."""
        await self._async_post_command(device, command, args or {})

    async def async_get_state(self, device: Device) -> dict[str, Any]:
        """Poll the bot for current state. Returns {event_name: value}.

        Each Get* command corresponds to one of the 24 event types the JS
        lib would deliver via MQTT push. We poll all the ones the flow wires
        to a sensor, button, select, or switch.
        """
        result: dict[str, Any] = {}

        # BatteryInfo
        d = await self._safe_get(device, "GetBattery")
        if d is not None:
            result["BatteryInfo"] = d.get("power")

        # ChargeState
        d = await self._safe_get(device, "GetChargeState")
        if d is not None:
            result["ChargeState"] = d.get("chargeStatus")

        # SleepStatus
        d = await self._safe_get(device, "GetSleepStatus")
        if d is not None:
            result["SleepStatus"] = d.get("sleepStatus")

        # DoNotDisturbEnabled
        d = await self._safe_get(device, "GetDoNotDisturbEnabled")
        if d is not None:
            result["DoNotDisturbEnabled"] = int(d.get("enable", 0))

        # LifeSpan
        d = await self._safe_get(device, "GetLifeSpan")
        if d is not None:
            result["LifeSpan"] = {
                "filter": d.get("filter"),
                "side_brush": d.get("sideBrush"),
                "main_brush": d.get("mainBrush"),
            }

        # WaterBoxInfo
        d = await self._safe_get(device, "GetWaterBoxInfo")
        if d is not None:
            result["WaterBoxInfo"] = d.get("wbf")

        # WaterLevel
        d = await self._safe_get(device, "GetWaterPermeability")
        if d is not None:
            result["WaterLevel"] = d.get("value")

        # CleanSpeed
        d = await self._safe_get(device, "GetCleanSpeed")
        if d is not None:
            result["CleanSpeed"] = d.get("speed")

        # LastError
        d = await self._safe_get(device, "GetLastError")
        if d is not None:
            result["LastError"] = d.get("error")

        # LastCleanLogs
        d = await self._safe_get(device, "GetCleanLogs")
        if d is not None:
            result["LastCleanLogs"] = {
                "totalTimeFormatted": d.get("lastTime"),
                "squareMeters": d.get("lastArea"),
                "timestamp": d.get("lastTimeStamp"),
            }

        # info
        d = await self._safe_get(device, "GetInfo")
        if d is not None:
            result["info"] = {
                "version": d.get("fwVer"),
                "libraryVersion": None,
                "deviceClass": d.get("deviceClass"),
                "deviceModel": d.get("model"),
                "nickname": d.get("deviceName"),
            }

        # MoppingSystemInfo (always present if power adjustment + mopping system
        # available; emit empty cloud response for now since neither Get* exposes it cleanly)
        result["MoppingSystemInfo"] = {
            "cleanStatus": None,
            "cleanInfo": {"level": result.get("CleanSpeed")},
            "waterInfo": {
                "enabled": bool(int(result.get("WaterBoxInfo") or 0)),
                "level": result.get("WaterLevel"),
            },
        }

        return result

    # ----- internals --------------------------------------------------------

    def _auth_block(self) -> dict[str, str]:
        assert self._auth is not None
        return {
            "realm": REALM,
            "resource": self._device_id,
            "token": self._auth.user_access_token,
            "userid": self._auth.user_id,
            "with": "users",
        }

    async def _async_get_devices(self) -> list[Device]:
        assert self._auth is not None
        portal_base = CONTINENT_TO_HOST[self._continent]
        url = f"{portal_base}/iot/devmanager.do"
        body = {
            "todo": "GetDeviceList",
            "auth": self._auth_block(),
            "userId": self._auth.user_id,
            "realm": REALM,
            "resource": self._device_id,
            "org": self._auth.org,
            "country": "Chinese" if self._auth.country == "CN" else self._auth.country,
            "edition": "ECOGLOBLE",
        }
        async with self._session.post(url, json=body, timeout=TIMEOUT) as r:
            r.raise_for_status()
            data = await r.json(content_type=None)
        if data.get("result") != "ok":
            raise YeediApiError(f"GetDeviceList failed: {data}")

        devices: list[Device] = []
        items = data.get("data") or data.get("devices") or []
        if isinstance(items, dict):
            items = list(items.values())
        for d in items:
            devices.append(
                Device(
                    did=str(d.get("did") or d.get("deviceId")),
                    device_class=str(d.get("deviceClass", "vacuum")),
                    device_model=str(d.get("deviceModel", "yeedi")),
                    device_name=str(
                        d.get("deviceName") or d.get("nickname") or d.get("did")
                    ),
                    resource=str(d.get("resource") or d.get("did")),
                )
            )
        return devices

    async def _async_post_command(
        self, device: Device, command: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        if self._auth is None:
            raise YeediApiError("not authenticated")
        portal_base = CONTINENT_TO_HOST[self._continent]
        url = f"{portal_base}/iot/devmanager.do"
        body = {
            "cmdName": command,
            "payload": args,
            "payloadType": "x",
            "auth": self._auth_block(),
            "td": "q",
            "toId": device.did,
            "toRes": device.resource,
            "toType": device.device_class,
        }
        async with self._session.post(url, json=body, timeout=TIMEOUT) as r:
            r.raise_for_status()
            data = await r.json(content_type=None)
        if data.get("result") != "ok" and data.get("ret") != "ok":
            raise YeediApiError(
                f"command {command!r} failed: errno={data.get('errno')} err={data.get('error')}"
            )
        return data

    async def _safe_get(
        self, device: Device, command: str
    ) -> dict[str, Any] | None:
        """Issue a Get* command; return data dict or None on error."""
        try:
            r = await self._async_post_command(device, command, {})
            return r.get("data") or {}
        except (YeediApiError, ClientResponseError, TimeoutError):
            _LOGGER.debug("Get %s failed", command, exc_info=True)
            return None