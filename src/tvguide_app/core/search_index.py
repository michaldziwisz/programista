from __future__ import annotations

import sqlite3
import threading
import time as time_module
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from tvguide_app.core.models import AccessibilityFeature, ScheduleItem


SearchKind = Literal["tv", "radio", "tv_accessibility", "archive"]


@dataclass(frozen=True)
class SearchResult:
    kind: SearchKind
    provider_id: str
    source_id: str
    source_name: str
    day: date
    start: str
    title: str
    accessibility: tuple[AccessibilityFeature, ...]


class SearchIndex:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM search_items")
            self._conn.commit()

    def prune(self, *, keep_seconds: int = 90 * 24 * 3600) -> int:
        cutoff = int(time_module.time()) - int(keep_seconds)
        with self._lock:
            cur = self._conn.execute("DELETE FROM search_items WHERE indexed_at < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    def add_items(self, kind: SearchKind, items: list[ScheduleItem]) -> None:
        now = int(time_module.time())
        rows: list[tuple[str, str, str, str, str, str, str, str, str, int]] = []
        for it in items:
            if kind == "tv_accessibility" and not it.accessibility:
                continue
            title = str(it.title or "").strip()
            if not title:
                continue
            title_norm = title.casefold()
            provider_id = str(getattr(it.provider_id, "value", it.provider_id))
            src = it.source
            source_id = str(getattr(src.id, "value", src.id))
            source_name = str(getattr(src, "name", "") or "").strip()
            day_iso = it.day.isoformat()
            start = it.start_time.strftime("%H:%M") if it.start_time else ""
            features = ",".join(it.accessibility) if it.accessibility else ""

            rows.append(
                (
                    kind,
                    provider_id,
                    source_id,
                    source_name,
                    day_iso,
                    start,
                    title,
                    title_norm,
                    features,
                    now,
                )
            )

        if not rows:
            return

        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO search_items(
                  kind, provider_id, source_id, source_name,
                  day, start, title, title_norm, features, indexed_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, provider_id, source_id, day, start, title_norm) DO UPDATE SET
                  source_name=excluded.source_name,
                  title=excluded.title,
                  features=excluded.features,
                  indexed_at=excluded.indexed_at
                """,
                rows,
            )
            self._conn.commit()

    def search(self, query: str, *, kinds: set[SearchKind], limit: int = 500) -> list[SearchResult]:
        q = (query or "").strip()
        if not q:
            return []
        if not kinds:
            kinds = {"tv", "radio", "tv_accessibility", "archive"}

        q_norm = q.casefold()
        like = f"%{_escape_like(q_norm)}%"
        kind_list = sorted(kinds)
        placeholders = ",".join(["?"] * len(kind_list))

        with self._lock:
            cur = self._conn.execute(
                f"""
                SELECT kind, provider_id, source_id, source_name, day, start, title, features
                FROM search_items
                WHERE kind IN ({placeholders})
                  AND title_norm LIKE ? ESCAPE '\\'
                ORDER BY day ASC, start ASC, source_name ASC, title ASC
                LIMIT ?
                """,
                (*kind_list, like, int(limit)),
            )
            rows = cur.fetchall()

        out: list[SearchResult] = []
        for kind, provider_id, source_id, source_name, day_iso, start, title, features in rows:
            try:
                d = date.fromisoformat(str(day_iso))
            except ValueError:
                continue

            feats = tuple(f for f in str(features or "").split(",") if f)  # type: ignore[assignment]
            out.append(
                SearchResult(
                    kind=str(kind),  # type: ignore[arg-type]
                    provider_id=str(provider_id),
                    source_id=str(source_id),
                    source_name=str(source_name),
                    day=d,
                    start=str(start or ""),
                    title=str(title),
                    accessibility=feats,  # type: ignore[arg-type]
                )
            )
        return out

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS search_items (
                  kind TEXT NOT NULL,
                  provider_id TEXT NOT NULL,
                  source_id TEXT NOT NULL,
                  source_name TEXT NOT NULL,
                  day TEXT NOT NULL,
                  start TEXT NOT NULL,
                  title TEXT NOT NULL,
                  title_norm TEXT NOT NULL,
                  features TEXT NOT NULL,
                  indexed_at INTEGER NOT NULL,
                  PRIMARY KEY(kind, provider_id, source_id, day, start, title_norm)
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_search_items_title_norm
                ON search_items(title_norm)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_search_items_kind_day
                ON search_items(kind, day)
                """
            )
            self._conn.commit()


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
