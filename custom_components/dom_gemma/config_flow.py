"""Konfiguracja: adres(y) serwera i token. Bez wartości domyślnych - nic z domu nie trafia do kodu.
Kilka adresów po przecinku (np. telefon w dwóch sieciach Wi-Fi) - działa ten, który odpowie."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from . import logic
from .const import CONF_FALLBACK, CONF_REQUIRE_EXPOSED, CONF_TOKEN, CONF_URL, DOMAIN
from .link import ServerLink, ServerUnavailable

USER_SCHEMA = vol.Schema({
    vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
    vol.Required(CONF_TOKEN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
})


class DomGemmaConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = ", ".join(logic.parse_urls(user_input[CONF_URL]))
            self._async_abort_entries_match({CONF_URL: url})
            errors = await _check(self.hass, url, user_input[CONF_TOKEN])
            if not errors:
                # od 0.1.9 nowe wpisy domyślnie wykonują akcje tylko na encjach wystawionych do Assist;
                # istniejące wpisy (bez klucza w opcjach) zostają przy dawnym False
                return self.async_create_entry(title="Dom Gemma", data={CONF_URL: url, CONF_TOKEN: user_input[CONF_TOKEN]},
                                               options={CONF_REQUIRE_EXPOSED: True, CONF_FALLBACK: True})
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Zmiana adresu (np. telefon dostał nowe IP) bez usuwania integracji; pusty token = bez zmiany."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = ", ".join(logic.parse_urls(user_input[CONF_URL]))
            token = user_input.get(CONF_TOKEN) or entry.data[CONF_TOKEN]
            errors = await _check(self.hass, url, token)
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_URL: url, CONF_TOKEN: token})
        return self.async_show_form(step_id="reconfigure", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_URL, default=entry.data[CONF_URL]): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Optional(CONF_TOKEN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        }))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DomGemmaOptionsFlow()


class DomGemmaOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        o = self.config_entry.options
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Optional(CONF_REQUIRE_EXPOSED, default=o.get(CONF_REQUIRE_EXPOSED, False)): bool,
            vol.Optional(CONF_FALLBACK, default=o.get(CONF_FALLBACK, True)): bool,
        }))


async def _check(hass, urls: str, token: str) -> dict[str, str]:
    """Wystarczy, że odpowie jeden z adresów (/health); token sprawdzany na nim (/v1/devices)."""
    if not logic.parse_urls(urls):
        return {"base": "cannot_connect"}
    link = ServerLink(hass, urls, token)
    try:
        status, _ = await link.request("GET", "/health", 10, auth=False)
        if status != 200:
            return {"base": "cannot_connect"}
        status, _ = await link.request("GET", "/v1/devices", 10)
    except ServerUnavailable:
        return {"base": "cannot_connect"}
    if status == 401:
        return {"base": "invalid_auth"}
    return {} if status == 200 else {"base": "cannot_connect"}
