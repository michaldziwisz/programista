from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from datetime import date

import requests

from tvguide_app.core.search_index import SearchKind, SearchResult
from tvguide_app.core.settings import SettingsStore


DEFAULT_HUB_BASE_URL = os.environ.get("PROGRAMISTA_HUB_BASE_URL", "https://tyflo.eu.org/programista/api").rstrip("/")
API_KEY_HEADER = os.environ.get("PROGRAMISTA_HUB_API_KEY_HEADER", "X-Programista-Key")


@dataclass(frozen=True)
class HubRegistration:
    api_key: str
    header: str


class HubClient:
    def __init__(
        self,
        settings_store: SettingsStore,
        *,
        base_url: str = DEFAULT_HUB_BASE_URL,
        app_version: str = "0.0.0",
        user_agent: str = "programista/desktop",
    ) -> None:
        self._settings = settings_store
        self._base_url = (base_url or DEFAULT_HUB_BASE_URL).rstrip("/")
        self._app_version = app_version
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})

    @property
    def base_url(self) -> str:
        return self._base_url

    def get_api_key(self) -> str | None:
        return self._settings.get_hub_api_key()

    def ensure_api_key(self) -> str | None:
        existing = self.get_api_key()
        if existing:
            return existing

        install_id = self._settings.get_or_create_hub_install_id()
        reg = self._register(install_id)
        if not reg:
            return None
        self._settings.set_hub_api_key(reg.api_key)
        return reg.api_key

    def _register(self, install_id: str) -> HubRegistration | None:
        payload = {
            "install_id": install_id,
            "app_version": self._app_version,
            "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        }
        try:
            resp = self._session.post(
                f"{self._base_url}/register",
                json=payload,
                timeout=10.0,
            )
            resp.raise_for_status()
            obj = resp.json()
        except Exception:  # noqa: BLE001
            return None

        api_key = obj.get("api_key")
        header = obj.get("header") or API_KEY_HEADER
        if not isinstance(api_key, str) or not api_key.strip():
            return None
        if not isinstance(header, str) or not header.strip():
            header = API_KEY_HEADER
        return HubRegistration(api_key=api_key.strip(), header=header.strip())

    def search(
        self,
        query: str,
        *,
        kinds: set[SearchKind],
        limit: int = 200,
        cursor: int | None = None,
    ) -> list[SearchResult]:
        api_key = self.ensure_api_key()
        if not api_key:
            raise RuntimeError("Brak klucza API.")

        if not kinds:
            kinds = {"tv", "radio", "tv_accessibility", "archive"}

        payload = {
            "query": (query or "").strip(),
            "kinds": sorted(kinds),
            "limit": max(1, min(int(limit), 200)),
        }
        if cursor is not None:
            payload["cursor"] = int(cursor)
        if not payload["query"]:
            return []

        headers = {API_KEY_HEADER: api_key}

        resp = self._session.post(
            f"{self._base_url}/search",
            json=payload,
            headers=headers,
            timeout=15.0,
        )
        if resp.status_code == 401:
            # Key could have been revoked/cleared server-side; re-register once.
            self._settings.clear_hub_api_key()
            api_key = self.ensure_api_key()
            if not api_key:
                resp.raise_for_status()
            headers = {API_KEY_HEADER: api_key}
            resp = self._session.post(
                f"{self._base_url}/search",
                json=payload,
                headers=headers,
                timeout=15.0,
            )

        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            raise RuntimeError("Nieprawidłowa odpowiedź serwera.")

        out: list[SearchResult] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            kind = str(row.get("kind") or "").strip()
            if kind not in {"tv", "radio", "tv_accessibility", "archive"}:
                continue
            provider_id = str(row.get("provider_id") or "").strip()
            source_id = str(row.get("source_id") or "").strip()
            source_name = str(row.get("source_name") or "").strip()
            title = str(row.get("title") or "").strip()
            if not provider_id or not source_id or not source_name or not title:
                continue

            day_raw = row.get("day")
            try:
                day = date.fromisoformat(str(day_raw))
            except ValueError:
                continue

            start_raw = str(row.get("start_time") or "").strip()
            # API returns ISO time (HH:MM:SS); in UI we display HH:MM.
            start = start_raw[:5] if len(start_raw) >= 5 else start_raw

            subtitle = row.get("subtitle")
            if subtitle is not None:
                subtitle = str(subtitle).strip() or None

            details_ref = row.get("details_ref")
            if details_ref is not None:
                details_ref = str(details_ref).strip() or None

            details_summary = row.get("details_summary")
            if details_summary is not None:
                details_summary = str(details_summary).strip() or None

            item_id = row.get("item_id")
            if item_id is not None:
                try:
                    item_id = int(item_id)
                except (TypeError, ValueError):
                    item_id = None

            feats_raw = row.get("accessibility")
            feats: list[str] = []
            if isinstance(feats_raw, list):
                feats = [str(f).strip() for f in feats_raw if str(f).strip()]
            accessibility = tuple(f for f in feats if f in {"AD", "JM", "N"})  # type: ignore[assignment]

            out.append(
                SearchResult(
                    kind=kind,  # type: ignore[arg-type]
                    provider_id=provider_id,
                    source_id=source_id,
                    source_name=source_name,
                    day=day,
                    start=start,
                    title=title,
                    subtitle=subtitle,
                    details_ref=details_ref,
                    details_summary=details_summary,
                    accessibility=accessibility,  # type: ignore[arg-type]
                    item_id=item_id,  # type: ignore[arg-type]
                )
            )

        # Keep results readable: chronological by default (like local search).
        out.sort(key=lambda r: (r.day, r.start, r.source_name.casefold(), r.title.casefold()))
        return out

    def get_details_text(self, provider_id: str, details_ref: str) -> str | None:
        provider_id = (provider_id or "").strip()
        details_ref = (details_ref or "").strip()
        if not provider_id or not details_ref:
            return None

        api_key = self.ensure_api_key()
        if not api_key:
            return None

        payload = {"provider_id": provider_id, "details_ref": details_ref}
        headers = {API_KEY_HEADER: api_key}

        resp = self._session.post(
            f"{self._base_url}/details",
            json=payload,
            headers=headers,
            timeout=10.0,
        )
        if resp.status_code == 401:
            self._settings.clear_hub_api_key()
            api_key = self.ensure_api_key()
            if not api_key:
                return None
            headers = {API_KEY_HEADER: api_key}
            resp = self._session.post(
                f"{self._base_url}/details",
                json=payload,
                headers=headers,
                timeout=10.0,
            )

        if resp.status_code == 404:
            return None

        resp.raise_for_status()
        obj = resp.json()
        text = obj.get("text") if isinstance(obj, dict) else None
        if not isinstance(text, str) or not text.strip():
            return None
        return text.strip()
