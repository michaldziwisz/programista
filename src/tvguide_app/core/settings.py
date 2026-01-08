from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TvAccessibilityFilters:
    ad: bool = True
    jm: bool = True
    n: bool = True


@dataclass(frozen=True)
class SearchKindFilters:
    tv: bool = True
    radio: bool = True
    tv_accessibility: bool = True
    archive: bool = True


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = self._load()

    def get_tv_accessibility_filters(self) -> TvAccessibilityFilters:
        with self._lock:
            raw = self._data.get("tv_accessibility_filters")
            if not isinstance(raw, dict):
                return TvAccessibilityFilters()
            return TvAccessibilityFilters(
                ad=bool(raw.get("ad", True)),
                jm=bool(raw.get("jm", True)),
                n=bool(raw.get("n", True)),
            )

    def set_tv_accessibility_filters(self, filters: TvAccessibilityFilters) -> None:
        with self._lock:
            self._data["tv_accessibility_filters"] = {"ad": bool(filters.ad), "jm": bool(filters.jm), "n": bool(filters.n)}
            self._save(self._data)

    def get_search_kind_filters(self) -> SearchKindFilters:
        with self._lock:
            raw = self._data.get("search_kind_filters")
            if not isinstance(raw, dict):
                return SearchKindFilters()
            return SearchKindFilters(
                tv=bool(raw.get("tv", True)),
                radio=bool(raw.get("radio", True)),
                tv_accessibility=bool(raw.get("tv_accessibility", True)),
                archive=bool(raw.get("archive", True)),
            )

    def set_search_kind_filters(self, filters: SearchKindFilters) -> None:
        with self._lock:
            self._data["search_kind_filters"] = {
                "tv": bool(filters.tv),
                "radio": bool(filters.radio),
                "tv_accessibility": bool(filters.tv_accessibility),
                "archive": bool(filters.archive),
            }
            self._save(self._data)

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return obj if isinstance(obj, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)
