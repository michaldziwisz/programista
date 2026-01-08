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
        self._local = threading.local()
        self._session_headers = {
            "User-Agent": user_agent,
            "Accept-Language": "pl,en;q=0.8",
        }

    def _get_session(self) -> requests.Session:
        # requests.Session is not guaranteed to be thread-safe, so keep one session per thread.
        sess = getattr(self._local, "session", None)
        if sess is None:
            sess = requests.Session()
            sess.headers.update(self._session_headers)
            self._local.session = sess
        return sess

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

        resp = self._get_session().get(url, timeout=timeout_seconds)
        resp.raise_for_status()
        _ensure_reasonable_text_encoding(resp)
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

        resp = self._get_session().post(url, data=data, timeout=timeout_seconds)
        resp.raise_for_status()
        _ensure_reasonable_text_encoding(resp)
        text = resp.text

        if cache_key and ttl_seconds is not None:
            self._cache.set_text(cache_key, text, ttl_seconds=ttl_seconds)
        return text

    @staticmethod
    def polite_delay(seconds: float) -> None:
        if seconds <= 0:
            return
        time.sleep(seconds)


def _ensure_reasonable_text_encoding(resp: requests.Response) -> None:
    content_type = (resp.headers.get("content-type") or "").lower()
    if "charset=" in content_type:
        return
    # requests defaults to ISO-8859-1 for many text/* responses with missing charset;
    # prefer detected encoding in that case to avoid mojibake (e.g. Polsat modules).
    if (resp.encoding or "").lower() == "iso-8859-1" and resp.apparent_encoding:
        resp.encoding = resp.apparent_encoding
