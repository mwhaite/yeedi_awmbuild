"""Yeedi API client.

All endpoint paths and response parsing should be aligned to the Node-RED flow.
Do not add or change endpoints here without verifying the forum reference.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .const import LOGGER


class YeediApiError(RuntimeError):
    """Raised when the Yeedi API returns an error or is misconfigured."""


@dataclass(slots=True)
class AuthResult:
    """Authentication result from Yeedi cloud."""

    access_token: str
    refresh_token: str | None
    expires_at: datetime | None


@dataclass(slots=True)
class YeediDevice:
    """Basic device descriptor."""

    device_id: str
    name: str
    model: str | None
    capabilities: list[str]
    status: dict[str, Any] | None
    raw: dict[str, Any]


@dataclass(slots=True)
class YeediRequestContext:
    """Data used to build requests."""

    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: datetime | None = None


# Endpoint paths — replace with actual values from the Node-RED flow.
#
# Expected format (examples — substitute your region and app id):
#   login:          "/api/region/app/login"
#   refresh:        "/api/region/app/token/refresh"
#   devices:        "/api/region/app/device/list"
#   device_status:  "/api/region/app/device/{device_id}/status"
#   command:        "/api/region/app/device/{device_id}/command"
#
# These may be absolute URLs if your setup does not use a base_url.
ENDPOINTS: dict[str, str | None] = {
    "login": None,
    "refresh": None,
    "devices": None,
    "device_status": None,
    "command": None,
}


class YeediApiClient:
    """Async API client for Yeedi cloud endpoints."""

    def __init__(self, session, base_url: str | None = None, region: str | None = None) -> None:
        self._session = session
        self._base_url = base_url
        self._region = region
        self._ctx = YeediRequestContext()
        self.ENDPOINTS = ENDPOINTS.copy()

    @property
    def access_token(self) -> str | None:
        return self._ctx.access_token

    @property
    def refresh_token(self) -> str | None:
        return self._ctx.refresh_token

    @property
    def expires_at(self) -> datetime | None:
        return self._ctx.expires_at

    def set_auth(self, auth: AuthResult) -> None:
        self._ctx.access_token = auth.access_token
        self._ctx.refresh_token = auth.refresh_token
        self._ctx.expires_at = auth.expires_at

    async def async_login(
        self, username: str, password: str, region: str | None = None
    ) -> AuthResult:
        endpoint = self._require_endpoint("login")
        payload = {
            "username": username,
            "password": password,
            "region": region or self._region,
        }
        response = await self._request("POST", endpoint, json=payload, include_auth=False)
        auth = self._parse_auth_response(response)
        self.set_auth(auth)
        return auth

    async def async_refresh_token(self) -> AuthResult:
        endpoint = self._require_endpoint("refresh")
        if not self._ctx.refresh_token:
            raise YeediApiError("Refresh token is missing; re-authentication required.")
        payload = {"refresh_token": self._ctx.refresh_token}
        response = await self._request("POST", endpoint, json=payload, include_auth=False)
        auth = self._parse_auth_response(response)
        self.set_auth(auth)
        return auth

    async def async_list_devices(self) -> list[YeediDevice]:
        endpoint = self._require_endpoint("devices")
        response = await self._request("GET", endpoint)
        return self._parse_devices_response(response)

    async def async_get_device_status(self, device_id: str) -> dict[str, Any]:
        endpoint = self._require_endpoint("device_status")
        endpoint = endpoint.format(device_id=device_id)
        response = await self._request("GET", endpoint)
        if not isinstance(response, dict):
            raise YeediApiError("Device status response is not a JSON object.")
        return response

    async def async_send_command(
        self, device_id: str, command: str, params: dict[str, Any] | None = None
    ) -> None:
        endpoint = self._require_endpoint("command")
        endpoint = endpoint.format(device_id=device_id)
        payload = {
            "device_id": device_id,
            "command": command,
            "params": params or {},
        }
        await self._request("POST", endpoint, json=payload)

    def _require_endpoint(self, key: str) -> str:
        endpoint = self.ENDPOINTS.get(key)
        if not endpoint:
            raise YeediApiError(
                f"Endpoint '{key}' is not configured. "
                "Update ENDPOINTS in api.py using the Node-RED flow details."
            )
        return endpoint

    def _build_url(self, endpoint: str) -> str:
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        if not self._base_url:
            raise YeediApiError("Base URL is not configured for relative endpoints.")
        return f"{self._base_url.rstrip('/')}/{endpoint.lstrip('/')}"

    def _headers(self, include_auth: bool = True) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
        }
        if include_auth and self._ctx.access_token:
            headers["Authorization"] = f"Bearer {self._ctx.access_token}"
        return headers

    def _is_transient_failure(self, err: Exception, response: Any | None) -> bool:
        """Return True if the error is likely transient and worth retrying."""
        # Connection-level errors (no HTTP response)
        import aiohttp

        if isinstance(err, (aiohttp.ClientError, TimeoutError)):
            return True
        # HTTP-level: 5xx server errors
        if response is not None and hasattr(response, "status"):
            return 500 <= response.status < 600
        return False

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json: dict[str, Any] | None = None,
        include_auth: bool = True,
        _retry_count: int = 0,
    ) -> Any:
        url = self._build_url(endpoint)
        headers = self._headers(include_auth=include_auth)
        LOGGER.debug("Yeedi request %s %s (attempt %d)", method, endpoint, _retry_count + 1)
        response = None
        try:
            async with asyncio.timeout(20):
                response = await self._session.request(method, url, json=json, headers=headers)
                response.raise_for_status()
                return await response.json()
        except Exception as err:
            if _retry_count < 2 and self._is_transient_failure(err, response):
                await asyncio.sleep(2**_retry_count)
                return await self._request(
                    method,
                    endpoint,
                    json=json,
                    include_auth=include_auth,
                    _retry_count=_retry_count + 1,
                )
            raise YeediApiError(f"Yeedi API request failed: {err}") from err

    def _parse_auth_response(self, response: Any) -> AuthResult:
        if not isinstance(response, dict):
            raise YeediApiError("Auth response is not a JSON object.")
        access_token = response.get("access_token")
        if not access_token:
            raise YeediApiError(
                "Auth response missing 'access_token'. "
                "Update parsing in api.py to match the Node-RED flow fields."
            )
        refresh_token = response.get("refresh_token")
        expires_at = None
        expires_in = response.get("expires_in")
        if isinstance(expires_in, (int, float)):
            expires_at = datetime.now(UTC) + timedelta(seconds=int(expires_in))
        return AuthResult(
            access_token=access_token, refresh_token=refresh_token, expires_at=expires_at
        )

    def _parse_devices_response(self, response: Any) -> list[YeediDevice]:
        if isinstance(response, list):
            devices = response
        elif isinstance(response, dict):
            devices = response.get("devices")
        else:
            devices = None
        if not isinstance(devices, list):
            raise YeediApiError(
                "Device list response is not recognized. "
                "Update parsing in api.py to match the Node-RED flow fields."
            )
        parsed: list[YeediDevice] = []
        for item in devices:
            if not isinstance(item, dict):
                continue
            device_id = str(item.get("device_id") or item.get("id") or "").strip()
            if not device_id:
                continue
            parsed.append(
                YeediDevice(
                    device_id=device_id,
                    name=str(item.get("name") or item.get("nickname") or device_id),
                    model=item.get("model"),
                    capabilities=list(item.get("capabilities") or []),
                    status=item.get("status"),
                    raw=item,
                )
            )
        return parsed
