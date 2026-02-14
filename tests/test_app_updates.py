from tvguide_app.core.app_updates import _pick_windows_installer_asset, _version_tuple


def test_version_tuple_parses_semver_prefix() -> None:
    assert _version_tuple("v0.1.18") == (0, 1, 18, 0)
    assert _version_tuple("0.1") == (0, 1, 0, 0)
    assert _version_tuple("1") == (1, 0, 0, 0)
    assert _version_tuple("1.2.3.4") == (1, 2, 3, 4)
    assert _version_tuple("1.2.3-rc1") == (1, 2, 3, 0)


def test_pick_windows_installer_asset_prefers_arch_specific_msi() -> None:
    assets = [
        {"name": "programista.exe", "browser_download_url": "https://example.com/programista.exe"},
        {
            "name": "programista-win-arm64.msi",
            "browser_download_url": "https://example.com/programista-win-arm64.msi",
        },
        {
            "name": "programista-win-x64.msi",
            "browser_download_url": "https://example.com/programista-win-x64.msi",
        },
    ]
    name, url = _pick_windows_installer_asset(assets, arch="arm64")
    assert name == "programista-win-arm64.msi"
    assert url == "https://example.com/programista-win-arm64.msi"


def test_pick_windows_installer_asset_falls_back_to_x64() -> None:
    assets = [
        {
            "name": "programista-win-x64.msi",
            "browser_download_url": "https://example.com/programista-win-x64.msi",
        }
    ]
    name, url = _pick_windows_installer_asset(assets, arch="arm64")
    assert name == "programista-win-x64.msi"
    assert url == "https://example.com/programista-win-x64.msi"

