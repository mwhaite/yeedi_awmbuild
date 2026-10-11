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

import base64
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from aiohttp import ClientResponseError, ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)

# ----- URL templates (from library/constants.js of ecovacs-deebot.js 1.0.0-alpha.0) -----
#
# Auth (GET with query params + authSign MD5):
#   Step 1: GET  gl-{country}-api.yeedi.com/v1/private/{country}/EN/{deviceId}/
#                 {appCode}/{appVersion}/{channel}/{deviceType}/user/login
#                 ?account=...&password=...&authAppkey=...&authSign=...&requestId=...
#                 &authTimespan=...&country=...&lang=EN&deviceId=...&appCode=...
#                 &appVersion=...&channel=google_play&deviceType=1
#   Step 2: GET  gl-{country}-openapi.yeedi.com/v1/global/auth/getAuthCode
#                 ?openId=global&uid=...&accessToken=...&bizType=...&deviceId=...&...
#   Step 3: POST <portal>/users/user.do           -> user_access_token (long-lived)
#
# Steady-state (commands + polling):
#   POST <portal>/iot/devmanager.do               -> commands + Get* state

AUTH_GL_API = "https://gl-{country}-api.yeedi.com"
AUTH_GL_OPENAPI = "https://gl-{country}-openapi.yeedi.com"
VERIFY_APP_VERSION = "3.14.0"
VERIFY_GETCONFIG_PATH = "common/getConfig"
VERIFY_SENDEMAIL_PATH = "user/sendEmailVerifyCode"
VERIFY_DEVICE_PATH = "user/verifyDevice"
VERIFY_PUBLIC_KEY_CONFIG_KEY = "PUBLIC.KEY.CONFIG"

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
    # Legacy portal hosts (used by callPortalApi for users/user.do and
    # appsvr/app.do — confirmed from mrbungle64/ecovacs-deebot.js
    # getPortalUrlFormat: non-CN countries use portal-{continent}.ecouser.net).
    "eu": "https://portal-eu.ecouser.net/api",
    "na": "https://portal-na.ecouser.net/api",
    "sa": "https://portal-sa.ecouser.net/api",
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


class DeviceVerificationRequired(YeediApiError):
    """Raised when the API returns 1013 — user must complete email verification.

    Caller should then call request_device_verification_code() to receive the
    emailed code, then verify_device(code) to mint uid + accessToken.
    """


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
    """Polling HTTP client for Yeedi cloud.

    Authentication follows the mrbungle64/ecovacs-deebot.js 1.0.0-alpha.0 flow:
    every private-API call is a GET whose query string carries the authSign
    MD5 over a sorted concatenation of (appkey + sorted(meta+params) + secret).
    New deviceIds trigger the 1013 "Please update to the latest version" code;
    the client surfaces DeviceVerificationRequired, the config flow then asks
    the user to enter the email code, and verify_device() finalises login.
    """

    def __init__(
        self,
        session: ClientSession,
        device_id: str | None = None,
        auth: Auth | None = None,
    ) -> None:
        self._session = session
        self._auth = auth
        self._continent = "eu"
        # Stable deviceId: caller should pass one persisted across restarts.
        # Fall back to a new random ID (legacy behaviour) if not provided.
        if device_id is None:
            device_id = "ha-" + hashlib.md5(
                str(time.time_ns()).encode()
            ).hexdigest()[:12]
        self._device_id = device_id

    # ----- public API -------------------------------------------------------

    @property
    def device_id(self) -> str:
        return self._device_id

    @property
    def auth(self) -> Auth | None:
        return self._auth

    def restore_auth(self, auth: Auth, country: str) -> None:
        """Re-attach a previously-cached Auth to this client (skip login)."""
        self._auth = auth
        self._continent = COUNTRY_TO_CONTINENT.get(country.upper(), "ww")

    # ----- meta + sign helpers ---------------------------------------------

    def _meta(self, country_upper: str) -> dict[str, str]:
        return {
            "country": country_upper,
            "lang": "EN",
            "deviceId": self._device_id,
            "appCode": APP_CODE,
            "appVersion": APP_VERSION,
            "channel": CHANNEL,
            "deviceType": DEVICE_TYPE,
        }

    def _sign_meta(self, country_upper: str) -> dict[str, str]:
        """Meta used INSIDE the authSign computation.

        For `user/login` (and the device-verification endpoints), the country
        value in the sign is **lowercase** while the path uses lowercase too.
        Probing the live API confirmed: signing with country=US yields
        ``0002 Parameter error``; signing with country=us yields ``0000``.
        """
        m = self._meta(country_upper)
        m["country"] = country_upper.lower()
        return m

    def _verify_meta(self, country_upper: str) -> dict[str, str]:
        """Same as _meta but with country lowercased and the verify appVersion.

        The verification endpoints use the same path structure as user/login
        but with `appVersion=3.14.0` (per ecovacs-deebot.js constants).
        """
        m = self._meta(country_upper)
        m["country"] = country_upper.lower()
        m["appVersion"] = VERIFY_APP_VERSION
        return m

    def _build_query(
        self,
        params: dict[str, Any],
        sign_params: dict[str, Any],
        appkey: str,
        secret: str,
    ) -> dict[str, str]:
        """Add authAppkey + authSign (MD5) into the query dict, return as strs.

        Sign text: appkey + concat(k + "=" + v for k,v in sorted(sign_params)) + secret
        """
        keys = sorted(sign_params.keys())
        sign_text = appkey + "".join(f"{k}={sign_params[k]}" for k in keys) + secret
        sign = hashlib.md5(sign_text.encode("utf-8")).hexdigest()
        out = {str(k): str(v) for k, v in params.items()}
        out["authAppkey"] = appkey
        out["authSign"] = sign
        return out

    def _request_id(self) -> str:
        """md5 of a fresh uuid4 (matches ecovacs-deebot.js uniqid+md5)."""
        return hashlib.md5(uuid.uuid4().hex.encode("utf-8")).hexdigest()

    # ----- auth API calls --------------------------------------------------

    async def _user_login_get(
        self, email: str, password_hash: str, country: str
    ) -> dict[str, Any]:
        """Step 1: user/login -> {uid, accessToken} or 1013.

        The Yeedi server returns 1013 ("Please update to the latest version to
        continue") for a fresh, never-verified deviceId. The caller catches
        DeviceVerificationRequired and continues with the email-verify flow.
        """
        country_lower = country.lower()
        url = (
            f"{AUTH_GL_API.format(country=country_lower)}"
            f"/v1/private/{country_lower}/EN/{self._device_id}/{APP_CODE}"
            f"/{APP_VERSION}/{CHANNEL}/{DEVICE_TYPE}/user/login"
        )
        now_ms = int(time.time() * 1000)
        params = {
            "account": email,
            "password": password_hash,
            "requestId": self._request_id(),
            "authTimespan": now_ms,
            "authTimeZone": "GMT-8",
        }
        # CRITICAL: sign with country LOWERCASE. Live API returns 0002 with
        # uppercase, 0000 with lowercase. The path segment stays lowercase too.
        meta = self._sign_meta(country.upper())
        sign_params = {**meta, **params}
        query = self._build_query(
            params, sign_params,
            AUTH_USERLOGIN_AUTH_APPKEY_YD, AUTH_USERLOGIN_SECRET_YD,
        )
        async with self._session.get(url, params=query, timeout=TIMEOUT) as r:
            data = await r.json(content_type=None)
        # Response is wrapped: {"code":"0000", "data":{uid, accessToken, ...}}
        # Errors come as {"code":"1013", "msg": "..."} with no `data` key.
        if isinstance(data, dict):
            code = data.get("code")
            if code == 1013 or code == "1013":
                raise DeviceVerificationRequired(
                    f"user/login returned 1013: {data.get('msg')}"
                )
            if code and code != "0000" and code != 0:
                raise YeediApiError(f"user/login error {code}: {data.get('msg')}")
            payload = data.get("data") if "data" in data else data
            if isinstance(payload, dict) and "uid" in payload and "accessToken" in payload:
                return payload
        raise YeediApiError(f"user/login missing uid/accessToken: {data}")

    async def _get_auth_code_get(
        self, uid: str, access_token: str, country: str
    ) -> str:
        """Step 2: getAuthCode -> authCode."""
        url = f"{AUTH_GL_OPENAPI.format(country=country.lower())}/v1/global/auth/getAuthCode"
        params = {
            "openId": "global",
            "uid": uid,
            "accessToken": access_token,
            "bizType": "",
            "deviceId": self._device_id,
            "authTimespan": int(time.time() * 1000),
        }
        # The getAuthCode sign uses the GETAUTH appkey+secret, not the userlogin ones.
        query = self._build_query(
            params, params,
            AUTH_GETAUTH_AUTH_APPKEY_YD, AUTH_GETAUTH_SECRET_YD,
        )
        async with self._session.get(url, params=query, timeout=TIMEOUT) as r:
            data = await r.json(content_type=None)
        if isinstance(data, dict):
            payload = data.get("data") if "data" in data else data
            if isinstance(payload, dict) and "authCode" in payload:
                return payload["authCode"]
            if isinstance(payload, str) and payload:
                # Some paths return authCode at top level when there's no envelope
                return payload
        raise YeediApiError(f"getAuthCode missing authCode: {data}")

    async def _login_by_it_token_post(
        self, uid: str, auth_code: str, org: str, country_upper: str
    ) -> dict[str, Any]:
        """Step 3: loginByItToken -> user_access_token (long-lived portal token)."""
        portal_base = CONTINENT_TO_HOST[self._continent]
        url = f"{portal_base}/users/user.do"
        body = {
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
        for _ in range(3):
            async with self._session.post(url, json=body, timeout=TIMEOUT) as r:
                data = await r.json(content_type=None)
            if data.get("result") == "ok":
                return data
            if data.get("result") == "fail" and data.get("error") == "set token error.":
                continue
            raise YeediApiError(f"loginByItToken failed: {data}")
        raise YeediApiError("loginByItToken: failed to login with token after 3 tries")

    # ----- device verification flow (1013 recovery) ------------------------

    async def _fetch_verify_public_key(self, country: str) -> str:
        """GET common/getConfig?keys=PUBLIC.KEY.CONFIG -> base64 public key.

        The response value is itself a JSON-encoded string containing
        {"publicKey": "<base64>"}; we parse the outer list and the inner JSON.
        """
        country_lower = country.lower()
        url = (
            f"{AUTH_GL_API.format(country=country_lower)}"
            f"/v1/private/{country_lower}/EN/{self._device_id}/{APP_CODE}"
            f"/{VERIFY_APP_VERSION}/{CHANNEL}/{DEVICE_TYPE}/{VERIFY_GETCONFIG_PATH}"
        )
        now_ms = int(time.time() * 1000)
        params = {
            "keys": VERIFY_PUBLIC_KEY_CONFIG_KEY,
            "requestId": self._request_id(),
            "authTimespan": now_ms,
            "authTimeZone": "GMT-8",
        }
        meta = self._verify_meta(country.upper())
        sign_params = {**meta, **params}
        query = self._build_query(
            params, sign_params,
            AUTH_USERLOGIN_AUTH_APPKEY_YD, AUTH_USERLOGIN_SECRET_YD,
        )
        async with self._session.get(url, params=query, timeout=TIMEOUT) as r:
            data = await r.json(content_type=None)
        # Outer: list of {"key": "...", "value": "<json-string>"}
        entry = next(
            (e for e in data if isinstance(e, dict) and e.get("key") == VERIFY_PUBLIC_KEY_CONFIG_KEY),
            None,
        )
        if entry is None or "value" not in entry:
            raise YeediApiError(f"getConfig missing {VERIFY_PUBLIC_KEY_CONFIG_KEY}: {data}")
        # Value is itself a JSON-encoded string with publicKey inside.
        inner = entry["value"]
        if isinstance(inner, str):
            import json as _json
            inner = _json.loads(inner)
        if "publicKey" not in inner:
            raise YeediApiError(f"verify public key missing: {inner}")
        return inner["publicKey"]

    @staticmethod
    def _rsa_encrypt_base64(text: str, public_key_b64: str) -> str:
        """RSA-PKCS1v15 encrypt text with a base64 SPKI public key, return base64."""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        der = base64.b64decode(public_key_b64)
        pub = serialization.load_der_public_key(der)
        assert isinstance(pub, rsa.RSAPublicKey), "expected RSA public key"
        ciphertext = pub.encrypt(text.encode("utf-8"), padding.PKCS1v15())
        return base64.b64encode(ciphertext).decode("ascii")

    async def request_device_verification_code(
        self, email: str, country: str
    ) -> None:
        """Send the verification code to the user's email. (Step 1 of recovery.)"""
        country_lower = country.lower()
        url = (
            f"{AUTH_GL_API.format(country=country_lower)}"
            f"/v1/private/{country_lower}/EN/{self._device_id}/{APP_CODE}"
            f"/{VERIFY_APP_VERSION}/{CHANNEL}/{DEVICE_TYPE}/{VERIFY_SENDEMAIL_PATH}"
        )
        public_key = await self._fetch_verify_public_key(country)
        encrypt_email = self._rsa_encrypt_base64(email, public_key)
        now_ms = int(time.time() * 1000)
        params = {
            "encryptEmail": encrypt_email,
            "verifyType": "EMAIL_VERIFY_DEVICE",
            "supportChar": "N",
            "isForce": "N",
            "requestId": self._request_id(),
            "authTimespan": now_ms,
            "authTimeZone": "GMT-8",
        }
        meta = self._verify_meta(country.upper())
        sign_params = {**meta, **params}
        query = self._build_query(
            params, sign_params,
            AUTH_USERLOGIN_AUTH_APPKEY_YD, AUTH_USERLOGIN_SECRET_YD,
        )
        async with self._session.get(url, params=query, timeout=TIMEOUT) as r:
            data = await r.json(content_type=None)
        if not isinstance(data, dict) or data.get("code") != "0000":
            raise YeediApiError(f"sendEmailVerifyCode failed: {data}")

    async def verify_device(
        self, email: str, code: str, country: str
    ) -> dict[str, Any]:
        """Confirm the emailed code. Returns {uid, accessToken} (Step 2 of recovery)."""
        country_lower = country.lower()
        url = (
            f"{AUTH_GL_API.format(country=country_lower)}"
            f"/v1/private/{country_lower}/EN/{self._device_id}/{APP_CODE}"
            f"/{VERIFY_APP_VERSION}/{CHANNEL}/{DEVICE_TYPE}/{VERIFY_DEVICE_PATH}"
        )
        public_key = await self._fetch_verify_public_key(country)
        encrypt_account = self._rsa_encrypt_base64(email, public_key)
        now_ms = int(time.time() * 1000)
        params = {
            "encryptAccount": encrypt_account,
            "backUpEmail": "",
            "verifyCode": code.strip(),
            "model": "HA-Integration",
            "system": "HomeAssistant",
            "requestId": self._request_id(),
            "authTimespan": now_ms,
            "authTimeZone": "GMT-8",
        }
        meta = self._verify_meta(country.upper())
        sign_params = {**meta, **params}
        query = self._build_query(
            params, sign_params,
            AUTH_USERLOGIN_AUTH_APPKEY_YD, AUTH_USERLOGIN_SECRET_YD,
        )
        async with self._session.get(url, params=query, timeout=TIMEOUT) as r:
            data = await r.json(content_type=None)
        if isinstance(data, dict):
            payload = data.get("data") if "data" in data else data
            if isinstance(payload, dict) and "uid" in payload and "accessToken" in payload:
                return payload
            if "uid" in data and "accessToken" in data:
                return data
        raise YeediApiError(f"verifyDevice missing fields: {data}")

    # ----- top-level login flow --------------------------------------------

    async def async_login(
        self, email: str, password: str, country: str
    ) -> tuple[Auth, list[Device]]:
        """3-step login + device discovery. Caches auth on the client.

        On 1013 (DeviceVerificationRequired), the config flow must catch that,
        ask the user for the emailed code, call request_device_verification_code,
        then verify_device, then complete_login.
        """
        self._continent = COUNTRY_TO_CONTINENT.get(country.upper(), "ww")
        country_upper = country.upper()
        org = "ECOYDCN" if country_upper == "CN" else "ECOYDWW"

        pw_hash = hashlib.md5(password.encode("utf-8")).hexdigest()

        try:
            step1 = await self._user_login_get(email, pw_hash, country)
            uid = step1["uid"]
            login_token = step1["accessToken"]
        except DeviceVerificationRequired as e:
            # Re-raise so config flow can drive the email verification.
            raise

        auth = await self.complete_login(uid, login_token, org, country_upper)
        devices = await self._async_get_devices()
        return auth, devices

    async def complete_login(
        self, uid: str, login_token: str, org: str, country: str
    ) -> Auth:
        """Run steps 2 + 3 given a known uid+accessToken, return cached Auth."""
        country_upper = country.upper()
        auth_code = await self._get_auth_code_get(uid, login_token, country)
        step3 = await self._login_by_it_token_post(
            uid, auth_code, org, country_upper
        )
        validity_ms = int(step3.get("last") or 604800000)
        expires_at = time.time() + (validity_ms / 1000) * 0.99
        final_uid = step3.get("userId") or uid
        self._auth = Auth(
            user_id=final_uid,
            user_access_token=step3["token"],
            expires_at=expires_at,
            org=org,
            country=country_upper,
        )
        return self._auth

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
        """Discover devices bound to the account.

        GetDeviceList is a USER-level query, not a device-level command —
        it goes to ``users/user.do``, not ``iot/devmanager.do``. Confirmed
        from the bmartin5692/sucks protocol.md (POST users/user.do
        GetDeviceList, separate from iot/devmanager.do command path).
        """
        assert self._auth is not None
        portal_base = CONTINENT_TO_HOST[self._continent]
        url = f"{portal_base}/users/user.do"
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

        # Response is at the top level: {"result":"ok", "devices":[{did, ...}, ...]}
        # (per ecovacs-deebot.js getDevices() which returns data['devices']).
        devices: list[Device] = []
        items = data.get("devices") or []
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