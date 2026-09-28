"""Logika agenta bez zależności od Home Assistant (testowana zwykłym pytestem, tests/test_logic.py)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Usługi, które agent w ogóle wykona - niezależnie od tego, co zwróci serwer (druga warstwa walidacji).
# Uprawnienia HA tu nie chronią: zwykły użytkownik może domyślnie sterować wszystkim, a satelity głosowe
# wołają agenta bez użytkownika (Context bez user_id).
ALLOWED_SERVICES: dict[str, set[str]] = {
    "light": {"turn_on", "turn_off", "toggle"},
    "switch": {"turn_on", "turn_off", "toggle"},
    "fan": {"turn_on", "turn_off"},
    "media_player": {"turn_on", "turn_off", "volume_up", "volume_down", "volume_mute"},
    "lock": {"lock", "unlock"},
    "script": {"turn_on"},
    "input_button": {"press"},
}
ALLOWED_DATA: dict[str, set[str]] = {
    "light.turn_on": {"brightness_pct", "brightness_step_pct"},
    "media_player.volume_mute": {"is_volume_muted"},
}

_YES = {"tak", "potwierdzam", "potwierdz", "zgadza sie", "dobrze", "ok", "okej", "jasne", "tak zrob to", "zrob to"}
_NO = {"nie", "anuluj", "stop", "nie rob tego", "zostaw", "nie trzeba"}


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c)).replace("ł", "l")
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


def answer_kind(text: str) -> str:
    """'yes' / 'no' / 'other' - odpowiedź na pytanie o potwierdzenie."""
    t = re.sub(r"\s+", " ", _plain(text))
    if t in _YES or t.startswith("tak "):
        return "yes"
    if t in _NO or t.startswith("nie "):
        return "no"
    return "other"


# ----------------------------------------------------------------------------- odpowiedź na dopytanie
# Model nie był uczony rozmów wieloturowych: samo „Nad stołem.” włącza (domyślny kierunek), a sklejone
# „Zgaś światło w salonie. Nad stołem.” myli urządzenie. Sprawdzone na serwerze (28.09): urządzenie trafnie
# wskazuje odpowiedź z dopisanym miejscem z pytania („Nad stołem w salonie.”), a kierunek bierzemy z polecenia.

_OFF_WORDS = ("zgas", "wylacz", "pogas", "gas")
_ON_WORDS = ("wlacz", "zapal", "pozapal")
_COMMAND_WORDS = _OFF_WORDS + _ON_WORDS + ("otworz", "zamknij", "ustaw", "przyciem", "rozjasn", "podglos", "scisz",
                                           "wycisz", "uruchom", "jaka", "jaki", "jakie", "czy", "ile", "sprawdz")
_QUESTION_LOC = re.compile(r"^Które światło (.+)\?$")


def power_direction(text: str) -> str | None:
    """'turn_on' / 'turn_off' z czasownika polecenia; None gdy brak albo oba (wtedy decyduje model)."""
    words = _plain(text).split()
    off = any(w.startswith(_OFF_WORDS) for w in words)
    on = any(w.startswith(_ON_WORDS) for w in words)
    return "turn_off" if off and not on else "turn_on" if on and not off else None


def is_new_command(answer: str) -> bool:
    """Odpowiedź zaczyna się czasownikiem polecenia („zgaś światło w kuchni”) - nowe polecenie, nie doprecyzowanie."""
    words = _plain(answer).split()
    return bool(words) and words[0].startswith(_COMMAND_WORDS)


def followup_text(question: str, answer: str) -> str:
    """Tekst dla modelu: odpowiedź + miejsce z pytania „Które światło w salonie?” (jeśli go w odpowiedzi nie ma)."""
    ans = answer.strip().rstrip(".!?").strip()
    m = _QUESTION_LOC.match(question.strip())
    if m and _plain(m.group(1)) not in _plain(ans):
        return f"{ans} {m.group(1)}."
    return f"{ans}."


def with_direction(actions: list[dict], direction: str, light_ids: set[str]) -> list[dict] | None:
    """Te same urządzenia z kierunkiem z polecenia. Tylko wł/wył świateł - inne akcje (i np. płyta indukcyjna,
    która wymaga potwierdzenia przy włączaniu) zostają bez zmian (None)."""
    for a in actions:
        domain, _, name = str(a.get("service", "")).partition(".")
        if (a.get("action") != "call_service" or name not in ("turn_on", "turn_off") or a.get("data")
                or not a.get("entity_id") or not set(a["entity_id"]) <= light_ids):
            return None
    return [{**a, "service": f"{a['service'].partition('.')[0]}.{direction}"} for a in actions]


def actions_direction(actions: list[dict]) -> str | None:
    """Wspólny kierunek wł/wył akcji (bez danych); None gdy mieszany albo to nie są wł/wył."""
    if not actions or any(a.get("action") != "call_service" or a.get("data") for a in actions):
        return None
    names = {str(a.get("service", "")).partition(".")[2] for a in actions}
    return names.pop() if len(names) == 1 and names <= {"turn_on", "turn_off"} else None


# ----------------------------------------------------------------------------- żarówki za przekaźnikami
# Żarówki Zigbee są zasilane przez sterowane przełączniki. Po włączeniu przełącznika żarówka zgłasza się po ~3 s
# w stanie zależnym od swojego „power-on behavior” - np. przy „restore” wstaje zgaszona, jeśli była zgaszona.

def bulbs_for(entity_ids: list[str], devices: list[DeviceName]) -> set[str]:
    """Żarówki zasilane przez podane przełączniki (z najmniejszego urządzenia z pakietu, które je zawiera)."""
    names = entity_names([d for d in devices if d.bulbs])
    return {b for e in entity_ids if (d := names.get(e)) for b in d.bulbs}


def bulbs_to_turn_on(states: dict[str, str | None]) -> tuple[list[str], bool]:
    """Stany żarówek -> (zgaszone do włączenia teraz, czy wszystko załatwione).
    None = brak encji w HA (pomijamy); unavailable/unknown = żarówka jeszcze nie ma zasilania / się nie zgłosiła."""
    off = sorted(b for b, s in states.items() if s == "off")
    waiting = any(s in ("unavailable", "unknown") for s in states.values())
    return off, not off and not waiting


def check_actions(actions: list[dict], known_entities: set[str], exposed: set[str] | None) -> list[str]:
    """Walidacja po stronie HA. exposed=None -> nie wymagamy wystawienia do Assist."""
    if not isinstance(actions, list) or not actions:
        return ["brak akcji"]
    errors = []
    for a in actions:
        kind = a.get("action")
        if kind in ("clarify", "unsupported"):
            if len(actions) > 1:
                errors.append(f"{kind} musi być jedyną akcją")
            continue
        ids = a.get("entity_id") or []
        if kind not in ("call_service", "get_state") or not ids:
            errors.append(f"zła akcja {kind}")
            continue
        missing = [e for e in ids if e not in known_entities]
        if missing:
            errors.append(f"nie ma w HA: {missing}")
        if exposed is not None and (hidden := [e for e in ids if e not in exposed]):
            errors.append(f"niewystawione do Assist: {hidden}")
        if kind == "get_state":
            continue
        service = a.get("service", "")
        domain, _, name = service.partition(".")
        if name not in ALLOWED_SERVICES.get(domain, set()):
            errors.append(f"niedozwolona usługa {service}")
        if any(e.split(".", 1)[0] != domain for e in ids):
            errors.append(f"encje z innej domeny niż {domain}")
        if set(a.get("data") or {}) - ALLOWED_DATA.get(service, set()):
            errors.append(f"niedozwolone dane dla {service}")
    return errors


# ----------------------------------------------------------------------------- zdania dla użytkownika

_VERB = {"turn_on": "Włączam", "turn_off": "Wyłączam", "toggle": "Przełączam", "lock": "Zamykam",
         "unlock": "Otwieram", "volume_up": "Pogłaśniam", "volume_down": "Ściszam", "volume_mute": "Wyciszam",
         "press": "Uruchamiam"}
_INFINITIVE = {"unlock": "otworzyć", "lock": "zamknąć", "turn_on": "włączyć", "turn_off": "wyłączyć",
               "press": "uruchomić", "toggle": "przełączyć"}


@dataclass
class EntityInfo:
    name: str                  # mianownik: „lampka przy biurku”
    state: str
    unit: str | None = None
    device_class: str | None = None
    acc: str | None = None     # biernik: „lampkę przy biurku” (z pakietu serwera; brak -> name)


@dataclass
class DeviceName:
    ids: frozenset[str]
    nom: str
    acc: str
    kind: str = ""
    bulbs: frozenset[str] = frozenset()  # żarówki (light.*) zasilane przez te przełączniki


def names_from_server(devices: dict) -> list[DeviceName]:
    """/v1/devices serwera: {uchwyt: {"ids", "name", "acc", "kind", "bulbs"?, ...}} -> lista urządzeń."""
    out = []
    for d in devices.values():
        if isinstance(d, dict) and d.get("ids") and d.get("name"):
            # żarówki tylko z domeny light - agent włącza je sam, bez modelu i bez listy dozwolonych usług
            bulbs = frozenset(b for b in d.get("bulbs") or [] if isinstance(b, str) and b.startswith("light."))
            out.append(DeviceName(frozenset(d["ids"]), d["name"], d.get("acc") or d["name"],
                                  str(d.get("kind") or ""), bulbs))
    return out


def entity_names(devices: list[DeviceName]) -> dict[str, DeviceName]:
    """Encja -> nazwa najmniejszego urządzenia, które ją zawiera (kanał L1 -> „kuchnia 2”, nie „światło w kuchni”)."""
    out: dict[str, DeviceName] = {}
    for d in sorted(devices, key=lambda d: -len(d.ids)):
        for e in d.ids:
            out[e] = d
    return out


def _names(ids: list[str], info: dict[str, EntityInfo], devices: list[DeviceName] | None = None,
           case: str = "acc") -> str:
    """Nazwy do zdania. Najpierw całe urządzenia z pakietu (L1+L2 = „światło w kuchni”), potem pojedyncze encje."""
    remaining = list(ids)
    names: list[str] = []
    for d in sorted(devices or [], key=lambda d: -len(d.ids)):
        if d.ids and d.ids <= set(remaining):
            n = d.acc if case == "acc" else d.nom
            if n not in names:
                names.append(n)
            remaining = [e for e in remaining if e not in d.ids]
    for e in remaining:
        i = info.get(e)
        n = ((i.acc or i.name) if case == "acc" else i.name) if i else e
        if n not in names:  # np. dwa kanały z tą samą nazwą w HA
            names.append(n)
    return ", ".join(names)


def action_sentence(a: dict, info: dict[str, EntityInfo], devices: list[DeviceName] | None = None) -> str:
    domain, _, name = a["service"].partition(".")
    data = a.get("data") or {}
    who = _names(a["entity_id"], info, devices)
    if "brightness_pct" in data:
        return f"Ustawiam {who} na {data['brightness_pct']}%"
    if "brightness_step_pct" in data:
        return f"{'Rozjaśniam' if data['brightness_step_pct'] > 0 else 'Przyciemniam'} {who}"
    if domain == "script":
        return f"Uruchamiam {who}"
    return f"{_VERB.get(name, 'Wykonuję')} {who}"


def confirmation_question(actions: list[dict], info: dict[str, EntityInfo],
                          devices: list[DeviceName] | None = None) -> str:
    parts = []
    for a in actions:
        if a.get("action") != "call_service":
            continue
        domain, _, name = a["service"].partition(".")
        verb = "uruchomić" if domain == "script" else _INFINITIVE.get(name, "wykonać")
        parts.append(f"{verb} {_names(a['entity_id'], info, devices)}")
    return f"Czy na pewno {' i '.join(parts)}? Powiedz tak albo nie."


def cap(sentence: str) -> str:
    """Wielka litera na początku zdania (nazwy z devices.yaml są małą literą: „temperatura w salonie”)."""
    return sentence[:1].upper() + sentence[1:]


def _number(value: str) -> str:
    try:
        f = float(value)
    except ValueError:
        return value
    return (f"{f:.1f}".rstrip("0").rstrip(".")).replace(".", ",")


_STATE_WORDS = {
    "door": {"on": "otwarte", "off": "zamknięte"},
    "window": {"on": "otwarte", "off": "zamknięte"},
    "opening": {"on": "otwarte", "off": "zamknięte"},
    "moisture": {"on": "wykryto wodę", "off": "sucho"},
    "occupancy": {"on": "wykryto obecność", "off": "brak obecności"},
    "presence": {"on": "wykryto obecność", "off": "brak obecności"},
}
_GENERIC = {"on": "włączone", "off": "wyłączone", "locked": "zamknięte", "unlocked": "otwarte",
            "open": "otwarte", "closed": "zamknięte", "unavailable": "niedostępne", "unknown": "stan nieznany",
            "playing": "gra", "paused": "wstrzymane", "idle": "bezczynne", "jammed": "zablokowany zamek"}


def state_sentence(entity_id: str, info: dict[str, EntityInfo]) -> str:
    i = info.get(entity_id)
    if i is None:
        return f"{entity_id}: nie znalazłem"
    if i.state in ("unavailable", "unknown"):
        return f"{i.name}: {_GENERIC[i.state]}"
    if i.unit:
        return f"{i.name}: {_number(i.state)} {i.unit}"
    word = _STATE_WORDS.get(i.device_class or "", {}).get(i.state) or _GENERIC.get(i.state, i.state)
    return f"{i.name}: {word}"
