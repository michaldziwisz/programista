# Polityka prywatności — Programista

Ostatnia aktualizacja: 2026-02-15

## Kto jest administratorem?

Administratorem danych w kontekście działania aplikacji jest autor projektu: Michał Dziwisz.

## Jakie dane przetwarza aplikacja?

### Dane przechowywane lokalnie (na Twoim komputerze)

Aplikacja zapisuje wyłącznie dane potrzebne do działania, m.in.:
- ustawienia aplikacji (np. filtry),
- ulubione pozycje,
- cache pobranych danych oraz lokalny indeks wyszukiwania.

Możesz usunąć te dane, odinstalowując aplikację i/lub kasując katalog danych aplikacji w profilu użytkownika.

### Dane przesyłane przez sieć

Aplikacja pobiera treści (ramówki, opisy) z zewnętrznych serwisów, wysyłając standardowe zapytania HTTP. Dostawcy tych serwisów mogą przetwarzać typowe dane techniczne (np. adres IP, nagłówki HTTP) zgodnie z ich politykami.

Dodatkowo aplikacja może korzystać z serwera wyszukiwania (domyślnie `https://tyflo.eu.org/programista/api`):
- przy pierwszym użyciu wyszukiwania aplikacja generuje losowy identyfikator instalacji (`install_id`) i rejestruje go na serwerze,
- serwer może otrzymać informację o wersji aplikacji oraz podstawowe informacje o platformie (system/wersja/architektura),
- podczas wyszukiwania do serwera przesyłana jest treść zapytania oraz zakres wyszukiwania.

`install_id` nie jest danymi pozwalającymi na bezpośrednią identyfikację użytkownika, ale jest trwałym identyfikatorem tej instalacji.

### Zgłaszanie błędów / sugestii

Jeśli użyjesz funkcji **Zgłoś błąd / sugestię…**, aplikacja wyśle treść zgłoszenia do usługi wskazanej w konfiguracji (adres jest pobierany z ustawień/zmiennych środowiskowych). W zależności od tego, co wpiszesz i co zaznaczysz, zgłoszenie może zawierać:
- tytuł i opis,
- opcjonalny adres e-mail,
- opcjonalnie dołączone logi/diagnostykę.

## Cele przetwarzania

- zapewnienie działania funkcji aplikacji (pobieranie treści, cache, wyszukiwanie),
- diagnostyka i obsługa zgłoszeń (tylko jeśli zdecydujesz się wysłać zgłoszenie).

## Udostępnianie danych

Autor nie sprzedaje danych użytkowników. Dane mogą trafiać do podmiotów dostarczających usługi, z których korzysta aplikacja (np. serwisy z ramówkami, GitHub, serwer wyszukiwania) w zakresie niezbędnym do działania.

## Kontakt

W sprawach prywatności proszę o kontakt przez GitHub: `https://github.com/michaldziwisz/programista/issues`.

