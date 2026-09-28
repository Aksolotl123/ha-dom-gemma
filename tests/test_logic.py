"""Testy logiki agenta bez Home Assistant: python -m pytest ha-dom-gemma/tests -q"""
import importlib.util
import sys
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "dom_gemma_logic", Path(__file__).resolve().parents[1] / "custom_components/dom_gemma/logic.py")
logic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = logic  # @dataclass szuka modułu w sys.modules
_spec.loader.exec_module(logic)

KNOWN = {"light.lampka", "switch.kuchnia_l1", "switch.kuchnia_l2", "lock.zamek", "sensor.temp", "binary_sensor.drzwi",
         "script.zamknij_i_zgas_wszystko"}
INFO = {
    "light.lampka": logic.EntityInfo("Lampka nocna", "on"),
    "switch.kuchnia_l1": logic.EntityInfo("Światło kuchnia", "off"),
    "switch.kuchnia_l2": logic.EntityInfo("Światło kuchnia", "off"),
    "lock.zamek": logic.EntityInfo("Drzwi wejściowe", "locked"),
    "sensor.temp": logic.EntityInfo("Temperatura w salonie", "21.46", unit="°C", device_class="temperature"),
    "binary_sensor.drzwi": logic.EntityInfo("Drzwi do sypialni", "off", device_class="door"),
    "script.zamknij_i_zgas_wszystko": logic.EntityInfo("Zamknij i zgaś wszystko", "off"),
}


@pytest.mark.parametrize("text,kind", [
    ("tak", "yes"), ("Tak!", "yes"), ("tak, otwórz", "yes"), ("Potwierdzam.", "yes"),
    ("nie", "no"), ("Anuluj", "no"), ("nie, zostaw", "no"),
    ("zgaś światło w kuchni", "other"), ("takie tam", "other"), ("", "other"),
])
def test_answer_kind(text, kind):
    assert logic.answer_kind(text) == kind


def test_check_actions_accepts_valid_and_rejects_unsafe():
    ok = [{"action": "call_service", "service": "switch.turn_off", "entity_id": ["switch.kuchnia_l1", "switch.kuchnia_l2"]}]
    assert logic.check_actions(ok, KNOWN, None) == []
    assert logic.check_actions([{"action": "get_state", "entity_id": ["sensor.temp"]}], KNOWN, None) == []
    # usługa spoza listy, encja z innej domeny, brak encji w HA, dane spoza listy
    assert logic.check_actions([{"action": "call_service", "service": "switch.delete", "entity_id": ["switch.kuchnia_l1"]}], KNOWN, None)
    assert logic.check_actions([{"action": "call_service", "service": "light.turn_on", "entity_id": ["switch.kuchnia_l1"]}], KNOWN, None)
    assert logic.check_actions([{"action": "call_service", "service": "light.turn_on", "entity_id": ["light.nie_ma"]}], KNOWN, None)
    assert logic.check_actions([{"action": "call_service", "service": "light.turn_on", "entity_id": ["light.lampka"],
                                 "data": {"rgb_color": [1, 2, 3]}}], KNOWN, None)
    assert logic.check_actions([], KNOWN, None)
    # clarify z inną akcją
    assert logic.check_actions([{"action": "clarify", "question": "?"}] + ok, KNOWN, None)


def test_check_actions_exposure():
    a = [{"action": "call_service", "service": "light.turn_on", "entity_id": ["light.lampka"]}]
    assert logic.check_actions(a, KNOWN, exposed={"light.lampka"}) == []
    assert logic.check_actions(a, KNOWN, exposed=set())


def test_sentences():
    off = {"action": "call_service", "service": "switch.turn_off", "entity_id": ["switch.kuchnia_l1", "switch.kuchnia_l2"]}
    assert logic.action_sentence(off, INFO) == "Wyłączam Światło kuchnia"          # dwa kanały, jedna nazwa
    dim = {"action": "call_service", "service": "light.turn_on", "entity_id": ["light.lampka"], "data": {"brightness_pct": 30}}
    assert logic.action_sentence(dim, INFO) == "Ustawiam Lampka nocna na 30%"
    step = {"action": "call_service", "service": "light.turn_on", "entity_id": ["light.lampka"], "data": {"brightness_step_pct": -20}}
    assert logic.action_sentence(step, INFO) == "Przyciemniam Lampka nocna"
    assert logic.state_sentence("sensor.temp", INFO) == "Temperatura w salonie: 21,5 °C"
    assert logic.state_sentence("binary_sensor.drzwi", INFO) == "Drzwi do sypialni: zamknięte"
    assert logic.state_sentence("lock.zamek", INFO) == "Drzwi wejściowe: zamknięte"
    unlock = {"action": "call_service", "service": "lock.unlock", "entity_id": ["lock.zamek"]}
    assert logic.confirmation_question([unlock], INFO) == "Czy na pewno otworzyć Drzwi wejściowe? Powiedz tak albo nie."
    script = {"action": "call_service", "service": "script.turn_on", "entity_id": ["script.zamknij_i_zgas_wszystko"]}
    assert logic.confirmation_question([script], INFO).startswith("Czy na pewno uruchomić Zamknij i zgaś wszystko")
