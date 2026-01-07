# Programista (desktop)

Aplikacja desktop do przeglądania ramówek:
- **Telewizja** (Teleman)
- **Radio** (polskieradio.pl)
- **Programy archiwalne** (staratelewizja.fandom.com)

Priorytety: 100% natywne kontrolki (wxPython), dostępność, cache w SQLite, możliwość wymuszenia odświeżenia oraz aktualizowalni dostawcy treści.

## Uruchomienie (dev)

1) Utwórz venv (Python 3.12+)
2) Zainstaluj zależności:
   - `pip install -e ".[dev,gui]"`
3) Uruchom:
   - `python -m tvguide_app`

## Testy

- `pytest`

## Binarka (Windows)

W WSL możesz pobrać najnowszą binarkę do `dist-windows/programista.exe`:
- `bash scripts/download_windows_release.sh`

Albo konkretny tag:
- `bash scripts/download_windows_release.sh v0.1.3`
