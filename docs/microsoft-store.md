# Microsoft Store + WinGet (msstore)

Cel: dystrybucja bez kupowania komercyjnego certyfikatu do podpisywania oraz bez utrzymywania osobnych manifestów w `winget-pkgs`.

## Microsoft Store

1) Załóż konto w Partner Center (wymaga opłaty za konto deweloperskie, ale nie wymaga kupowania osobnego certyfikatu do podpisywania).
2) Utwórz nową aplikację i zarezerwuj nazwę.
3) Wybierz dystrybucję Win32 i wrzuć instalatory:
   - `programista-win-x64.msi`
   - `programista-win-arm64.msi`
4) Przy kolejnych wersjach aktualizujesz listing, wrzucając nowe MSI (Store zapewnia kanał dystrybucji i aktualizacji).

> Uwaga: polityki dot. podpisu mogą się zmieniać. Jeśli Partner Center zacznie wymagać podpisu instalatora, jedyną drogą „bez kupowania certyfikatu” jest przejście na MSIX i podpisywanie paczki certyfikatem ze Store (Store i tak finalnie re-signuje paczki).

## WinGet

Po publikacji w Microsoft Store aplikacja jest instalowalna przez WinGet ze źródła `msstore` (nie trzeba PR do `microsoft/winget-pkgs`):

- wyszukiwanie: `winget search Programista -s msstore`
- instalacja: `winget install --id <PRODUCT_ID> -s msstore`

`<PRODUCT_ID>` ma format `9N...` i można go odczytać np. z wyników `winget search/show` albo z linku w Microsoft Store.

