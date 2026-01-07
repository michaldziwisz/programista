from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ProviderKind = Literal["tv", "radio", "archive"]


class PackFormatError(ValueError):
    pass


@dataclass(frozen=True)
class LatestPackInfo:
    version: str
    sha256: str
    asset: str


@dataclass(frozen=True)
class LatestManifest:
    schema: int
    provider_api_version: int
    packs: dict[ProviderKind, LatestPackInfo]


@dataclass(frozen=True)
class PackManifest:
    schema: int
    kind: ProviderKind
    version: str
    package: str
    entrypoint: str
    provider_api_version: int
    min_app_version: str | None


def _load_json(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:  # noqa: PERF203
        raise PackFormatError(f"Nieprawidłowy JSON: {e}") from e
    if not isinstance(data, dict):
        raise PackFormatError("Oczekiwano obiektu JSON.")
    return data


def parse_latest_manifest(text: str) -> LatestManifest:
    data = _load_json(text)

    schema = data.get("schema")
    if schema != 1:
        raise PackFormatError("Nieobsługiwana wersja schema w latest.json.")

    api_version = data.get("provider_api_version")
    if not isinstance(api_version, int) or api_version < 1:
        raise PackFormatError("Brak/nieprawidłowe provider_api_version w latest.json.")

    packs_raw = data.get("packs")
    if not isinstance(packs_raw, dict):
        raise PackFormatError("Brak/nieprawidłowe packs w latest.json.")

    packs: dict[ProviderKind, LatestPackInfo] = {}
    for kind in ("tv", "radio", "archive"):
        entry = packs_raw.get(kind)
        if not isinstance(entry, dict):
            raise PackFormatError(f"Brak/nieprawidłowe packs.{kind} w latest.json.")
        version = entry.get("version")
        sha256 = entry.get("sha256")
        asset = entry.get("asset")
        if not isinstance(version, str) or not version.strip():
            raise PackFormatError(f"Brak/nieprawidłowe packs.{kind}.version w latest.json.")
        if not isinstance(sha256, str) or len(sha256.strip()) < 32:
            raise PackFormatError(f"Brak/nieprawidłowe packs.{kind}.sha256 w latest.json.")
        if not isinstance(asset, str) or not asset.strip():
            raise PackFormatError(f"Brak/nieprawidłowe packs.{kind}.asset w latest.json.")
        packs[kind] = LatestPackInfo(version=version.strip(), sha256=sha256.strip(), asset=asset.strip())

    return LatestManifest(schema=schema, provider_api_version=api_version, packs=packs)


def read_pack_manifest(pack_dir: Path) -> PackManifest:
    path = pack_dir / "pack.json"
    if not path.is_file():
        raise PackFormatError("Brak pack.json w paczce.")
    text = path.read_text(encoding="utf-8")
    data = _load_json(text)

    schema = data.get("schema")
    if schema != 1:
        raise PackFormatError("Nieobsługiwana wersja schema w pack.json.")

    kind = data.get("kind")
    if kind not in ("tv", "radio", "archive"):
        raise PackFormatError("Brak/nieprawidłowe kind w pack.json.")

    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise PackFormatError("Brak/nieprawidłowe version w pack.json.")

    package = data.get("package")
    if not isinstance(package, str) or not package.strip():
        raise PackFormatError("Brak/nieprawidłowe package w pack.json.")

    entrypoint = data.get("entrypoint")
    if not isinstance(entrypoint, str) or ":" not in entrypoint:
        raise PackFormatError("Brak/nieprawidłowe entrypoint w pack.json.")

    api_version = data.get("provider_api_version")
    if not isinstance(api_version, int) or api_version < 1:
        raise PackFormatError("Brak/nieprawidłowe provider_api_version w pack.json.")

    min_app_version = data.get("min_app_version")
    if min_app_version is not None and (not isinstance(min_app_version, str) or not min_app_version.strip()):
        raise PackFormatError("Nieprawidłowe min_app_version w pack.json.")

    return PackManifest(
        schema=schema,
        kind=kind,
        version=version.strip(),
        package=package.strip(),
        entrypoint=entrypoint.strip(),
        provider_api_version=api_version,
        min_app_version=min_app_version.strip() if isinstance(min_app_version, str) else None,
    )

