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

def make_response(json_body, status=200):
    """Build a mock that quacks like an aiohttp ClientResponse context manager."""
    resp = MagicMock()
    resp.status = status
    resp.raise_for_status = MagicMock()
    resp.json = AsyncMock(return_value=json_body)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


# ----- Client (smoke, mocked HTTP) ---------------------------------------


@pytest.mark.asyncio
async def test_client_login_happy_path():
    """3-step login (GET user/login -> GET getAuthCode -> POST loginByItToken) + device list."""
    session = MagicMock()
    session.get = MagicMock(side_effect=[
        make_response({"uid": "uid-123", "accessToken": "step1-tok"}),  # user/login
        make_response({"authCode": "auth-code-xyz"}),                    # getAuthCode
    ])
    session.post = MagicMock(side_effect=[
        make_response({"result": "ok", "token": "long-token", "userId": "uid-123", "last": 604800000}),  # loginByItToken
        make_response({"result": "ok", "data": [{"did": "did-A", "deviceClass": "vacuum",
                                                  "deviceModel": "yeedi 2 hybrid",
                                                  "deviceName": "Tomschin", "resource": "did-A"}]}),  # GetDeviceList
    ])

    client = YeediClient(session, device_id="ha-abcdef012345")
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

    # Step 1 was a GET to the AT login URL.
    step1_url = session.get.call_args_list[0].args[0]
    assert AUTH_GL_API.format(country="at") in step1_url
    # Step 1 query string contains authAppkey + authSign + account + password.
    step1_query = session.get.call_args_list[0].kwargs["params"]
    assert "authAppkey" in step1_query
    assert "authSign" in step1_query
    assert step1_query["account"] == "tomschin@example.com"
    # Password is MD5("hunter2") = 2ab96390c97dbecc7d2e6545fe1322eb
    import hashlib
    assert step1_query["password"] == hashlib.md5(b"hunter2").hexdigest()
    # Step 2 was a GET to the openapi URL.
    step2_url = session.get.call_args_list[1].args[0]
    assert AUTH_GL_OPENAPI.format(country="at") in step2_url
    # Step 3 was a POST to the EU portal users/user.do.
    step3_url = session.post.call_args_list[0].args[0]
    assert step3_url.startswith(CONTINENT_TO_HOST["eu"])
    assert step3_url.endswith("/users/user.do")
    # deviceId flowed into Step 1 URL path and Step 2 query string.
    step1_url = session.get.call_args_list[0].args[0]
    assert "ha-abcdef012345" in step1_url  # in path segment
    step2_query = session.get.call_args_list[1].kwargs["params"]
    assert step2_query["deviceId"] == "ha-abcdef012345"
    # GetDeviceList (the 4th call) is a POST to users/user.do, NOT iot/devmanager.do.
    devlist_url = session.post.call_args_list[1].args[0]
    assert devlist_url.endswith("/users/user.do")
    assert "/iot/" not in devlist_url
    devlist_body = session.post.call_args_list[1].kwargs["json"]
    assert devlist_body["todo"] == "GetDeviceList"
    assert devlist_body["auth"]["token"] == "long-token"


@pytest.mark.asyncio
async def test_client_login_bad_credentials_step1():
    """Step 1 response without uid/accessToken -> YeediApiError."""
    session = MagicMock()
    session.get = MagicMock(return_value=make_response({"foo": "bar"}))
    session.post = MagicMock()

    client = YeediClient(session)
    with pytest.raises(YeediApiError):
        await client.async_login(
            email="a@b", password="pw", country="DE"
        )


@pytest.mark.asyncio
async def test_client_login_1013_raises_device_verification_required():
    """Step 1 returns 1013 -> DeviceVerificationRequired (config flow handles)."""
    from custom_components.yeedi.client import DeviceVerificationRequired
    session = MagicMock()
    session.get = MagicMock(return_value=make_response(
        {"code": 1013, "msg": "Please update to the latest version to continue."}
    ))
    session.post = MagicMock()

    client = YeediClient(session)
    with pytest.raises(DeviceVerificationRequired):
        await client.async_login(email="a@b", password="pw", country="DE")
    # No step 2 / 3 should be attempted.
    assert session.get.call_count == 1
    assert session.post.call_count == 0


@pytest.mark.asyncio
async def test_client_verify_device_flow():
    """1013 -> request verify code -> verify code -> complete_login -> devices."""
    from custom_components.yeedi.client import DeviceVerificationRequired
    session = MagicMock()
    # Order of HTTP calls during the verify recovery:
    # 1. user/login (GET) -> 1013
    # 2. getConfig (GET) -> public key
    # 3. sendEmailVerifyCode (GET) -> code 0000
    # 4. verifyDevice (GET) -> uid + accessToken
    # 5. getAuthCode (GET) -> authCode
    # 6. loginByItToken (POST) -> user_access_token
    # 7. getDeviceList (POST) -> device data
    # Use a real RSA-2048 SPKI public key so _rsa_encrypt_base64 actually
    # succeeds. The encrypted value is never verified in this test.
    import base64 as _b64
    _test_pubkey = _b64.b64decode(
        "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAzNS9MOpTMmvtdlUuzHW8HnKnfSaXQ5a4z2S"
        "gWR4FOAIqQATxkNVbriLeVmFwl1F2y5PqkfdkoUkGCNsWPpQ+a9gWd1GrIoVc7Oe7YUfenmeOtCVNSsM2K"
        "hL8318ZdVIW4udCQrofmunnq407qoNkXOgZKzlsbzATxAXLYTWLulrK/nGUtDr4sc0pQnky/8MYG2f2luTU"
        "eF+ZfoqKWQjj2Wu0iFdbj7c1kKwH/IXM14gtWygxhalRS3SSe6NSBY7FeuposggN4/1qEwa4AG3xRlPdNey"
        "ncoHOlNmeChfeyhmkdd9Wdq6EyBHx50RBTGArrAFjHV0OdG8KIpmYYS6nnwIDAQAB"
    )
    pubkey_b64 = _b64.b64encode(_test_pubkey).decode()
    import json as _json
    getconfig_value = _json.dumps({"publicKey": pubkey_b64})  # single-layer JSON string
    session.get = MagicMock(side_effect=[
        make_response({"code": 1013, "msg": "Please update to the latest version to continue."}),  # user/login
        make_response([{"key": "PUBLIC.KEY.CONFIG", "value": getconfig_value}]),  # getConfig (for sendEmail)
        make_response({"code": "0000", "msg": "success"}),  # sendEmailVerifyCode
        make_response([{"key": "PUBLIC.KEY.CONFIG", "value": getconfig_value}]),  # getConfig (for verifyDevice)
        make_response({"uid": "u-new", "accessToken": "verify-tok"}),  # verifyDevice
        make_response({"authCode": "ac"}),  # getAuthCode
    ])
    session.post = MagicMock(side_effect=[
        make_response({"result": "ok", "token": "long", "userId": "u-new", "last": 604800000}),  # loginByItToken
        make_response({"result": "ok", "data": []}),  # GetDeviceList
    ])

    client = YeediClient(session, device_id="ha-1234567890ab")
    # Step 1: login hits 1013.
    with pytest.raises(DeviceVerificationRequired):
        await client.async_login(email="a@b.com", password="pw", country="US")

    # The full recovery: send email code, then verify, then complete.
    await client.request_device_verification_code(email="a@b.com", country="US")
    verify_resp = await client.verify_device(email="a@b.com", code="654321", country="US")
    assert verify_resp["uid"] == "u-new"
    assert verify_resp["accessToken"] == "verify-tok"
    auth = await client.complete_login(
        uid=verify_resp["uid"],
        login_token=verify_resp["accessToken"],
        org="ECOYDWW",
        country="US",
    )
    assert auth.user_access_token == "long"
    devices = await client._async_get_devices()
    assert devices == []


@pytest.mark.asyncio
async def test_client_login_china_uses_cn_org():
    """Country CN -> org ECOYDCN, portal host portal.ecouser.net."""
    session = MagicMock()
    session.get = MagicMock(side_effect=[
        make_response({"uid": "u1", "accessToken": "t1"}),
        make_response({"authCode": "ac"}),
    ])
    session.post = MagicMock(side_effect=[
        make_response({"result": "ok", "token": "long", "userId": "u1", "last": 604800000}),
        make_response({"result": "ok", "data": []}),
    ])

    client = YeediClient(session)
    auth, _ = await client.async_login(email="x@y", password="pw", country="CN")
    assert auth.org == "ECOYDCN"
    # Portal URL is the CN one.
    portal_call = session.post.call_args_list[0]
    assert portal_call.args[0].startswith("https://portal.ecouser.net/api")


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