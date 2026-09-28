"""Czujnik łączności z DomGemma Server. Gdy serwer nie odpowiada, agent po cichu przekazuje zdania wbudowanemu
agentowi HA (opcja) - ten czujnik pozwala to zauważyć i ustawić powiadomienie."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import aiohttp

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_URL, DOMAIN, HEALTH_TIMEOUT

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([ServerConnectivity(entry)], update_before_add=True)


class ServerConnectivity(BinarySensorEntity):
    """on = serwer odpowiada na /health i model jest gotowy; off = brak odpowiedzi albo model się ładuje."""

    _attr_has_entity_name = True
    _attr_translation_key = "server"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_server"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}
        self._attr_is_on = False
        self._attr_extra_state_attributes = {}

    async def async_update(self) -> None:
        # /health jest bez tokenu; błąd sieci to stan „off”, a nie „niedostępny” - o to właśnie chodzi w czujniku
        try:
            async with asyncio.timeout(HEALTH_TIMEOUT):
                async with async_get_clientsession(self.hass).get(f"{self.entry.data[CONF_URL]}/health") as r:
                    body = await r.json() if r.status == 200 else {}
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            if self._attr_is_on:
                _LOGGER.warning("DomGemma Server przestał odpowiadać: %s", type(err).__name__)
            self._attr_is_on = False
            self._attr_extra_state_attributes = {"status": type(err).__name__}
            return
        self._attr_is_on = body.get("ready") is True
        self._attr_extra_state_attributes = {"status": body.get("status"), "format_version": body.get("format_version")}
