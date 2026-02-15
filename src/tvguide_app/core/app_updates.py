from __future__ import annotations

from dataclasses import dataclass
import json
import platform
import re
from typing import Literal

from tvguide_app.core.http import HttpClient
from tvguide_app.core.windows_appmodel import is_packaged_app


GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/michaldziwisz/programista/releases/latest"
_CACHE_KEY_LATEST_RELEASE = "app_update/github_latest_release_v1"


WindowsArch = Literal["x64", "arm64", "unknown"]


@dataclass(frozen=True)
class AppUpdateCheckResult:
    current_version: str
    latest_version: str | None
    update_available: bool
    release_url: str | None
    installer_name: str | None
    installer_url: str | None
    message: str


def windows_arch() -> WindowsArch:
    machine = (platform.machine() or "").upper()
    if machine in ("ARM64", "AARCH64"):
        return "arm64"
    if machine in ("AMD64", "X86_64"):
        return "x64"
    return "unknown"


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    v = (version or "").strip()
    if v.startswith(("v", "V")):
        v = v[1:]

    # Keep only the numeric prefix (e.g. "0.1.2", drop "-rc1", "+build", etc.).
    m = re.match(r"^([0-9]+(?:\.[0-9]+){0,3})", v)
    core = m.group(1) if m else "0"
    parts = [int(p) for p in core.split(".") if p]
    parts = (parts + [0, 0, 0, 0])[:4]
    return parts[0], parts[1], parts[2], parts[3]


def _pick_windows_installer_asset(assets: list[dict], *, arch: WindowsArch) -> tuple[str | None, str | None]:
    candidates: list[str]
    if arch == "arm64":
        candidates = [
            "programista-win-arm64.msi",
            "programista-win-arm64.exe",
            "programista-win-x64.msi",
            "programista.exe",
        ]
    elif arch == "x64":
        candidates = [
            "programista-win-x64.msi",
            "programista-win-x64.exe",
            "programista.exe",
        ]
    else:
        candidates = [
            "programista-win-x64.msi",
            "programista-win-x64.exe",
            "programista.exe",
        ]

    by_name = {str(a.get("name") or ""): a for a in assets}
    for name in candidates:
        a = by_name.get(name)
        if not a:
            continue
        url = a.get("browser_download_url")
        if isinstance(url, str) and url:
            return name, url
    return None, None


def check_for_app_update(
    http: HttpClient,
    *,
    current_version: str,
    force_refresh: bool,
    cache_ttl_seconds: int = 6 * 3600,
) -> AppUpdateCheckResult:
    if platform.system().lower() == "windows" and is_packaged_app():
        return AppUpdateCheckResult(
            current_version=current_version,
            latest_version=None,
            update_available=False,
            release_url=None,
            installer_name=None,
            installer_url=None,
            message="Ta wersja programu jest aktualizowana przez Microsoft Store.",
        )

    try:
        raw = http.get_text(
            GITHUB_LATEST_RELEASE_URL,
            cache_key=_CACHE_KEY_LATEST_RELEASE,
            ttl_seconds=cache_ttl_seconds,
            force_refresh=force_refresh,
            timeout_seconds=10.0,
        )
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        return AppUpdateCheckResult(
            current_version=current_version,
            latest_version=None,
            update_available=False,
            release_url=None,
            installer_name=None,
            installer_url=None,
            message=f"Nie udało się sprawdzić aktualizacji: {e}",
        )

    tag = str(data.get("tag_name") or "")
    latest_version = tag.lstrip("vV") if tag else None
    release_url = data.get("html_url")
    if not isinstance(release_url, str) or not release_url:
        release_url = None

    if not latest_version:
        return AppUpdateCheckResult(
            current_version=current_version,
            latest_version=None,
            update_available=False,
            release_url=release_url,
            installer_name=None,
            installer_url=None,
            message="Nie udało się odczytać wersji z GitHuba.",
        )

    update_available = _version_tuple(latest_version) > _version_tuple(current_version)

    assets = data.get("assets")
    if not isinstance(assets, list):
        assets = []

    installer_name = None
    installer_url = None
    if platform.system().lower() == "windows":
        installer_name, installer_url = _pick_windows_installer_asset(assets, arch=windows_arch())

    if update_available:
        return AppUpdateCheckResult(
            current_version=current_version,
            latest_version=latest_version,
            update_available=True,
            release_url=release_url,
            installer_name=installer_name,
            installer_url=installer_url,
            message=f"Dostępna jest nowa wersja: {latest_version} (masz: {current_version}).",
        )

    return AppUpdateCheckResult(
        current_version=current_version,
        latest_version=latest_version,
        update_available=False,
        release_url=release_url,
        installer_name=installer_name,
        installer_url=installer_url,
        message=f"Masz aktualną wersję ({current_version}).",
    )
