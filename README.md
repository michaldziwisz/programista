# Programista (desktop)

Aplikacja desktop do przeglądania ramówek:
- **Telewizja** (Teleman)
- **Programy TV z udogodnieniami** (TVP / Polsat / Puls) — napisy / język migowy / audiodeskrypcja
- **Radio** (Polskie Radio / Radio Kierowców / Radio Nowy Świat / Radio 357)
- **Ulubione** (TV + Radio)
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

W WSL możesz pobrać binarkę do `dist-windows/programista.exe`:
- najnowsza (auto wykrycie arch): `bash scripts/download_windows_release.sh`
- wymuszenie arch: `bash scripts/download_windows_release.sh latest arm64` albo `bash scripts/download_windows_release.sh latest x64`
- instalator MSI: `bash scripts/download_windows_release.sh latest arm64 msi` (zapisze do `dist-windows/programista.msi`)

Albo konkretny tag:
- `bash scripts/download_windows_release.sh v0.1.3 arm64`

Windows na ARM (np. Parallels na Apple Silicon): jeśli release nie zawiera natywnej binarki ARM64, skrypt pobierze wariant x64 (emulacja) i wypisze ostrzeżenie.

Build lokalny (Windows / PowerShell):
- `powershell -ExecutionPolicy Bypass -File scripts\\build_windows.ps1 -Arch arm64`

## Instalator (Windows MSI) + aktualizacje

Docelowo do dystrybucji w Microsoft Store i WinGet najwygodniejszy jest instalator MSI (osobno dla `x64` i `arm64`).

W aplikacji jest pozycja menu: **Pomoc → Sprawdź aktualizacje programu…** — sprawdza GitHub Releases i (na Windows) potrafi pobrać oraz uruchomić instalator.

## Licencja

MIT — zob. `LICENSE`.
