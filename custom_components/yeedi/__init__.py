"""Support for Yeedi robot vacuums."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .controller import YeediController

PLATFORMS = [
    Platform.VACUUM,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.CAMERA,
]

type YeediConfigEntry = ConfigEntry[YeediController]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Yeedi component."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: YeediConfigEntry) -> bool:
    """Set up Yeedi from a config entry."""
    controller = YeediController(hass, entry.data)
    entry.async_on_unload(controller.teardown)
    await controller.initialize()
    entry.runtime_data = controller
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: YeediConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data.setdefault(DOMAIN, {}).pop(entry.entry_id, None)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
