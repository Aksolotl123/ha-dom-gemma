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


# ----------------------------------------------------------------------------- jasność świateł za przełącznikiem
def test_dim_gate_only_for_relays_with_bulbs():
    devs = logic.names_from_server({
        "swiatlo": {"ids": ["switch.rel"], "name": "światło", "kind": "light", "bulbs": ["light.b"]},
        "gniazdko": {"ids": ["switch.plug"], "name": "gniazdko", "kind": "plug"},
    })
    dimmable = logic.dimmable_relays(devs)
    assert dimmable == {"switch.rel"}
    known = {"switch.rel", "switch.plug"}
    dim = lambda e: [{"action": "call_service", "service": "switch.turn_on", "entity_id": [e], "data": {"brightness_pct": 30}}]
    assert logic.check_actions(dim("switch.rel"), known, None, dimmable) == []
    assert logic.check_actions(dim("switch.plug"), known, None, dimmable)            # gniazdko: odrzucone
    assert logic.check_actions(dim("switch.rel"), known, None)                        # brak listy urządzeń: odrzucone
    assert logic.dimmable_relays(None) == frozenset()
    plain = [{"action": "call_service", "service": "switch.turn_on", "entity_id": ["switch.plug"]}]
    assert logic.check_actions(plain, known, None, dimmable) == []                   # zwykłe włączenie bez zmian
    assert logic.action_sentence(dim("switch.rel")[0], {}, devs) == "Ustawiam światło na 30%"


def test_bulbs_dim_plan():
    want = round(30 * 255 / 100)
    # przed wysłaniem: wysyłamy do dostępnych (także pokazujących stary „off”), czekamy na niedostępne
    todo, done = logic.bulbs_dim_plan({"a": ("off", None, 100.0), "b": ("unavailable", None, 100.0),
                                       "c": (None, None, 0.0)}, {}, {"brightness_pct": 30}, now=200.0)
    assert todo == ["a"] and not done
    # stan sprzed polecenia to nie potwierdzenie; zbyt wcześnie na ponowienie
    todo, done = logic.bulbs_dim_plan({"a": ("on", 255, 150.0)}, {"a": (200.0, 1)}, {"brightness_pct": 30}, now=201.0)
    assert todo == [] and not done
    # nowszy stan z inną jasnością (np. 100% po starcie) -> ponowienie po czasie
    todo, done = logic.bulbs_dim_plan({"a": ("on", 255, 202.0)}, {"a": (200.0, 1)}, {"brightness_pct": 30}, now=204.0)
    assert todo == ["a"] and not done
    # nowszy stan z zadaną jasnością -> koniec
    assert logic.bulbs_dim_plan({"a": ("on", want + 2, 202.0)}, {"a": (200.0, 1)}, {"brightness_pct": 30}, now=204.0) == ([], True)
    # próby wyczerpane -> koniec (bez nieskończonego ponawiania)
    assert logic.bulbs_dim_plan({"a": ("on", 255, 202.0)}, {"a": (200.0, 3)}, {"brightness_pct": 30}, now=210.0) == ([], True)
    # krok ±N: jedna próba (ponowienie zsumowałoby kroki), potwierdza nowszy stan „on”
    assert logic.bulbs_dim_plan({"a": ("on", 100, 199.0)}, {"a": (200.0, 1)}, {"brightness_step_pct": -20}, now=210.0) == ([], True)
    assert logic.bulbs_dim_plan({"a": ("on", 100, 202.0)}, {"a": (200.0, 1)}, {"brightness_step_pct": -20}, now=203.0) == ([], True)
    assert logic.bulbs_dim_plan({}, {}, {"brightness_pct": 30}, now=0.0) == ([], True)
    # kolor / samo zapalenie: ponawiane (idempotentne), potwierdza nowszy stan „on”
    assert logic.bulbs_dim_plan({"a": ("off", None, 202.0)}, {"a": (200.0, 1)}, {"color_name": "red"}, now=204.0) == (["a"], False)
    assert logic.bulbs_dim_plan({"a": ("on", 10, 202.0)}, {"a": (200.0, 1)}, {"color_name": "red"}, now=204.0) == ([], True)
    assert logic.bulbs_dim_plan({"a": ("off", None, 100.0)}, {}, {}, now=200.0) == (["a"], False)


def test_bulbs_off_plan():
    # świeci -> gasimy; niedostępna -> czekamy; zgaszona/brak encji -> załatwiona
    assert logic.bulbs_off_plan({"a": ("on", 1.0), "b": ("unavailable", 1.0), "c": ("off", 1.0), "d": (None, 0.0)},
                                {}, now=10.0, force=False) == (["a"], False)
    # po włączeniu zasilania stary „off” nie wystarcza - gasimy i czekamy na nowszy stan
    assert logic.bulbs_off_plan({"c": ("off", 1.0)}, {}, now=10.0, force=True) == (["c"], False)
    assert logic.bulbs_off_plan({"c": ("off", 1.0)}, {"c": (10.0, 1)}, now=11.0, force=True) == ([], False)
    assert logic.bulbs_off_plan({"c": ("off", 12.0)}, {"c": (10.0, 1)}, now=13.0, force=True) == ([], True)
    # próby wyczerpane -> koniec
    assert logic.bulbs_off_plan({"a": ("on", 12.0)}, {"a": (10.0, 3)}, now=20.0, force=True) == ([], True)
    assert logic.bulbs_off_plan({}, {}, now=0.0, force=True) == ([], True)


# ----------------------------------------------------------------------------- pojedyncze żarówki, kolor (v6)
SYP = "switch.syp"
K1, K2 = "switch.k_l1", "switch.k_l2"
BULB_DEVS = logic.names_from_server({
    "swiatlo_sypialni": {"ids": [SYP], "name": "światło w sypialni", "kind": "light", "area": "sypialnia",
                         "bulbs": ["light.s1", "light.s2", "light.s3"], "color": True},
    "zarowka_1_sypialni": {"ids": ["light.s1"], "name": "żarówka 1 w sypialni", "acc": "żarówkę 1 w sypialni",
                           "kind": "light", "area": "sypialnia", "relay": SYP, "bulb": 1, "color": True},
    "zarowka_2_sypialni": {"ids": ["light.s2"], "name": "żarówka 2 w sypialni", "kind": "light", "area": "sypialnia",
                           "relay": SYP, "bulb": 2, "color": True},
    "zarowka_3_sypialni": {"ids": ["light.s3"], "name": "żarówka 3 w sypialni", "kind": "light", "area": "sypialnia",
                           "relay": SYP, "bulb": 3, "color": True},
    "zarowka_1_kuchni": {"ids": ["light.k1"], "name": "żarówka 1 w kuchni", "kind": "light", "area": "kuchnia",
                         "relay": K2, "bulb": 1},
    "zarowka_2_kuchni": {"ids": ["light.k2"], "name": "żarówka 2 w kuchni", "kind": "light", "area": "kuchnia",
                         "relay": K2, "bulb": 2},
    "zarowka_3_kuchni": {"ids": ["light.k3"], "name": "żarówka 3 w kuchni", "kind": "light", "area": "kuchnia",
                         "relay": K1, "bulb": 3},
    "zly": {"ids": ["light.x", "light.y"], "name": "zły", "kind": "light", "relay": SYP},       # 2 encje - nie żarówka
    "zly2": {"ids": ["light.z"], "name": "zły 2", "kind": "light", "relay": "light.syp"},      # relay nie switch.*
})
IDX = logic.bulb_index(BULB_DEVS)


def test_bulb_index_accepts_only_single_light_with_switch_relay():
    assert set(IDX) == {"light.s1", "light.s2", "light.s3", "light.k1", "light.k2", "light.k3"}
    assert IDX["light.s1"].relay == SYP and IDX["light.s1"].area == "sypialnia" and IDX["light.s1"].color
    assert not IDX["light.k1"].color


def test_bulb_plan_turn_on_from_dark_room_lights_only_the_target():
    states = {SYP: "off", "light.s1": "unavailable", "light.s2": "unavailable", "light.s3": "unavailable"}
    plan = logic.bulb_plan("light.turn_on", ["light.s1"], {}, IDX, states)
    assert plan == logic.BulbPlan([SYP], [], [], ["light.s1"], ["light.s2", "light.s3"], True)


def test_bulb_plan_turn_on_when_all_lit_is_exclusive():
    states = {SYP: "on", "light.s1": "on", "light.s2": "on", "light.s3": "on"}
    plan = logic.bulb_plan("light.turn_on", ["light.s1"], {}, IDX, states)
    assert plan == logic.BulbPlan([], [], [], ["light.s1"], ["light.s2", "light.s3"], False)


def test_bulb_plan_color_on_lit_relay_keeps_siblings_but_color_from_dark_is_exclusive():
    lit = {SYP: "on", "light.s1": "on", "light.s2": "on", "light.s3": "on"}
    assert logic.bulb_plan("light.turn_on", ["light.s2"], {"color_name": "red"}, IDX, lit) == \
        logic.BulbPlan([], [], [], ["light.s2"], [], False)
    dark = {SYP: "off"}
    assert logic.bulb_plan("light.turn_on", ["light.s2"], {"color_name": "red"}, IDX, dark).siblings_off == \
        ["light.s1", "light.s3"]


def test_bulb_plan_kitchen_turns_off_the_other_relay():
    # żarówka 1 (L2) w kuchni: pozostałe na L2 gasimy, przełącznik L1 (żarówka 3) wyłączamy
    states = {K1: "on", K2: "on", "light.k1": "on", "light.k2": "on", "light.k3": "on"}
    plan = logic.bulb_plan("light.turn_on", ["light.k1"], {}, IDX, states)
    assert plan == logic.BulbPlan([], [K1], [], ["light.k1"], ["light.k2"], False)
    # dwie żarówki na różnych przełącznikach - oba zostają
    plan = logic.bulb_plan("light.turn_on", ["light.k1", "light.k3"], {}, IDX, states)
    assert plan.relays_off == [] and plan.siblings_off == ["light.k2"]


def test_bulb_plan_turn_off():
    # zgaszenie jednej z trzech - przełącznik zostaje
    states = {SYP: "on", "light.s1": "on", "light.s2": "on", "light.s3": "on"}
    assert logic.bulb_plan("light.turn_off", ["light.s3"], {}, IDX, states) == \
        logic.BulbPlan([], [], ["light.s3"], [], [], False)
    # zgaszenie ostatniej świecącej - wyłączamy też przełącznik
    states = {SYP: "on", "light.s1": "off", "light.s2": "off", "light.s3": "on"}
    assert logic.bulb_plan("light.turn_off", ["light.s3"], {}, IDX, states) == \
        logic.BulbPlan([], [SYP], ["light.s3"], [], [], False)
    # przełącznik wyłączony - nic do zrobienia
    assert logic.bulb_plan("light.turn_off", ["light.s1"], {}, IDX, {SYP: "off", "light.s1": "unavailable"}) == \
        logic.BulbPlan([], [], [], [], [], False)


def test_bulb_plan_ignores_other_actions():
    assert logic.bulb_plan("light.turn_on", ["light.lampka"], {}, IDX, {}) is None          # nie żarówka
    assert logic.bulb_plan("light.turn_on", ["light.s1", "light.lampka"], {}, IDX, {}) is None
    assert logic.bulb_plan("light.toggle", ["light.s1"], {}, IDX, {}) is None
    assert logic.bulb_plan("light.turn_on", [], {}, IDX, {}) is None


def test_color_gate_and_sentence():
    known = {SYP, "light.s1", "light.k1", "switch.plug"}
    colorful = logic.color_entities(BULB_DEVS)
    assert colorful == {SYP, "light.s1", "light.s2", "light.s3"}

    def color(e, data=None):
        return [{"action": "call_service", "service": f"{e.split('.')[0]}.turn_on", "entity_id": [e],
                 "data": data or {"color_name": "red"}}]
    dimmable = logic.dimmable_relays(BULB_DEVS)
    assert logic.check_actions(color(SYP), known, None, dimmable, colorful) == []
    assert logic.check_actions(color("light.s1"), known, None, dimmable, colorful) == []
    assert logic.check_actions(color("light.k1"), known, None, dimmable, colorful)      # żarówka bez koloru
    assert logic.check_actions(color("switch.plug"), known, None, dimmable, colorful)   # gniazdko
    assert logic.check_actions(color(SYP), known, None, dimmable)                       # brak listy: odrzucone
    assert logic.action_sentence(color("light.s1")[0], {}, BULB_DEVS) == "Ustawiam żarówkę 1 w sypialni na kolor czerwony"
    assert logic.action_sentence(color("light.s1", {"color_temp_kelvin": 2700})[0], {}, BULB_DEVS) == \
        "Ustawiam żarówkę 1 w sypialni na kolor ciepły biały"


# ----------------------------------------------------------------------------- kilka adresów serwera
def test_parse_and_order_urls():
    assert logic.parse_urls("http://a:8765, http://b:8765/ ;http://a:8765") == ["http://a:8765", "http://b:8765"]
    assert logic.parse_urls("") == [] and logic.parse_urls(None) == []
    assert logic.parse_urls("http://a:8765") == ["http://a:8765"]
    urls = ["http://a:8765", "http://b:8765"]
    assert logic.order_urls(urls, None) == urls
    assert logic.order_urls(urls, "http://b:8765") == ["http://b:8765", "http://a:8765"]   # ostatnio działający najpierw
    assert logic.order_urls(urls, "http://inny:1") == urls                                 # nieznany - kolejność z konfiguracji


# ----------------------------------------------------------------------------- audyt 0.1.9: potwierdzenie po stronie HA
def _call(service, ids, **extra):
    return [{"action": "call_service", "service": service, "entity_id": ids, **extra}]


def test_ha_forces_confirmation_for_sensitive_actions():
    # serwer może powiedzieć needs_confirmation=False - HA i tak pyta przy otwieraniu zamka
    assert logic.needs_ha_confirmation(_call("lock.unlock", ["lock.zamek"]), {})
    assert logic.needs_ha_confirmation(_call("lock.open", ["lock.zamek"]), {})
    assert logic.needs_ha_confirmation(_call("alarm_control_panel.alarm_disarm", ["alarm_control_panel.dom"]), {})
    # wrażliwa akcja w środku listy też wymusza pytanie
    mixed = _call("light.turn_on", ["light.lampka"]) + _call("lock.unlock", ["lock.zamek"])
    assert logic.needs_ha_confirmation(mixed, {})
    # brama/garaż: tylko otwieranie, tylko device_class garage/gate
    assert logic.needs_ha_confirmation(_call("cover.open_cover", ["cover.brama"]), {"cover.brama": "gate"})
    assert logic.needs_ha_confirmation(_call("cover.toggle", ["cover.garaz"]), {"cover.garaz": "garage"})
    assert not logic.needs_ha_confirmation(_call("cover.close_cover", ["cover.brama"]), {"cover.brama": "gate"})
    assert not logic.needs_ha_confirmation(_call("cover.open_cover", ["cover.roleta"]), {"cover.roleta": "shutter"})


def test_ha_does_not_force_confirmation_for_ordinary_actions():
    assert not logic.needs_ha_confirmation(_call("lock.lock", ["lock.zamek"]), {})
    assert not logic.needs_ha_confirmation(_call("light.turn_on", ["light.lampka"]), {})
    assert not logic.needs_ha_confirmation(_call("switch.turn_off", ["switch.kuchnia_l1"]), {})
    # 0.1.10: skrypty i przyciski bez wymuszonego pytania (decyzja użytkownika - sterowanie głosem ma być szybkie)
    assert not logic.needs_ha_confirmation(_call("script.turn_on", ["script.zamknij_i_zgas_wszystko"]), {})
    assert not logic.needs_ha_confirmation(_call("input_button.press", ["input_button.dzwonek"]), {})
    assert not logic.needs_ha_confirmation([{"action": "get_state", "entity_id": ["lock.zamek"]}], {})
    assert not logic.needs_ha_confirmation([], {})


def test_public_http_urls_warns_only_outside_lan():
    lan = ["http://192.168.1.5:8765", "http://10.0.0.2:8765", "http://172.16.3.4:8765", "http://telefon.local:8765",
           "http://telefon:8765", "http://127.0.0.1:8765", "http://100.101.102.103:8765", "https://8.8.8.8:8765",
           "http://[fd00::1]:8765"]
    assert logic.public_http_urls(lan) == []
    assert logic.public_http_urls(["http://8.8.8.8:8765", "http://moj.example.com:8765", "8.8.4.4:8765"]) == \
        ["http://8.8.8.8:8765", "http://moj.example.com:8765", "8.8.4.4:8765"]


def _dev(ids, relay=None, bulbs=()):
    return logic.DeviceName(frozenset(ids), "urządzenie", "urządzenie", bulbs=frozenset(bulbs), relay=relay)


def test_restrict_devices_keeps_only_known_switch_relay_and_light_bulbs():
    known = {"switch.kuchnia_l1", "light.zarowka_kuchnia", "light.zarowka_salon"}
    allowed = known.__contains__
    out = logic.restrict_devices([
        _dev({"light.zarowka_kuchnia"}, relay="switch.kuchnia_l1"),          # poprawny relay - zostaje
        _dev({"light.zarowka_salon"}, relay="lock.zamek"),                   # zamek jako relay - odcięty
        _dev({"light.zarowka_salon"}, relay="switch.nie_ma_w_ha"),           # nieznany przełącznik - odcięty
        _dev({"switch.kuchnia_l1"}, bulbs={"light.zarowka_kuchnia", "lock.zamek", "light.obca"}),
    ], allowed)
    assert out[0].relay == "switch.kuchnia_l1"
    assert out[1].relay is None and out[2].relay is None
    assert out[3].bulbs == frozenset({"light.zarowka_kuchnia"})


def test_restrict_devices_handles_missing_package():
    assert logic.restrict_devices(None, lambda e: True) == []

