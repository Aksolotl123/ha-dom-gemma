"""Dom Gemma - agent rozmowy Assist korzystający z lokalnego modelu (DomGemma Server w sieci domowej)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_TOKEN, CONF_URL
from .link import ServerLink

PLATFORMS = [Platform.CONVERSATION, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # jedno połączenie (z pamięcią działającego adresu) dla agenta i czujnika
    entry.runtime_data = ServerLink(hass, entry.data[CONF_URL], entry.data[CONF_TOKEN])
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
