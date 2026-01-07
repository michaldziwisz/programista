from __future__ import annotations

import hashlib
import os
import tempfile
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

from tvguide_app.core.http import HttpClient
from tvguide_app.core.provider_packs.loader import SUPPORTED_PROVIDER_API_VERSION, PackStore
from tvguide_app.core.provider_packs.schema import LatestManifest, PackFormatError, ProviderKind, parse_latest_manifest, read_pack_manifest


@dataclass(frozen=True)
class PackUpdate:
    kind: ProviderKind
    version: str


@dataclass(frozen=True)
class UpdateResult:
    updated: list[PackUpdate]
    message: str


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_extracted_root(extract_dir: Path) -> Path:
    if (extract_dir / "pack.json").is_file():
        return extract_dir
    entries = [p for p in extract_dir.iterdir()]
    dirs = [p for p in entries if p.is_dir()]
    if len(dirs) == 1 and (dirs[0] / "pack.json").is_file():
        return dirs[0]
    raise PackFormatError("Nie znaleziono pack.json po rozpakowaniu paczki.")


class ProviderPackUpdater:
    def __init__(self, http: HttpClient, store: PackStore, *, base_url: str) -> None:
        self._http = http
        self._store = store
        self._base_url = base_url.rstrip("/") + "/"
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "programista/0.1 (+providers-updater)"})

    def fetch_latest(self, *, force: bool) -> LatestManifest:
        url = self._base_url + "latest.json"
        text = self._http.get_text(
            url,
            cache_key=f"providers:latest:{url}",
            ttl_seconds=12 * 3600,
            force_refresh=force,
            timeout_seconds=20.0,
        )
        return parse_latest_manifest(text)

    def update_if_needed(self, *, force_check: bool = False) -> UpdateResult:
        latest = self.fetch_latest(force=force_check)
        if latest.provider_api_version != SUPPORTED_PROVIDER_API_VERSION:
            raise PackFormatError("Niekompatybilna wersja provider_api_version w latest.json.")

        active = self._store.active_versions()
        updated: list[PackUpdate] = []

        for kind, pack in latest.packs.items():
            current = active.get(kind)
            if current == pack.version:
                continue
            self._install_pack(kind, pack.version, pack.asset, pack.sha256)
            self._store.set_active_version(kind, pack.version)
            updated.append(PackUpdate(kind=kind, version=pack.version))

        if not updated:
            return UpdateResult(updated=[], message="Dostawcy są aktualni.")
        kinds = ", ".join([f"{u.kind}={u.version}" for u in updated])
        return UpdateResult(updated=updated, message=f"Zaktualizowano dostawców: {kinds}.")

    def _install_pack(self, kind: ProviderKind, version: str, asset: str, expected_sha256: str) -> None:
        target_dir = self._store.pack_dir(kind, version)
        if target_dir.is_dir():
            manifest = read_pack_manifest(target_dir)
            if manifest.kind == kind and manifest.version == version:
                return

        url = self._base_url + asset.lstrip("/")

        with tempfile.TemporaryDirectory(prefix=f"providers-{kind}-") as tmp:
            tmp_dir = Path(tmp)
            zip_path = tmp_dir / f"{kind}-{version}.zip"
            self._download(url, zip_path)

            actual_sha = _sha256_file(zip_path)
            if actual_sha.lower() != expected_sha256.lower():
                raise PackFormatError("Nie zgadza się SHA256 paczki dostawców.")

            extract_dir = tmp_dir / "extract"
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)

            root = _resolve_extracted_root(extract_dir)
            manifest = read_pack_manifest(root)
            if manifest.kind != kind or manifest.version != version:
                raise PackFormatError("pack.json nie zgadza się z latest.json.")

            final_parent = self._store.root / kind
            final_parent.mkdir(parents=True, exist_ok=True)
            tmp_install = final_parent / f".tmp-{version}-{os.getpid()}"
            shutil.rmtree(tmp_install, ignore_errors=True)
            shutil.copytree(root, tmp_install)

            if target_dir.exists():
                # Keep a backup rather than deleting.
                backup = target_dir.with_name(target_dir.name + f".bak-{uuid.uuid4().hex}")
                target_dir.rename(backup)
            tmp_install.rename(target_dir)

    def _download(self, url: str, dest: Path) -> None:
        with self._session.get(url, stream=True, timeout=30.0) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        f.write(chunk)
