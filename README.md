# Dom Gemma — agent rozmowy Assist z lokalnym modelem

Integracja Home Assistant, która przekazuje polecenia z Assist (tekst lub głos w aplikacji Companion,
satelity głosowe) do **DomGemma Server** — małego modelu językowego (Gemma 3 1B dotrenowanego na urządzeniach
domu) działającego na telefonie w sieci domowej. Model zwraca akcje, a integracja:

- sprawdza je po stronie HA (tylko istniejące encje, tylko dozwolone usługi, opcjonalnie tylko encje wystawione
  do Assist),
- wykonuje usługi w kontekście użytkownika, który mówi,
- odpowiada na pytania o stan („W salonie: 21,5 °C”) bez drugiego wywołania modelu,
- pyta o potwierdzenie przed akcjami wskazanymi przez serwer — „tak” / „nie” w tej samej rozmowie, ważne 60 s;
  przed akcjami wrażliwymi (otwarcie zamka, uruchomienie skryptu, rozbrojenie alarmu, otwarcie bramy/garażu) pyta
  zawsze, nawet gdy serwer potwierdzenia nie żąda,
- rozumie odpowiedź na dopytanie („Które światło w salonie?” → „Nad stołem”): urządzenie bierze z odpowiedzi
  i miejsca z pytania, a „włącz”/„zgaś” z pierwszego polecenia,
- włącza żarówki zasilane przez sterowany przełącznik: po włączeniu przełącznika czeka (do 20 s), aż żarówka się
  zgłosi, i włącza ją, jeśli wstała zgaszona (pole `bulbs` w liście urządzeń serwera),
- ustawia jasność takich świateł: włącza przełącznik, po chwili wysyła jasność do żarówek i ponawia, dopóki nowy
  stan żarówki jej nie potwierdzi; jasność na przełączniku bez żarówek (gniazdko) jest odrzucana,
- zmienia kolor świateł (`color_name` / `color_temp_kelvin`) — tylko tych, które lista urządzeń oznacza jako
  kolorowe (`color`); za przełącznikiem kolor trafia do żarówek jak jasność,
- steruje pojedynczą żarówką za przełącznikiem (pole `relay` w liście urządzeń): „włącz żarówkę 1” włącza
  przełącznik, zapala tę żarówkę i gasi pozostałe w pomieszczeniu; po zgaszeniu ostatniej świecącej żarówki
  wyłącza też przełącznik,
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
  Od wersji 0.1.9 domyślnie **włączone** dla nowo dodanych integracji. Integracje dodane wcześniej zachowują
  dotychczasowe ustawienie (wyłączone, jeśli nie było zmieniane) — warto je włączyć w opcjach i wystawić do Assist
  tylko encje, którymi model ma sterować.
- **Gdy serwer nie odpowiada, przekaż zdanie wbudowanemu agentowi HA** — domyślnie włączone.

## Bezpieczeństwo

Uprawnienia użytkowników HA nie ograniczają domyślnie sterowania encjami, a satelity głosowe działają bez
użytkownika — dlatego walidacja w integracji (lista usług, istnienie encji) i potwierdzenia są główną ochroną.
Serwer powinien być dostępny tylko w sieci lokalnej.

- **O potwierdzeniu decyduje HA.** Dla akcji wrażliwych (`lock.unlock`/`lock.open`, każdy `script.*`, rozbrojenie
  alarmu, otwarcie bramy/garażu — `cover` z `device_class` `gate`/`garage`) integracja pyta „tak/nie” niezależnie
  od pola `needs_confirmation` z serwera — podstawiony serwer nie otworzy zamka bez zgody (alarm i bramy są
  dziś poza listą dozwolonych usług — to zabezpieczenie na wypadek jej rozszerzenia). Zamek lub bramę
  sterowane zwykłym przełącznikiem (`switch.*`) integracja traktuje jak każdy przełącznik — takich encji lepiej nie
  wystawiać do Assist (przy włączonej opcji wystawienia) albo nie umieszczać na liście urządzeń serwera.
- **Token i http.** Połączenie z serwerem idzie po `http`, więc token i treść poleceń są w sieci jawnym tekstem.
  Przy kilku adresach token jest wysyłany kolejno pod każdy z nich, aż któryś odpowie — jeśli pod adresem
  telefonu znajdzie się inne urządzenie (np. po zmianie IP z DHCP), dostanie token. Dlatego: ustaw w routerze
  **stałe adresy (rezerwację DHCP)** dla telefonu z serwerem, nie podawaj adresów spoza swojej sieci, a jeśli
  serwer działa za odwrotnym proxy z certyfikatem — użyj adresu `https://`. Dla adresu `http://` spoza sieci
  prywatnej integracja zapisuje w logu ostrzeżenie (raz).
- Treść wypowiedzi odrzuconych przez walidację trafia do logu tylko na poziomie DEBUG.
