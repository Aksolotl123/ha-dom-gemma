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
    # nazwy z serwera: całe urządzenie (L1+L2) ma pierwszeństwo, biernik w akcjach, mianownik przy błędach
    devs = logic.names_from_server({
        "swiatlo_kuchni": {"ids": ["switch.kuchnia_l1", "switch.kuchnia_l2"], "name": "światło w kuchni", "acc": "światło w kuchni"},
        "kuchnia_2": {"ids": ["switch.kuchnia_l1"], "name": "kuchnia 2", "acc": "kuchnię 2"},
        "lampka": {"ids": ["light.lampka"], "name": "lampka przy biurku", "acc": "lampkę przy biurku"},
    })
    assert logic.action_sentence(off, INFO, devs) == "Wyłączam światło w kuchni"
    part = {"action": "call_service", "service": "switch.turn_on", "entity_id": ["switch.kuchnia_l1"]}
    assert logic.action_sentence(part, INFO, devs) == "Włączam kuchnię 2"
    assert logic.action_sentence(dim, INFO, devs) == "Ustawiam lampkę przy biurku na 30%"
    assert logic.entity_names(devs)["switch.kuchnia_l1"].nom == "kuchnia 2"
    assert logic.names_from_server({"x": "zły wpis"}) == []
    assert logic.cap("temperatura w salonie: 21 °C") == "Temperatura w salonie: 21 °C"
    assert logic.cap("") == ""
    # niedostępny czujnik z jednostką: bez „unavailable °C” (znalezione w teście na żywym HA)
    down = {"sensor.x": logic.EntityInfo("temperatura w salonie", "unavailable", unit="°C")}
    assert logic.state_sentence("sensor.x", down) == "temperatura w salonie: niedostępne"
    unlock = {"action": "call_service", "service": "lock.unlock", "entity_id": ["lock.zamek"]}
    assert logic.confirmation_question([unlock], INFO) == "Czy na pewno otworzyć Drzwi wejściowe? Powiedz tak albo nie."
    script = {"action": "call_service", "service": "script.turn_on", "entity_id": ["script.zamknij_i_zgas_wszystko"]}
    assert logic.confirmation_question([script], INFO).startswith("Czy na pewno uruchomić Zamknij i zgaś wszystko")


# ----------------------------------------------------------------------------- dopytanie
@pytest.mark.parametrize("text,direction", [
    ("Zgaś światło w salonie.", "turn_off"), ("wyłącz światło w salonie", "turn_off"), ("pogaś w salonie", "turn_off"),
    ("Włącz światło w salonie.", "turn_on"), ("zapal światło", "turn_on"),
    ("Światło w salonie", None), ("", None), ("włącz albo wyłącz", None),
])
def test_power_direction(text, direction):
    assert logic.power_direction(text) == direction


def test_followup_text_adds_place_from_question():
    # sprawdzone na serwerze: „Nad stołem w salonie.” trafia, „Zgaś światło w salonie. Nad stołem.” myli urządzenie
    assert logic.followup_text("Które światło w salonie?", "Nad stołem.") == "Nad stołem w salonie."
    assert logic.followup_text("Które światło w salonie?", "lampkę w salonie") == "lampkę w salonie."
    assert logic.followup_text("Które światło w sypialni?", " nocną! ") == "nocną w sypialni."
    assert logic.followup_text("Które urządzenie i w którym pomieszczeniu?", "Światło w kuchni") == "Światło w kuchni."
    assert logic.is_new_command("Zgaś światło w kuchni") and logic.is_new_command("jaka jest temperatura")
    assert not logic.is_new_command("Nad stołem.") and not logic.is_new_command("") and not logic.is_new_command("Wszystkie")


def test_with_direction_only_for_light_power():
    lights = {"switch.stol", "switch.kanapa", "light.lampka"}
    on = [{"action": "call_service", "service": "switch.turn_on", "entity_id": ["switch.stol"]}]
    assert logic.with_direction(on, "turn_off", lights) == [
        {"action": "call_service", "service": "switch.turn_off", "entity_id": ["switch.stol"]}]
    assert on[0]["service"] == "switch.turn_on"   # bez zmiany wejścia
    plyta = [{"action": "call_service", "service": "switch.turn_off", "entity_id": ["switch.plyta"]}]
    assert logic.with_direction(plyta, "turn_on", lights) is None     # nie-światło: bez obejścia potwierdzenia
    dim = [{"action": "call_service", "service": "light.turn_on", "entity_id": ["light.lampka"], "data": {"brightness_pct": 5}}]
    assert logic.with_direction(dim, "turn_off", lights) is None
    assert logic.with_direction([{"action": "clarify", "question": "?"}], "turn_on", lights) is None
    assert logic.actions_direction(on + [{**on[0], "entity_id": ["switch.kanapa"]}]) == "turn_on"
    assert logic.actions_direction(on + plyta) is None
    assert logic.actions_direction(dim) is None and logic.actions_direction([]) is None


# ----------------------------------------------------------------------------- żarówki za przekaźnikami
def test_bulbs_for_and_turn_on_decision():
    devs = logic.names_from_server({
        "swiatlo_kuchni": {"ids": ["switch.k1", "switch.k2"], "name": "światło w kuchni", "kind": "light",
                           "bulbs": ["light.b1", "light.b2", "light.b3", "light.b4"]},
        "kuchnia_1": {"ids": ["switch.k2"], "name": "kuchnia 1", "kind": "light", "bulbs": ["light.b1", "light.b2"]},
        "poleczki": {"ids": ["switch.p1", "switch.p2"], "name": "półeczki", "kind": "light", "bulbs": ["light.p"]},
        "zly": {"ids": ["switch.z"], "name": "zły", "bulbs": ["switch.cos", 7, "light.ok"]},
        "zmywarka": {"ids": ["switch.zm"], "name": "zmywarka", "kind": "plug"},
    })
    assert logic.bulbs_for(["switch.k2"], devs) == {"light.b1", "light.b2"}          # część: tylko jej żarówki
    assert logic.bulbs_for(["switch.k1", "switch.k2"], devs) == {"light.b1", "light.b2", "light.b3", "light.b4"}
    assert logic.bulbs_for(["switch.p1"], devs) == {"light.p"}                     # kanał bez własnego wpisu
    assert logic.bulbs_for(["switch.z"], devs) == {"light.ok"}                     # tylko light.*
    assert logic.bulbs_for(["switch.zm"], devs) == set() and logic.bulbs_for([], devs) == set()
    assert logic.bulbs_to_turn_on({"light.a": "unavailable", "light.b": "on"}) == ([], False)   # czekamy
    assert logic.bulbs_to_turn_on({"light.a": "off", "light.b": "unknown"}) == (["light.a"], False)
    assert logic.bulbs_to_turn_on({"light.a": "on", "light.x": None}) == ([], True)            # brak encji = pomiń
    assert logic.bulbs_to_turn_on({}) == ([], True)
