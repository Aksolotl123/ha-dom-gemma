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
    name: str
    state: str
    unit: str | None = None
    device_class: str | None = None


def _names(ids: list[str], info: dict[str, EntityInfo]) -> str:
    names = []
    for e in ids:
        n = info[e].name if e in info else e
        if n not in names:  # np. dwa kanały L1+L2 z tą samą nazwą
            names.append(n)
    return ", ".join(names)


def action_sentence(a: dict, info: dict[str, EntityInfo]) -> str:
    domain, _, name = a["service"].partition(".")
    data = a.get("data") or {}
    who = _names(a["entity_id"], info)
    if "brightness_pct" in data:
        return f"Ustawiam {who} na {data['brightness_pct']}%"
    if "brightness_step_pct" in data:
        return f"{'Rozjaśniam' if data['brightness_step_pct'] > 0 else 'Przyciemniam'} {who}"
    if domain == "script":
        return f"Uruchamiam {who}"
    return f"{_VERB.get(name, 'Wykonuję')} {who}"


def confirmation_question(actions: list[dict], info: dict[str, EntityInfo]) -> str:
    parts = []
    for a in actions:
        if a.get("action") != "call_service":
            continue
        domain, _, name = a["service"].partition(".")
        verb = "uruchomić" if domain == "script" else _INFINITIVE.get(name, "wykonać")
        parts.append(f"{verb} {_names(a['entity_id'], info)}")
    return f"Czy na pewno {' i '.join(parts)}? Powiedz tak albo nie."


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
    if i.unit:
        return f"{i.name}: {_number(i.state)} {i.unit}"
    word = _STATE_WORDS.get(i.device_class or "", {}).get(i.state) or _GENERIC.get(i.state, i.state)
    return f"{i.name}: {word}"
