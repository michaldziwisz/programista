# Programista (desktop)

Aplikacja desktop do przeglądania ramówek:
- **Telewizja** (Teleman)
- **Radio** (polskieradio.pl)
- **Programy archiwalne** (staratelewizja.fandom.com)

Priorytety: 100% natywne kontrolki (wxPython), dostępność, cache w SQLite, możliwość wymuszenia odświeżenia oraz aktualizowalni dostawcy treści.

## Uruchomienie (dev)

1) Utwórz venv (Python 3.12+)
2) Zainstaluj zależności:
   - `pip install -e .[dev]`
3) Uruchom:
   - `python -m tvguide_app`

## Testy

- `pytest`
