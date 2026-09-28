"""Konfiguracja: adres serwera i token. Bez wartości domyślnych - nic z domu nie trafia do kodu."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .const import CONF_FALLBACK, CONF_REQUIRE_EXPOSED, CONF_TOKEN, CONF_URL, DOMAIN

USER_SCHEMA = vol.Schema({
    vol.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
    vol.Required(CONF_TOKEN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
})


class DomGemmaConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            self._async_abort_entries_match({CONF_URL: url})
            errors = await _check(self.hass, url, user_input[CONF_TOKEN])
            if not errors:
                return self.async_create_entry(title="Dom Gemma", data={CONF_URL: url, CONF_TOKEN: user_input[CONF_TOKEN]})
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Zmiana adresu (np. telefon dostał nowe IP) bez usuwania integracji; pusty token = bez zmiany."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            token = user_input.get(CONF_TOKEN) or entry.data[CONF_TOKEN]
            errors = await _check(self.hass, url, token)
            if not errors:
                return self.async_update_reload_and_abort(entry, data_updates={CONF_URL: url, CONF_TOKEN: token})
        return self.async_show_form(step_id="reconfigure", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_URL, default=entry.data[CONF_URL]): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
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


async def _check(hass, url: str, token: str) -> dict[str, str]:
    """Sprawdza adres (/health) i token (/v1/devices)."""
    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(10):
            async with session.get(f"{url}/health") as r:
                if r.status != 200:
                    return {"base": "cannot_connect"}
            async with session.get(f"{url}/v1/devices", headers={"Authorization": f"Bearer {token}"}) as r:
                if r.status == 401:
                    return {"base": "invalid_auth"}
                if r.status != 200:
                    return {"base": "cannot_connect"}
    except (aiohttp.ClientError, TimeoutError):
        return {"base": "cannot_connect"}
    return {}
