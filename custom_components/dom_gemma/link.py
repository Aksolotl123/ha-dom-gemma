"""Połączenie z DomGemma Server pod jednym z kilku adresów (np. telefon w dwóch sieciach Wi-Fi / dwa routery).
Pytamy najpierw adres, który ostatnio odpowiedział; gdy nie odpowiada - kolejny. Wspólne dla agenta i czujnika."""
from __future__ import annotations

import asyncio
import logging

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import logic
from .const import CONNECT_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class ServerUnavailable(Exception):
    """Żaden adres serwera nie odpowiedział."""


class ServerLink:
    def __init__(self, hass: HomeAssistant, urls: str, token: str) -> None:
        self.hass = hass
        self.urls = logic.parse_urls(urls)
        self.token = token
        self.good: str | None = None   # ostatnio działający adres

    def _headers(self, auth: bool) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if auth else {}

    async def request(self, method: str, path: str, timeout: float, auth: bool = True,
                      body: dict | None = None) -> tuple[int, object]:
        """(status, JSON) z pierwszego adresu, który odpowie. Błąd połączenia = następny adres;
        odpowiedź HTTP (także 401/503) = koniec - serwer jest, tylko coś mu nie pasuje."""
        session = async_get_clientsession(self.hass)
        client_timeout = aiohttp.ClientTimeout(total=timeout, sock_connect=CONNECT_TIMEOUT)
        errors = []
        for url in logic.order_urls(self.urls, self.good):
            try:
                async with asyncio.timeout(timeout + 1):
                    async with session.request(method, f"{url}{path}", json=body, headers=self._headers(auth),
                                               timeout=client_timeout) as r:
                        data = await r.json(content_type=None) if r.status == 200 else None
                        if self.good != url:
                            _LOGGER.info("DomGemma Server odpowiada pod %s", url)
                        self.good = url
                        return r.status, data
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                errors.append(f"{url}: {type(err).__name__}")
        raise ServerUnavailable("; ".join(errors) or "brak adresów")
