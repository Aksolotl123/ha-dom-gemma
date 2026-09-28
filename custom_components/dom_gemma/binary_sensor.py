"""Czujnik łączności z DomGemma Server. Gdy serwer nie odpowiada, agent po cichu przekazuje zdania wbudowanemu
agentowi HA (opcja) - ten czujnik pozwala to zauważyć i ustawić powiadomienie."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, HEALTH_TIMEOUT
from .link import ServerLink, ServerUnavailable

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
        # /health jest bez tokenu; błąd sieci to stan „off”, a nie „niedostępny” - o to właśnie chodzi w czujniku.
        # Przy kilku adresach sprawdzamy kolejno (ostatnio działający najpierw) - wspólny ServerLink z agentem.
        link: ServerLink = self.entry.runtime_data
        try:
            status, body = await link.request("GET", "/health", HEALTH_TIMEOUT, auth=False)
        except ServerUnavailable as err:
            if self._attr_is_on:
                _LOGGER.warning("DomGemma Server przestał odpowiadać: %s", err)
            self._attr_is_on = False
            self._attr_extra_state_attributes = {"status": "brak odpowiedzi", "adresy": link.urls}
            return
        body = body if isinstance(body, dict) else {}
        self._attr_is_on = status == 200 and body.get("ready") is True
        self._attr_extra_state_attributes = {"status": body.get("status"), "format_version": body.get("format_version"),
                                             "adres": link.good}
