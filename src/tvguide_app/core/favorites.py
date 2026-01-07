from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import threading
from typing import Literal

from tvguide_app.core.models import Source


FavoriteKind = Literal["tv", "radio"]


@dataclass(frozen=True)
class FavoriteRef:
    kind: FavoriteKind
    provider_id: str
    source_id: str


@dataclass(frozen=True)
class FavoriteEntry(FavoriteRef):
    name: str


def encode_favorite_source_id(ref: FavoriteRef) -> str:
    return json.dumps(
        {"k": ref.kind, "p": ref.provider_id, "s": ref.source_id},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_favorite_source_id(value: str) -> FavoriteRef | None:
    try:
        data = json.loads(value)
    except Exception:  # noqa: BLE001
        return None

    if not isinstance(data, dict):
        return None

    kind = data.get("k") or data.get("kind")
    provider_id = data.get("p") or data.get("provider_id")
    source_id = data.get("s") or data.get("source_id")

    if kind not in ("tv", "radio"):
        return None

    if provider_id is None or source_id is None:
        return None

    provider_id = str(provider_id).strip()
    source_id = str(source_id).strip()
    if not provider_id or not source_id:
        return None

    return FavoriteRef(kind=kind, provider_id=provider_id, source_id=source_id)


class FavoritesStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._entries: dict[tuple[str, str, str], FavoriteEntry] = {}
        self._load()

    def list_entries(self) -> list[FavoriteEntry]:
        with self._lock:
            entries = list(self._entries.values())
        entries.sort(key=lambda e: (e.kind, e.name.casefold(), e.provider_id, e.source_id))
        return entries

    def get(self, ref: FavoriteRef) -> FavoriteEntry | None:
        key = (ref.kind, ref.provider_id, ref.source_id)
        with self._lock:
            return self._entries.get(key)

    def is_favorite(self, ref: FavoriteRef) -> bool:
        return self.get(ref) is not None

    def add_entry(self, entry: FavoriteEntry) -> bool:
        key = (entry.kind, entry.provider_id, entry.source_id)
        with self._lock:
            current = self._entries.get(key)
            if current == entry:
                return False
            self._entries[key] = entry
            self._save_locked()
        return True

    def add_source(self, kind: FavoriteKind, source: Source) -> bool:
        return self.add_entry(
            FavoriteEntry(
                kind=kind,
                provider_id=str(source.provider_id),
                source_id=str(source.id),
                name=source.name,
            )
        )

    def remove(self, ref: FavoriteRef) -> bool:
        key = (ref.kind, ref.provider_id, ref.source_id)
        with self._lock:
            if key not in self._entries:
                return False
            del self._entries[key]
            self._save_locked()
        return True

    def _load(self) -> None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except Exception:  # noqa: BLE001
            return

        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            return

        if isinstance(data, dict):
            favorites = data.get("favorites", [])
        else:
            favorites = []

        if not isinstance(favorites, list):
            return

        loaded: dict[tuple[str, str, str], FavoriteEntry] = {}
        for it in favorites:
            if not isinstance(it, dict):
                continue
            kind = it.get("kind")
            provider_id = it.get("provider_id")
            source_id = it.get("source_id")
            name = it.get("name")
            if kind not in ("tv", "radio"):
                continue
            if provider_id is None or source_id is None or name is None:
                continue
            provider_id = str(provider_id).strip()
            source_id = str(source_id).strip()
            name = str(name).strip()
            if not provider_id or not source_id or not name:
                continue
            entry = FavoriteEntry(kind=kind, provider_id=provider_id, source_id=source_id, name=name)
            loaded[(entry.kind, entry.provider_id, entry.source_id)] = entry

        with self._lock:
            self._entries = loaded

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(f"{self._path.name}.tmp")
        data = {
            "version": 1,
            "favorites": [
                {
                    "kind": entry.kind,
                    "provider_id": entry.provider_id,
                    "source_id": entry.source_id,
                    "name": entry.name,
                }
                for entry in self.list_entries()
            ],
        }
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self._path)

