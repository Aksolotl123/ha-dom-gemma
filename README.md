# Dom Gemma — agent rozmowy Assist z lokalnym modelem

Integracja Home Assistant, która przekazuje polecenia z Assist (tekst lub głos w aplikacji Companion,
satelity głosowe) do **DomGemma Server** — małego modelu językowego (Gemma 3 1B dotrenowanego na urządzeniach
domu) działającego na telefonie w sieci domowej. Model zwraca akcje, a integracja:

- sprawdza je po stronie HA (tylko istniejące encje, tylko dozwolone usługi, opcjonalnie tylko encje wystawione
  do Assist),
- wykonuje usługi w kontekście użytkownika, który mówi,
- odpowiada na pytania o stan („W salonie: 21,5 °C”) bez drugiego wywołania modelu,
- pyta o potwierdzenie przed akcjami wskazanymi przez serwer (np. otwarcie zamka) — „tak” / „nie” w tej samej
  rozmowie, ważne 60 s,
- rozumie odpowiedź na dopytanie („Które światło w salonie?” → „Nad stołem”): urządzenie bierze z odpowiedzi
  i miejsca z pytania, a „włącz”/„zgaś” z pierwszego polecenia,
- włącza żarówki zasilane przez sterowany przełącznik: po włączeniu przełącznika czeka (do 20 s), aż żarówka się
  zgłosi, i włącza ją, jeśli wstała zgaszona (pole `bulbs` w liście urządzeń serwera),
- ustawia jasność takich świateł: włącza przełącznik, po chwili wysyła jasność do żarówek i ponawia, dopóki nowy
  stan żarówki jej nie potwierdzi; jasność na przełączniku bez żarówek (gniazdko) jest odrzucana,
- gdy serwer nie odpowiada, przekazuje zdanie wbudowanemu agentowi HA (opcja); czujnik „Serwer” (łączność,
  sprawdzany co 30 s) pokazuje, czy serwer działa — można na nim oprzeć powiadomienie.

Integracja nie zawiera żadnych danych domu: lista urządzeń i model są na serwerze, a adres i token podaje się
w konfiguracji.

## Instalacja

1. HACS → Integracje → ⋮ → Własne repozytoria → dodaj adres tego repozytorium (kategoria: Integracja).
2. Zainstaluj „Dom Gemma” i uruchom ponownie Home Assistant.
3. Ustawienia → Urządzenia i usługi → Dodaj integrację → „Dom Gemma”: adres serwera (np. `http://<ip-telefonu>:8765`)
   i token z pliku `config.json` serwera. Kilka adresów rozdziel przecinkiem (np. telefon, który bywa w dwóch sieciach Wi-Fi):
   integracja pyta najpierw ten, który ostatnio odpowiedział, a gdy nie odpowie w 3 s - następny.
4. Ustawienia → Asystenci głosowi → wybierz asystenta → Agent rozmowy: **Dom Gemma**.

## Opcje

- **Wykonuj tylko na encjach wystawionych do Assist** — dodatkowe ograniczenie ponad listę urządzeń serwera.
- **Gdy serwer nie odpowiada, przekaż zdanie wbudowanemu agentowi HA** — domyślnie włączone.

## Bezpieczeństwo

Uprawnienia użytkowników HA nie ograniczają domyślnie sterowania encjami, a satelity głosowe działają bez
użytkownika — dlatego walidacja w integracji (lista usług, istnienie encji) i potwierdzenia są główną ochroną.
Serwer powinien być dostępny tylko w sieci lokalnej.
