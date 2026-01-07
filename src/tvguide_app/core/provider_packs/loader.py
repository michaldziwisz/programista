from __future__ import annotations

import importlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from tvguide_app.core.http import HttpClient
from tvguide_app.core.provider_packs.schema import PackFormatError, PackManifest, ProviderKind, read_pack_manifest
from tvguide_app.core.providers.archive_base import ArchiveProvider
from tvguide_app.core.providers.base import ScheduleProvider

SUPPORTED_PROVIDER_API_VERSION = 1


@dataclass(frozen=True)
class LoadedPack:
    kind: ProviderKind
    version: str
    manifest: PackManifest
    providers: list[ScheduleProvider] | list[ArchiveProvider]


def _norm_path(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def _purge_modules(prefix: str) -> None:
    for name in list(sys.modules):
        if name == prefix or name.startswith(prefix + "."):
            sys.modules.pop(name, None)


def _version_parts(v: str) -> tuple[int, ...] | None:
    parts: list[int] = []
    for p in v.split("."):
        p = p.strip()
        if not p.isdigit():
            return None
        parts.append(int(p))
    return tuple(parts)


def _is_version_at_least(current: str, minimum: str) -> bool:
    c = _version_parts(current)
    m = _version_parts(minimum)
    if c is None or m is None:
        # If we can't parse versions, be permissive.
        return True
    return c >= m


class PackStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def active_versions(self) -> dict[ProviderKind, str]:
        path = self._root / "active.json"
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        out: dict[ProviderKind, str] = {}
        for kind in ("tv", "radio", "archive", "tv_accessibility"):
            v = data.get(kind)
            if isinstance(v, str) and v.strip():
                out[kind] = v.strip()
        return out

    def set_active_version(self, kind: ProviderKind, version: str) -> None:
        active = self.active_versions()
        active[kind] = version
        path = self._root / "active.json"
        path.write_text(json.dumps(active, ensure_ascii=False, indent=2), encoding="utf-8")

    def pack_dir(self, kind: ProviderKind, version: str) -> Path:
        return self._root / kind / version

    def list_installed_versions(self, kind: ProviderKind) -> list[str]:
        base = self._root / kind
        if not base.is_dir():
            return []
        versions: list[str] = []
        for p in base.iterdir():
            if p.is_dir():
                versions.append(p.name)
        versions.sort()
        return versions

    def resolve_active_pack_dir(self, kind: ProviderKind) -> Path | None:
        active = self.active_versions().get(kind)
        if active:
            p = self.pack_dir(kind, active)
            if p.is_dir():
                return p
        versions = self.list_installed_versions(kind)
        if not versions:
            return None
        return self.pack_dir(kind, versions[-1])


class PackLoader:
    def __init__(self, store: PackStore, *, app_version: str) -> None:
        self._store = store
        self._app_version = app_version

    def load_kind(self, kind: ProviderKind, http: HttpClient) -> LoadedPack | None:
        pack_dir = self._store.resolve_active_pack_dir(kind)
        if not pack_dir:
            return None
        manifest = read_pack_manifest(pack_dir)
        if manifest.kind != kind:
            raise PackFormatError("pack.json.kind nie zgadza się z typem paczki.")
        if manifest.provider_api_version != SUPPORTED_PROVIDER_API_VERSION:
            raise PackFormatError("Niekompatybilna wersja provider_api_version w pack.json.")
        if manifest.min_app_version and not _is_version_at_least(self._app_version, manifest.min_app_version):
            raise PackFormatError("Paczka dostawców wymaga nowszej wersji aplikacji.")

        module_name, func_name = self._parse_entrypoint(manifest.entrypoint)

        self._ensure_sys_path(pack_dir, kind)
        _purge_modules(manifest.package)
        _purge_modules(module_name)

        module = importlib.import_module(module_name)
        func = getattr(module, func_name, None)
        if not callable(func):
            raise PackFormatError("Entrypoint nie jest funkcją.")

        providers = self._call_entrypoint(func, kind, http)
        return LoadedPack(kind=kind, version=manifest.version, manifest=manifest, providers=providers)

    @staticmethod
    def _parse_entrypoint(entrypoint: str) -> tuple[str, str]:
        if ":" not in entrypoint:
            raise PackFormatError("Nieprawidłowy entrypoint w pack.json.")
        module_name, func_name = entrypoint.split(":", 1)
        module_name = module_name.strip()
        func_name = func_name.strip()
        if not module_name or not func_name:
            raise PackFormatError("Nieprawidłowy entrypoint w pack.json.")
        return module_name, func_name

    def _ensure_sys_path(self, pack_dir: Path, kind: ProviderKind) -> None:
        kind_dir = self._store.root / kind
        kind_dir_norm = _norm_path(str(kind_dir))
        sys.path[:] = [p for p in sys.path if not _norm_path(p).startswith(kind_dir_norm)]
        sys.path.insert(0, str(pack_dir))

    @staticmethod
    def _call_entrypoint(
        func: Callable[[HttpClient], object],
        kind: ProviderKind,
        http: HttpClient,
    ) -> list[ScheduleProvider] | list[ArchiveProvider]:
        result = func(http)
        if not isinstance(result, list):
            raise PackFormatError("Entrypoint powinien zwracać listę dostawców.")
        if kind in ("tv", "radio", "tv_accessibility"):
            providers: list[ScheduleProvider] = []
            for p in result:
                if not isinstance(p, ScheduleProvider):
                    raise PackFormatError("Dostawca nie implementuje ScheduleProvider.")
                providers.append(p)
            return providers

        providers_arch: list[ArchiveProvider] = []
        for p in result:
            if not isinstance(p, ArchiveProvider):
                raise PackFormatError("Dostawca nie implementuje ArchiveProvider.")
            providers_arch.append(p)
        return providers_arch
