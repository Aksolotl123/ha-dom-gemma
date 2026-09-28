"""Agent rozmowy Assist: zdanie -> DomGemma Server (LAN) -> akcje -> walidacja w HA -> wykonanie/pytanie."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import logic
from .const import CONF_FALLBACK, CONF_REQUIRE_EXPOSED, CONF_TOKEN, CONF_URL, CONFIRM_TTL, DEFAULT_TIMEOUT, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([DomGemmaAgent(entry)])


class ServerUnavailable(Exception):
    """Serwer nie odpowiedział - przekazujemy zdanie do wbudowanego agenta HA."""


class DomGemmaAgent(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = False

    def __init__(self, entry: ConfigEntry) -> None:
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": "Dom Gemma",
                                  "manufacturer": "lokalny model"}
        # conversation_id -> (czas, akcje czekające na "tak")
        self._pending: dict[str, tuple[float, list[dict]]] = {}

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return ["pl"]

    # ------------------------------------------------------------------ obsługa zdania
    async def _async_handle_message(self, user_input: conversation.ConversationInput,
                                    chat_log: conversation.ChatLog) -> conversation.ConversationResult:
        conv_id = chat_log.conversation_id
        response = intent.IntentResponse(language=user_input.language)

        pending = self._pending.pop(conv_id, None)
        if pending and time.monotonic() - pending[0] < CONFIRM_TTL:
            kind = logic.answer_kind(user_input.text)
            if kind == "yes":
                speech = await self._execute(pending[1], user_input)
                return self._reply(user_input, chat_log, response, speech)
            if kind == "no":
                return self._reply(user_input, chat_log, response, "Dobrze, anulowałem.")
            # inne zdanie = nowe polecenie; oczekująca akcja przepada

        try:
            result = await self._ask_server(user_input.text)
        except ServerUnavailable as err:
            _LOGGER.warning("DomGemma Server niedostępny (%s)", err)
            if self.entry.options.get(CONF_FALLBACK, True):
                return await conversation.async_converse(
                    self.hass, user_input.text, conv_id, user_input.context, language=user_input.language,
                    agent_id=conversation.HOME_ASSISTANT_AGENT, device_id=user_input.device_id,
                    satellite_id=user_input.satellite_id)
            response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, "Serwer modelu jest niedostępny.")
            return self._finish(user_input, chat_log, response, "Serwer modelu jest niedostępny.")

        actions = result.get("actions") or []
        errors = [] if result.get("valid") else list(result.get("errors") or ["odpowiedź niepoprawna"])
        errors += logic.check_actions(actions, set(self.hass.states.async_entity_ids()), self._exposed(actions))
        if errors:
            _LOGGER.info("Odrzucone %r -> %r: %s", user_input.text, result.get("commands"), errors)
            msg = "Nie zrozumiałem, co mam zrobić. Spróbuj powiedzieć to inaczej."
            response.async_set_error(intent.IntentResponseErrorCode.NO_INTENT_MATCH, msg)
            return self._finish(user_input, chat_log, response, msg)

        first = actions[0]
        if first["action"] == "clarify":
            return self._reply(user_input, chat_log, response, first["question"], keep_listening=True)
        if first["action"] == "unsupported":
            return self._reply(user_input, chat_log, response, first.get("reply") or "Nie umiem tego zrobić.")

        if result.get("needs_confirmation"):
            self._pending[conv_id] = (time.monotonic(), actions)
            question = logic.confirmation_question(actions, self._info(actions))
            return self._reply(user_input, chat_log, response, question, keep_listening=True)

        speech = await self._execute(actions, user_input)
        return self._reply(user_input, chat_log, response, speech)

    # ------------------------------------------------------------------ pomocnicze
    async def _ask_server(self, text: str) -> dict:
        url = self.entry.data[CONF_URL]
        headers = {"Authorization": f"Bearer {self.entry.data[CONF_TOKEN]}"}
        try:
            async with asyncio.timeout(DEFAULT_TIMEOUT):
                async with async_get_clientsession(self.hass).post(
                        f"{url}/v1/command", json={"text": text}, headers=headers) as r:
                    if r.status != 200:
                        raise ServerUnavailable(f"HTTP {r.status}")
                    return await r.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ServerUnavailable(type(err).__name__) from err

    def _exposed(self, actions: list[dict]) -> set[str] | None:
        if not self.entry.options.get(CONF_REQUIRE_EXPOSED, False):
            return None
        ids = {e for a in actions for e in a.get("entity_id") or []}
        return {e for e in ids if async_should_expose(self.hass, conversation.DOMAIN, e)}

    def _info(self, actions: list[dict]) -> dict[str, logic.EntityInfo]:
        info = {}
        for e in {e for a in actions for e in a.get("entity_id") or []}:
            if (s := self.hass.states.get(e)) is not None:
                info[e] = logic.EntityInfo(name=s.name, state=s.state, unit=s.attributes.get("unit_of_measurement"),
                                           device_class=s.attributes.get("device_class"))
        return info

    async def _execute(self, actions: list[dict], user_input: conversation.ConversationInput) -> str:
        sentences = []
        for a in actions:
            if a["action"] == "get_state":
                info = self._info([a])
                sentences.append("; ".join(logic.state_sentence(e, info) for e in a["entity_id"]))
                continue
            domain, _, service = a["service"].partition(".")
            data = {"entity_id": a["entity_id"], **(a.get("data") or {})}
            try:
                await self.hass.services.async_call(domain, service, data, blocking=True, context=user_input.context)
            except Unauthorized:
                sentences.append(f"Nie masz uprawnień do {logic._names(a['entity_id'], self._info([a]))}")
                continue
            except HomeAssistantError as err:
                _LOGGER.warning("Błąd %s: %s", a["service"], err)
                sentences.append(f"Nie udało się: {logic._names(a['entity_id'], self._info([a]))}")
                continue
            sentences.append(logic.action_sentence(a, self._info([a])))
        return ". ".join(sentences) + "."

    def _reply(self, user_input, chat_log, response, speech: str, keep_listening: bool = False):
        response.async_set_speech(speech)
        return self._finish(user_input, chat_log, response, speech, keep_listening)

    @staticmethod
    def _finish(user_input, chat_log, response, speech: str, keep_listening: bool = False):
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=user_input.agent_id, content=speech))
        return conversation.ConversationResult(response=response, conversation_id=chat_log.conversation_id,
                                               continue_conversation=keep_listening)
