"""Tests for the Yeedi API client."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.yeedi.api import AuthResult, YeediApiClient, YeediApiError, YeediDevice


class MockResponse:
    """Mock aiohttp response."""

    def __init__(self, json_data, status=200):
        self._json = json_data
        self.status = status

    async def json(self):
        return self._json

    def raise_for_status(self):
        if self.status >= 400:
            raise Exception(f"HTTP {self.status}")


class MockSession:
    """Mock aiohttp session."""

    def __init__(self):
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self._respond(method, url, kwargs)

    def _respond(self, method, url, kwargs):
        raise NotImplementedError


def test_auth_result_creation():
    """Test AuthResult dataclass creation."""
    now = datetime.now(UTC)
    auth = AuthResult(access_token="abc123", refresh_token="def456", expires_at=now)
    assert auth.access_token == "abc123"
    assert auth.refresh_token == "def456"
    assert auth.expires_at == now


def test_yee_device_creation():
    """Test YeediDevice dataclass creation."""
    device = YeediDevice(
        device_id="dev-001",
        name="My Vacuum",
        model="Yeedi Vac 2",
        capabilities=["start", "stop", "locate"],
        status={"battery": 85},
        raw={"id": "dev-001", "name": "My Vacuum"},
    )
    assert device.device_id == "dev-001"
    assert device.name == "My Vacuum"
    assert "locate" in device.capabilities
    assert device.status["battery"] == 85


def test_auth_response_parse():
    """Test parsing of auth response."""
    client = YeediApiClient(session=None, base_url="https://api.example.com")

    response = {
        "access_token": "token123",
        "refresh_token": "refresh456",
        "expires_in": 3600,
    }
    auth = client._parse_auth_response(response)
    assert auth.access_token == "token123"
    assert auth.refresh_token == "refresh456"
    assert auth.expires_at is not None
    assert auth.expires_at > datetime.now(UTC)


def test_auth_response_missing_token():
    """Test that missing access_token raises error."""
    client = YeediApiClient(session=None)
    try:
        client._parse_auth_response({"refresh_token": "abc"})
        raise AssertionError("Should have raised YeediApiError")
    except YeediApiError:
        pass


def test_devices_response_parse_list():
    """Test parsing device list response (top-level list)."""
    client = YeediApiClient(session=None)
    response = [
        {"device_id": "d1", "name": "Robot 1", "model": "V1"},
        {"device_id": "d2", "name": "Robot 2", "model": "V2"},
    ]
    devices = client._parse_devices_response(response)
    assert len(devices) == 2
    assert devices[0].device_id == "d1"
    assert devices[1].name == "Robot 2"


def test_devices_response_parse_dict():
    """Test parsing device list response (nested dict)."""
    client = YeediApiClient(session=None)
    response = {"devices": [{"device_id": "d1", "name": "Robot 1"}]}
    devices = client._parse_devices_response(response)
    assert len(devices) == 1
    assert devices[0].device_id == "d1"


def test_devices_response_invalid():
    """Test parsing invalid response raises error."""
    client = YeediApiClient(session=None)
    try:
        client._parse_devices_response("not a list or dict")
        raise AssertionError("Should have raised YeediApiError")
    except YeediApiError:
        pass


def test_devices_response_empty():
    """Test parsing empty device list."""
    client = YeediApiClient(session=None)
    devices = client._parse_devices_response([])
    assert devices == []


def test_devices_response_skip_invalid_items():
    """Test that invalid items in device list are skipped."""
    client = YeediApiClient(session=None)
    response = [
        {"device_id": "d1", "name": "Robot 1"},
        {"device_id": "", "name": "No ID"},  # invalid - empty id
        {"name": "No ID field"},  # invalid - no device_id
        "not a dict",  # invalid - not a dict
    ]
    devices = client._parse_devices_response(response)
    assert len(devices) == 1
    assert devices[0].device_id == "d1"
