"""Constants for the Yeedi integration.

Every value here corresponds to a field in Tomschin's Node-RED flow
(comment #48 on community.home-assistant.io/t/348575). Nothing invented.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "yeedi"

# Account node fields (ecovacs-account)
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"
CONF_COUNTRY: Final = "country"
CONF_LOGIN_HOST: Final = "login_host"   # always "yeedi.com" but explicit per flow
CONF_POLL_SECONDS: Final = "poll_seconds"

# Defaults from the flow
DEFAULT_LOGIN_HOST: Final = "yeedi.com"
DEFAULT_POLL_SECONDS: Final = 60

PLATFORMS: Final[tuple[str, ...]] = ("sensor", "button", "select", "switch")

# Event types emitted by the bot. Mirror of the switch 'Type' rules in the flow.
EVENT_TYPES: Final[tuple[str, ...]] = (
    "info",                          # 0
    "ChargeState",                   # 1
    "SleepStatus",                   # 2
    "BatteryInfo",                   # 3
    "DoNotDisturbEnabled",           # 4
    "LastCleanLogs",                 # 5
    "LifeSpan",                      # 6
    "WaterBoxInfo",                  # 7
    "WaterLevel",                    # 8
    "LastError",                     # 9
    "MoppingSystemInfo",             # 10
    "CleanSpeed",                    # 11
    "CurrentUsedSpotAreas",          # 12
    "CleanSum",                      # 13
    "CurrentUsedCustomAreaValues",   # 14
    "CleanReport",                   # 15
    "CleanLog",                      # 16
    "CurrentSpotAreaID",             # 17
    "ChargingPosition",              # 18
    "Position",                      # 19
    "RelocationState",               # 20
    "ContinuousCleaningEnabled",     # 21
    "CarpetPressure",                # 22
    "DusterRemind",                  # 23
)

# Sensor mapping: (event_type, state_field_path).
# - state_field_path is a JS-style dotted path inside the message payload.
# - name matches the ha-sensor entity name in the flow.
# - device_class is from haConfig.device_class on the ha-sensor node.
SENSORS: Final[tuple[dict, ...]] = (
    {"event": "BatteryInfo",         "path": "payload.value",                     "name": "Battery",                "device_class": "battery"},
    {"event": "LastCleanLogs",       "path": "payload.value.totalTimeFormatted",  "name": "Cleaning time"},
    {"event": "LifeSpan",            "path": "payload.value.filter",              "name": "LifeSpanFilter"},
    {"event": "LifeSpan",            "path": "payload.value.side_brush",          "name": "LifeSpanSideBrush"},
    {"event": "LifeSpan",            "path": "payload.value.main_brush",          "name": "LifeSpanMainBrush"},
    {"event": "ChargeState",         "path": "payload.value",                     "name": "ChargeState"},
    {"event": "SleepStatus",         "path": "payload.value",                     "name": "SleepStatus"},
    {"event": "WaterBoxInfo",        "path": "payload.value",                     "name": "WaterBoxInfo"},
    {"event": "DoNotDisturbEnabled", "path": "payload.value",                     "name": "DoNotDisturbEnabled"},
    {"event": "info",                "path": "payload.value.version",             "name": "infoVersion"},
    {"event": "info",                "path": "payload.value.libraryVersion",      "name": "infoLibraryVersion"},
    {"event": "info",                "path": "payload.value.deviceClass",         "name": "infoDeviceClass"},
    {"event": "info",                "path": "payload.value.deviceModel",         "name": "infoDeviceModel"},
    {"event": "info",                "path": "payload.value.nickname",            "name": "infoNickname"},
    {"event": "LastError",           "path": "payload.value",                     "name": "Last error"},
    {"event": "CleanSpeed",          "path": "payload.value",                     "name": "Clean Speed"},
    {"event": "WaterLevel",          "path": "payload.value",                     "name": "WaterLevel"},
)

# Button mapping: entity name -> ecovacs-deebot-command name + arg.
BUTTONS: Final[tuple[dict, ...]] = (
    {"name": "Start",     "command": "Clean"},
    {"name": "Return",    "command": "Charge"},
    {"name": "Pause",     "command": "Pause"},
    {"name": "Find",      "command": "FindMe",     "arg": "30"},
    {"name": "Backwards", "command": "MoveBackward"},
)

# Select mapping: select entity -> command name + write-back value.
# The flow's two triggers fire api-call-service select.select_option with
# option="2" on every WaterLevel/CleanSpeed event to push the current HA
# select value back into the bot on connect.
SELECTS: Final[tuple[dict, ...]] = (
    {"name": "Water level select", "command": "SetWaterLevel", "startup_value": "2"},
    {"name": "Clean Speed",        "command": "SetCleanSpeed", "startup_value": "2"},
)

# Switch mapping: switch name -> (on command + args, off command).
SWITCHES: Final[tuple[dict, ...]] = (
    {
        "name": "Do Not Disturb",
        "on_command": "EnableDoNotDisturb",
        "on_arg": "20:00",
        "on_arg2": "6:00",
        "off_command": "DisableDoNotDisturb",
    },
)

# Device metadata from ha-device-config in the flow.
DEVICE_NAME: Final = "yeedi vacuum cleaner"
DEVICE_MANUFACTURER: Final = "Ecovac"
DEVICE_MODEL: Final = "yeedi 2 hybrid"
DEVICE_SW_VERSION: Final = "0.2"

# Country picker for config flow (alpha-2 codes the user can select).
COUNTRIES: Final[tuple[str, ...]] = (
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "NO", "PL", "PT", "RO",
    "SK", "SI", "ES", "SE", "CH", "GB",
    "US", "CA", "MX",
    "BR", "AR", "CL",
    "AU", "NZ", "JP", "KR", "SG", "MY", "TH", "ID", "PH", "VN", "IN",
    "HK", "TW", "AE", "SA", "IL", "TR", "EG", "ZA", "RU", "UA",
    "CN",
)