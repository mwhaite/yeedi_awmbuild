"""Tests for the Yeedi integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.yeedi.client import (
    AUTH_GL_API,
    AUTH_GL_OPENAPI,
    CONTINENT_TO_HOST,
    COUNTRY_TO_CONTINENT,
    Device,
    YeediApiError,
    YeediClient,
)
from custom_components.yeedi.const import (
    BUTTONS,
    COUNTRIES,
    EVENT_TYPES,
    SELECTS,
    SENSORS,
    SWITCHES,
)
from custom_components.yeedi.entity import extract


# ----- entity.extract -----------------------------------------------------

def test_extract_simple():
    """BatteryInfo: payload.value path."""
    state = {"BatteryInfo": 87}
    assert extract(state, "BatteryInfo", "payload.value") == 87


def test_extract_nested_path():
    """LastCleanLogs: payload.value.totalTimeFormatted path."""
    state = {"LastCleanLogs": {"totalTimeFormatted": "1h 23m"}}
    assert (
        extract(state, "LastCleanLogs", "payload.value.totalTimeFormatted")
        == "1h 23m"
    )


def test_extract_missing_event_returns_none():
    assert extract({}, "BatteryInfo", "payload.value") is None
    assert extract({"BatteryInfo": None}, "BatteryInfo", "payload.value") is None


def test_extract_missing_dict_key_returns_none():
    """LastCleanLogs present but totalTimeFormatted missing."""
    state = {"LastCleanLogs": {"other": "x"}}
    assert (
        extract(state, "LastCleanLogs", "payload.value.totalTimeFormatted") is None
    )


def test_extract_strips_payload_value_prefix():
    """Same result whether the path is "payload.value.x" or "x" when
    the polled state already dropped payload.value."""
    state = {"info": {"version": "1.0"}}
    assert extract(state, "info", "payload.value.version") == "1.0"
    # Path "payload.value" exactly (BatteryInfo shape) returns the raw value.
    state2 = {"BatteryInfo": 87}
    assert extract(state2, "BatteryInfo", "payload.value") == 87


# ----- Spec inventory ---------------------------------------------------

def test_event_types_count():
    """The spec lists 24 event types, matching the flow's switch 'Type' rules."""
    assert len(EVENT_TYPES) == 24
    assert "info" in EVENT_TYPES
    assert "BatteryInfo" in EVENT_TYPES
    assert "ChargeState" in EVENT_TYPES


def test_sensor_count_matches_flow():
    """17 sensors, one per ha-sensor node in the flow."""
    assert len(SENSORS) == 17


def test_button_count_matches_flow():
    """5 buttons, one per ha-button node in the flow."""
    assert len(BUTTONS) == 5
    commands = {b["command"] for b in BUTTONS}
    assert commands == {"Clean", "Charge", "Pause", "FindMe", "MoveBackward"}


def test_select_count_matches_flow():
    """2 selects, one per ha-select node."""
    assert len(SELECTS) == 2


def test_switch_count_matches_flow():
    """1 switch (Do Not Disturb)."""
    assert len(SWITCHES) == 1
    sw = SWITCHES[0]
    assert sw["on_command"] == "EnableDoNotDisturb"
    assert sw["off_command"] == "DisableDoNotDisturb"
    assert sw["on_arg"] == "20:00"
    assert sw["on_arg2"] == "6:00"


def test_country_list_includes_flow_country():
    """Flow uses AT; the country picker must include it."""
    assert "AT" in COUNTRIES


# ----- Client (smoke, mocked HTTP) ---------------------------------------

@pytest.mark.asyncio
async def test_client_login_happy_path():
    """3-step login + device list, with mocked HTTP."""
    session = MagicMock()
    # Each call to session.post returns an async context manager.
    def make_post(json_body, status=200):
        resp = MagicMock()
        resp.status = status
        resp.raise_for_status = MagicMock()
        resp.json = AsyncMock(return_value=json_body)
        resp_cm = MagicMock()
        resp_cm.__aenter__ = AsyncMock(return_value=resp)
        resp_cm.__aexit__ = AsyncMock(return_value=None)
        return resp_cm

    responses = [
        make_post({"uid": "uid-123", "accessToken": "step1-tok"}),     # user/login
        make_post({"authCode": "auth-code-xyz"}),                       # getAuthCode
        make_post({"result": "ok", "token": "long-token", "userId": "uid-123", "last": 604800000}),  # loginByItToken
        make_post({"result": "ok", "data": [{"did": "did-A", "deviceClass": "vacuum",
                                              "deviceModel": "yeedi 2 hybrid",
                                              "deviceName": "Tomschin", "resource": "did-A"}]}),  # GetDeviceList
    ]
    session.post = MagicMock(side_effect=responses)

    client = YeediClient(session)
    auth, devices = await client.async_login(
        email="tomschin@example.com", password="hunter2", country="AT"
    )

    assert auth.user_id == "uid-123"
    assert auth.user_access_token == "long-token"
    assert auth.country == "AT"
    assert auth.org == "ECOYDWW"
    assert len(devices) == 1
    assert devices[0].did == "did-A"
    assert devices[0].device_name == "Tomschin"

    # Step 1 went to AT login URL.
    call_urls = [c.args[0] for c in session.post.call_args_list]
    assert AUTH_GL_API.format(country="at") in call_urls[0]
    # Step 2 went to the openapi URL.
    assert AUTH_GL_OPENAPI.format(country="at") in call_urls[1]
    # Step 3 went to the EU portal.
    assert call_urls[2].startswith(CONTINENT_TO_HOST["eu"])


@pytest.mark.asyncio
async def test_client_login_bad_credentials_step1():
    """Step 1 response without uid/accessToken -> YeediApiError."""
    session = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = AsyncMock(return_value={"foo": "bar"})
    resp_cm = MagicMock()
    resp_cm.__aenter__ = AsyncMock(return_value=resp)
    resp_cm.__aexit__ = AsyncMock(return_value=None)
    session.post = MagicMock(return_value=resp_cm)

    client = YeediClient(session)
    with pytest.raises(YeediApiError):
        await client.async_login(
            email="a@b", password="pw", country="DE"
        )


@pytest.mark.asyncio
async def test_client_login_china_uses_cn_org():
    """Country CN -> org ECOYDCN, portal host portal.ecouser.net."""
    session = MagicMock()
    def make_post(json_body):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = AsyncMock(return_value=json_body)
        resp_cm = MagicMock()
        resp_cm.__aenter__ = AsyncMock(return_value=resp)
        resp_cm.__aexit__ = AsyncMock(return_value=None)
        return resp_cm

    session.post = MagicMock(side_effect=[
        make_post({"uid": "u1", "accessToken": "t1"}),
        make_post({"authCode": "ac"}),
        make_post({"result": "ok", "token": "long", "userId": "u1", "last": 604800000}),
        make_post({"result": "ok", "data": []}),
    ])

    client = YeediClient(session)
    auth, _ = await client.async_login(email="x@y", password="pw", country="CN")
    assert auth.org == "ECOYDCN"
    # Portal URL is the CN one.
    portal_call = session.post.call_args_list[2]
    assert portal_call.args[0].startswith("https://portal.cn.ecouser.net/api") \
        or portal_call.args[0].startswith("https://portal.ecouser.net/api")


@pytest.mark.asyncio
async def test_client_send_command():
    """async_send_command posts cmdName + payload to the portal."""
    session = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = AsyncMock(return_value={"result": "ok", "data": {}})
    resp_cm = MagicMock()
    resp_cm.__aenter__ = AsyncMock(return_value=resp)
    resp_cm.__aexit__ = AsyncMock(return_value=None)
    session.post = MagicMock(return_value=resp_cm)

    client = YeediClient(session)
    client._auth = type("A", (), {
        "user_id": "u", "user_access_token": "tok", "expires_at": 9999999999,
        "org": "ECOYDWW", "country": "AT",
    })()
    client._continent = "eu"

    device = Device(did="did-1", device_class="vacuum",
                    device_model="yeedi 2 hybrid", device_name="X", resource="did-1")

    await client.async_send_command(device, "Clean", {})

    body = session.post.call_args.kwargs["json"]
    assert body["cmdName"] == "Clean"
    assert body["toId"] == "did-1"
    assert body["auth"]["token"] == "tok"


def test_country_to_continent_at_is_eu():
    """AT (the flow's country code) maps to continent eu."""
    assert COUNTRY_TO_CONTINENT["AT"] == "eu"