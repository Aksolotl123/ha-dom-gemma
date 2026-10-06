"""Agent rozmowy Assist: zdanie -> DomGemma Server (LAN) -> akcje -> walidacja w HA -> wykonanie/pytanie."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import logic
from .const import (BULB_SETTLE, BULB_STEP, BULB_WAIT, CLARIFY_TTL, CONF_FALLBACK, CONF_REQUIRE_EXPOSED,
                    CONFIRM_TTL, DEFAULT_TIMEOUT, DEVICES_TTL, DOMAIN)
from .link import ServerLink, ServerUnavailable

_LOGGER = logging.getLogger(__name__)
_WARNED: set[str] = set()   # encje z relay/bulbs, przed którymi już ostrzegliśmy (raz na uruchomienie HA)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([DomGemmaAgent(entry)])


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
        # conversation_id -> (czas, pierwsze polecenie, pytanie modelu) - czekamy na doprecyzowanie
        self._clarify: dict[str, tuple[float, str, str]] = {}
        # urządzenia z serwera (polskie nazwy, rodzaj, żarówki za przekaźnikami); None = jeszcze nie pobrane
        self._devices: list[logic.DeviceName] | None = None
        self._devices_at = 0.0

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

        # odpowiedź na „Które światło w salonie?”: urządzenie z odpowiedzi + miejsca, kierunek z pierwszego polecenia
        clarify = self._clarify.pop(conv_id, None)
        if clarify and (time.monotonic() - clarify[0] >= CLARIFY_TTL or logic.is_new_command(user_input.text)):
            clarify = None
        if clarify and logic.answer_kind(user_input.text) == "no":
            return self._reply(user_input, chat_log, response, "Dobrze, nic nie robię.")
        text = logic.followup_text(clarify[2], user_input.text) if clarify else user_input.text
        direction = logic.power_direction(clarify[1]) if clarify else None

        try:
            result = await self._ask_server(text)
            if clarify and direction is None:
                # polecenie bez „włącz/zgaś” - kierunek wskaże model na sklejonym zdaniu
                both = await self._ask_server(f"{clarify[1].strip().rstrip('.!?')}. {user_input.text}")
                direction = logic.actions_direction(both.get("actions") or []) if both.get("valid") else None
            if self._devices is None or time.monotonic() - self._devices_at > DEVICES_TTL:
                self._devices = await self._fetch_names() or self._devices
                self._devices_at = time.monotonic()
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
        if clarify and direction and result.get("valid"):
            lights = {e for d in self._devices or [] if d.kind == "light" for e in d.ids}
            actions = logic.with_direction(actions, direction, lights) or actions
        errors = [] if result.get("valid") else list(result.get("errors") or ["odpowiedź niepoprawna"])
        # przed potwierdzeniem: „dim płyta_indukcyjna” ma paść tu, a nie po „tak”
        errors += logic.check_actions(actions, set(self.hass.states.async_entity_ids()), self._exposed(actions),
                                      logic.dimmable_relays(self._devices), logic.color_entities(self._devices))
        if errors:
            # treść wypowiedzi i komendy serwera tylko w DEBUG (log INFO trafia do zgłoszeń/diagnostyki)
            _LOGGER.info("Odrzucono polecenie z Assist (%d błąd/błędy walidacji)", len(errors))
            _LOGGER.debug("Odrzucone %r -> %r: %s", user_input.text, result.get("commands"), errors)
            msg = "Nie zrozumiałem, co mam zrobić. Spróbuj powiedzieć to inaczej."
            response.async_set_error(intent.IntentResponseErrorCode.NO_INTENT_MATCH, msg)
            return self._finish(user_input, chat_log, response, msg)

        first = actions[0]
        if first["action"] == "clarify":
            # przy ponownym dopytaniu zostaje pierwsze polecenie (w nim jest czasownik)
            self._clarify[conv_id] = (time.monotonic(), clarify[1] if clarify else user_input.text, first["question"])
            return self._reply(user_input, chat_log, response, first["question"], keep_listening=True)
        if first["action"] == "unsupported":
            return self._reply(user_input, chat_log, response, first.get("reply") or "Nie umiem tego zrobić.")

        # o potwierdzeniu akcji wrażliwych (zamek, skrypt, brama...) decyduje HA, nie tylko serwer
        if result.get("needs_confirmation") or logic.needs_ha_confirmation(actions, self._device_classes(actions)):
            self._pending[conv_id] = (time.monotonic(), actions)
            question = logic.confirmation_question(actions, self._info(actions), self._devices)
            return self._reply(user_input, chat_log, response, question, keep_listening=True)

        speech = await self._execute(actions, user_input)
        return self._reply(user_input, chat_log, response, speech)

    # ------------------------------------------------------------------ pomocnicze
    @property
    def _link(self) -> ServerLink:
        return self.entry.runtime_data

    async def _ask_server(self, text: str) -> dict:
        status, data = await self._link.request("POST", "/v1/command", DEFAULT_TIMEOUT, body={"text": text})
        if status != 200 or not isinstance(data, dict):
            raise ServerUnavailable(f"HTTP {status}")
        return data

    async def _fetch_names(self) -> list[logic.DeviceName] | None:
        """Nazwy z /v1/devices. Błąd nie blokuje polecenia - wtedy zostają nazwy encji z HA (spróbujemy ponownie)."""
        try:
            status, data = await self._link.request("GET", "/v1/devices", 5)
            if status == 200 and isinstance(data, dict):
                # relay/bulbs z pakietu agent steruje sam (bez modelu) - tylko encje, które HA zna (0.1.10)
                return logic.restrict_devices(logic.names_from_server(data), self._derived_allowed)
        except ServerUnavailable as err:
            _LOGGER.debug("Nie pobrano nazw urządzeń: %s", err)
        return None

    def _derived_allowed(self, entity_id: str) -> bool:
        """Czy encja z pól relay/bulbs pakietu serwera może być sterowana bez udziału modelu: musi istnieć w HA
        i (przy require_exposed) być wystawiona do Assist - te same warunki, które check_actions stawia encjom
        z akcji modelu. Sprawdzane przy pobraniu pakietu (co DEVICES_TTL), nie przy każdym poleceniu."""
        if self.hass.states.get(entity_id) is None:
            reason = "nie ma jej w HA"
        elif self.entry.options.get(CONF_REQUIRE_EXPOSED, False) and not async_should_expose(
                self.hass, conversation.DOMAIN, entity_id):
            reason = "nie jest wystawiona do Assist"
        else:
            return True
        if entity_id not in _WARNED:  # pakiet odświeżany co 10 min - ostrzegamy raz na uruchomienie HA
            _WARNED.add(entity_id)
            _LOGGER.warning("Pakiet serwera wskazuje encję %s, która %s - pomijam ją w relay/bulbs", entity_id, reason)
        return False

    def _device_classes(self, actions: list[dict]) -> dict[str, str | None]:
        return {e: s.attributes.get("device_class") if (s := self.hass.states.get(e)) else None
                for a in actions for e in a.get("entity_id") or []}

    def _exposed(self, actions: list[dict]) -> set[str] | None:
        # brak klucza = wpis sprzed 0.1.9 (wtedy domyślnie False); nowe wpisy dostają True w config_flow
        if not self.entry.options.get(CONF_REQUIRE_EXPOSED, False):
            return None
        ids = {e for a in actions for e in a.get("entity_id") or []}
        return {e for e in ids if async_should_expose(self.hass, conversation.DOMAIN, e)}

    def _info(self, actions: list[dict]) -> dict[str, logic.EntityInfo]:
        """Stan z HA + polska nazwa z serwera (gdy jest), inaczej nazwa encji z HA."""
        names = logic.entity_names(self._devices or [])
        info = {}
        for e in {e for a in actions for e in a.get("entity_id") or []}:
            if (s := self.hass.states.get(e)) is not None:
                d = names.get(e)
                info[e] = logic.EntityInfo(name=d.nom if d else s.name, state=s.state,
                                           unit=s.attributes.get("unit_of_measurement"),
                                           device_class=s.attributes.get("device_class"), acc=d.acc if d else None)
        return info

    async def _execute(self, actions: list[dict], user_input: conversation.ConversationInput) -> str:
        sentences = []
        powered: list[str] = []  # włączone przełączniki - ich żarówki dopilnujemy po odpowiedzi
        index = logic.bulb_index(self._devices)
        for a in actions:
            if a["action"] == "get_state":
                info = self._info([a])
                sentences.append("; ".join(logic.state_sentence(e, info) for e in a["entity_id"]))
                continue
            # v6: pojedyncze żarówki - przez ich przełącznik (logic.bulb_plan); reszta akcji zwykłą drogą
            if bulb_ids := [e for e in a["entity_id"] if e in index]:
                part = {**a, "entity_id": bulb_ids}
                ok = await self._execute_bulbs(part, index, user_input.context)
                if ok is not None:
                    sentences.append(logic.action_sentence(part, self._info([part]), self._devices) if ok else
                                     f"Nie udało się: {logic._names(bulb_ids, self._info([part]), self._devices, 'nom')}")
                    rest = [e for e in a["entity_id"] if e not in index]
                    if not rest:
                        continue
                    a = {**a, "entity_id": rest}
            domain, _, service = a["service"].partition(".")
            data = {"entity_id": a["entity_id"], **(a.get("data") or {})}
            via_bulbs = a["service"] == "switch.turn_on" and bool(a.get("data"))
            if via_bulbs:
                # światło za przełącznikiem: przełącznik bez danych, jasność/kolor ustawiamy na żarówkach po odpowiedzi
                was_off = any((s := self.hass.states.get(e)) is None or s.state != "on" for e in a["entity_id"])
                data = {"entity_id": a["entity_id"]}
            try:
                await self.hass.services.async_call(domain, service, data, blocking=True, context=user_input.context)
            except Unauthorized:
                sentences.append(f"Nie masz uprawnień do sterowania: {logic._names(a['entity_id'], self._info([a]), self._devices, 'nom')}")
                continue
            except HomeAssistantError as err:
                _LOGGER.warning("Błąd %s: %s", a["service"], err)
                sentences.append(f"Nie udało się: {logic._names(a['entity_id'], self._info([a]), self._devices, 'nom')}")
                continue
            if via_bulbs:
                if bulbs := logic.bulbs_for(a["entity_id"], self._devices or []):
                    plan = logic.BulbPlan([], [], [], sorted(bulbs), [], was_off)
                    self.entry.async_create_background_task(
                        self.hass, self._apply_bulbs(plan, a["data"], user_input.context), "dom_gemma_dim")
            elif a["service"] == "switch.turn_on":
                powered += a["entity_id"]
            sentences.append(logic.action_sentence(a, self._info([a]), self._devices))
        if bulbs := logic.bulbs_for(powered, self._devices or []):
            # żarówka zgłasza się kilka sekund po włączeniu zasilania - nie wstrzymujemy odpowiedzi głosowej
            self.entry.async_create_background_task(
                self.hass, self._light_bulbs(bulbs, user_input.context), "dom_gemma_bulbs")
        return ". ".join(logic.cap(s) for s in sentences) + "."

    async def _execute_bulbs(self, a: dict, index: dict[str, logic.DeviceName], context) -> bool | None:
        """Pojedyncze żarówki (v6): przełączniki od razu, zapalenie wybranych i zgaszenie sąsiednich w tle.
        None = to nie wł/wył samych żarówek (akcja idzie zwykłą drogą, light.* wprost); False = błąd HA."""
        watched = set(index) | {d.relay for d in index.values()}
        states = {e: s.state if (s := self.hass.states.get(e)) else None for e in watched}
        data = a.get("data") or {}
        plan = logic.bulb_plan(a["service"], a["entity_id"], data, index, states)
        if plan is None:
            return None
        _LOGGER.debug("Żarówki %s %s: %s", a["service"], a["entity_id"], plan)
        try:
            if plan.relays_on:
                await self.hass.services.async_call("switch", "turn_on", {"entity_id": plan.relays_on},
                                                    blocking=True, context=context)
            if plan.lights_off:
                await self.hass.services.async_call("light", "turn_off", {"entity_id": plan.lights_off},
                                                    blocking=True, context=context)
            if plan.relays_off:
                await self.hass.services.async_call("switch", "turn_off", {"entity_id": plan.relays_off},
                                                    blocking=True, context=context)
        except HomeAssistantError as err:
            _LOGGER.warning("Błąd sterowania żarówkami %s: %s", a["entity_id"], err)
            return False
        if plan.targets or plan.siblings_off:
            self.entry.async_create_background_task(self.hass, self._apply_bulbs(plan, data, context), "dom_gemma_bulb")
        return True

    async def _light_bulbs(self, bulbs: set[str], context) -> None:
        """Czeka, aż żarówki za włączonym przełącznikiem się zgłoszą, i włącza te, które wstały zgaszone."""
        tried: dict[str, int] = {}
        deadline = time.monotonic() + BULB_WAIT
        while time.monotonic() < deadline:
            states = {b: s.state if (s := self.hass.states.get(b)) else None for b in bulbs}
            off, done = logic.bulbs_to_turn_on(states)
            if done:
                return
            # ponawiamy co krok, ale najwyżej 3 razy na żarówkę (świeżo zasilona może zgubić pierwsze polecenie)
            if todo := [b for b in off if tried.get(b, 0) < 3]:
                for b in todo:
                    tried[b] = tried.get(b, 0) + 1
                try:
                    await self.hass.services.async_call("light", "turn_on", {"entity_id": todo}, blocking=True,
                                                        context=context)
                except HomeAssistantError as err:
                    _LOGGER.debug("Żarówki %s jeszcze nie odpowiadają: %s", todo, err)
            await asyncio.sleep(BULB_STEP)
        _LOGGER.warning("Żarówki nie włączyły się w %s s: %s", BULB_WAIT,
                        {b: s.state if (s := self.hass.states.get(b)) else None for b in sorted(bulbs)})

    async def _apply_bulbs(self, plan: logic.BulbPlan, data: dict, context) -> None:
        """Żarówki za przełącznikiem: plan.targets zapalić z jasnością/kolorem (data), plan.siblings_off zgasić -
        aż nowy stan to potwierdzi. Świeżo zasilona żarówka gubi polecenia przez pierwsze sekundy, a jej stan w HA
        bywa nieaktualny - stąd przerwa po włączeniu zasilania i potwierdzenie stanem nowszym niż polecenie."""
        if plan.relay_was_off:
            await asyncio.sleep(BULB_SETTLE)
        sent_on: dict[str, tuple[float, int]] = {}
        sent_off: dict[str, tuple[float, int]] = {}
        deadline = time.monotonic() + BULB_WAIT
        while time.monotonic() < deadline:
            on_info = {b: (s.state, s.attributes.get("brightness"), s.last_updated.timestamp())
                       if (s := self.hass.states.get(b)) else (None, None, 0.0) for b in plan.targets}
            off_info = {b: (s.state, s.last_updated.timestamp()) if (s := self.hass.states.get(b)) else (None, 0.0)
                        for b in plan.siblings_off}
            now = time.time()
            todo_on, done_on = logic.bulbs_dim_plan(on_info, sent_on, data, now)
            todo_off, done_off = logic.bulbs_off_plan(off_info, sent_off, now, force=plan.relay_was_off)
            if done_on and done_off:
                return
            for todo, sent, service, extra in ((todo_on, sent_on, "turn_on", data), (todo_off, sent_off, "turn_off", {})):
                if not todo:
                    continue
                for b in todo:
                    sent[b] = (now, sent.get(b, (0.0, 0))[1] + 1)
                try:
                    await self.hass.services.async_call("light", service, {"entity_id": todo, **extra},
                                                        blocking=True, context=context)
                except HomeAssistantError as err:
                    _LOGGER.debug("Żarówki %s jeszcze nie odpowiadają: %s", todo, err)
            await asyncio.sleep(BULB_STEP)
        _LOGGER.warning("Żarówki nie potwierdziły stanu w %s s (%s): %s", BULB_WAIT, data,
                        {b: (s.state, s.attributes.get("brightness")) if (s := self.hass.states.get(b)) else None
                         for b in plan.targets + plan.siblings_off})

    def _reply(self, user_input, chat_log, response, speech: str, keep_listening: bool = False):
        speech = logic.cap(speech)
        response.async_set_speech(speech)
        return self._finish(user_input, chat_log, response, speech, keep_listening)

    @staticmethod
    def _finish(user_input, chat_log, response, speech: str, keep_listening: bool = False):
        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(agent_id=user_input.agent_id, content=speech))
        return conversation.ConversationResult(response=response, conversation_id=chat_log.conversation_id,
                                               continue_conversation=keep_listening)
