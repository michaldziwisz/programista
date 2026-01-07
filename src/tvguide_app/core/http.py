from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from typing import Any

import requests

from tvguide_app.core.cache import SqliteCache


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status_code: int
    text: str


class HttpClient:
    def __init__(self, cache: SqliteCache, *, user_agent: str) -> None:
        self._cache = cache
        self._session = requests.Session()
        self._lock = threading.Lock()
        self._session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Language": "pl,en;q=0.8",
            }
        )

    def get_text(
        self,
        url: str,
        *,
        cache_key: str | None = None,
        ttl_seconds: int | None = None,
        force_refresh: bool = False,
        timeout_seconds: float = 15.0,
    ) -> str:
        if cache_key and not force_refresh:
            cached = self._cache.get_text(cache_key)
            if cached is not None:
                return cached

        with self._lock:
            resp = self._session.get(url, timeout=timeout_seconds)
        resp.raise_for_status()
        text = resp.text

        if cache_key and ttl_seconds is not None:
            self._cache.set_text(cache_key, text, ttl_seconds=ttl_seconds)
        return text

    def post_form_text(
        self,
        url: str,
        data: dict[str, Any],
        *,
        cache_key: str | None = None,
        ttl_seconds: int | None = None,
        force_refresh: bool = False,
        timeout_seconds: float = 15.0,
    ) -> str:
        if cache_key and not force_refresh:
            cached = self._cache.get_text(cache_key)
            if cached is not None:
                return cached

        with self._lock:
            resp = self._session.post(url, data=data, timeout=timeout_seconds)
        resp.raise_for_status()
        text = resp.text

        if cache_key and ttl_seconds is not None:
            self._cache.set_text(cache_key, text, ttl_seconds=ttl_seconds)
        return text

    @staticmethod
    def polite_delay(seconds: float) -> None:
        if seconds <= 0:
            return
        time.sleep(seconds)
