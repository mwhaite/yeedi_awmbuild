"""Constants for the Yeedi integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.const import Platform

DOMAIN = "yeedi"

CONF_REGION = "region"
CONF_BASE_URL = "base_url"
CONF_POLL_INTERVAL = "poll_interval"

DEFAULT_POLL_INTERVAL = timedelta(seconds=60)

DATA_CLIENT = "client"
DATA_COORDINATOR = "coordinator"

PLATFORMS: list[Platform] = ["vacuum"]

LOGGER = logging.getLogger(__name__)
