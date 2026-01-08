from __future__ import annotations

import json
import sqlite3
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CacheEntry:
    key: str
    value: str
    fetched_at: int
    expires_at: int


class SqliteCache:
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

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL,
                  fetched_at INTEGER NOT NULL,
                  expires_at INTEGER NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cache_entries_expires_at
                ON cache_entries(expires_at)
                """
            )
            self._conn.commit()

    def prune_expired(self) -> int:
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute("DELETE FROM cache_entries WHERE expires_at <= ?", (now,))
            self._conn.commit()
            return cur.rowcount

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM cache_entries")
            self._conn.commit()

    def get_text(self, key: str) -> str | None:
        now = int(time.time())
        with self._lock:
            row = self._conn.execute(
                "SELECT value, expires_at FROM cache_entries WHERE key = ?",
                (key,),
            ).fetchone()
            if not row:
                return None
            value, expires_at = row
            if expires_at <= now:
                self._conn.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
                self._conn.commit()
                return None
            if isinstance(value, (bytes, bytearray, memoryview)):
                raw = bytes(value)
                try:
                    raw = zlib.decompress(raw)
                except Exception:  # noqa: BLE001
                    pass
                return raw.decode("utf-8", errors="replace")
            return str(value)

    def set_text(self, key: str, value: str, *, ttl_seconds: int) -> None:
        now = int(time.time())
        expires_at = now + int(ttl_seconds)
        payload: str | bytes
        # Large HTML/JSON payloads can be several MB (e.g. TVP), making SQLite writes slow.
        # Compress them to keep the UI responsive.
        if len(value) >= 200_000:
            payload = zlib.compress(value.encode("utf-8"), level=6)
        else:
            payload = value
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO cache_entries(key, value, fetched_at, expires_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                  value=excluded.value,
                  fetched_at=excluded.fetched_at,
                  expires_at=excluded.expires_at
                """,
                (key, payload, now, expires_at),
            )
            self._conn.commit()

    def get_json(self, key: str) -> Any | None:
        text = self.get_text(key)
        if text is None:
            return None
        return json.loads(text)

    def set_json(self, key: str, value: Any, *, ttl_seconds: int) -> None:
        self.set_text(key, json.dumps(value, ensure_ascii=False), ttl_seconds=ttl_seconds)
