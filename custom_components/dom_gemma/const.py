"""Stałe integracji Dom Gemma."""
DOMAIN = "dom_gemma"

CONF_URL = "url"
CONF_TOKEN = "token"
CONF_REQUIRE_EXPOSED = "require_exposed"
CONF_FALLBACK = "fallback_to_default"

DEFAULT_TIMEOUT = 15  # s; odpowiedź serwera ~1-3.6 s, ale gdy przygotowuje rozmowę, czeka do ~10 s
CONFIRM_TTL = 60      # s; po tym czasie "tak" nie wykona już oczekującej akcji
