"""Constants for the Yeedi integration."""

from __future__ import annotations

from enum import StrEnum

DOMAIN = "yeedi"

# Config flow
CONF_VERIFY_MQTT_CERTIFICATE = "verify_mqtt_certificate"
CONF_OVERRIDE_REST_URL = "override_rest_url"
CONF_OVERRIDE_MQTT_URL = "override_mqtt_url"


class InstanceMode(StrEnum):
    """Instance mode."""

    CLOUD = "cloud"
    SELF_HOSTED = "self_hosted"
