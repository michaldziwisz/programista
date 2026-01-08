from __future__ import annotations

from datetime import date
from typing import Any, Literal

from tvguide_app.core.cache import SqliteCache
from tvguide_app.core.models import AccessibilityFeature, ProviderId, ScheduleItem, Source
from tvguide_app.core.providers.archive_base import ArchiveProvider
from tvguide_app.core.providers.base import ScheduleProvider
from tvguide_app.core.util import parse_time_hhmm


ScheduleCacheKind = Literal["tv", "radio", "tv_accessibility", "archive"]


class CachedScheduleProvider(ScheduleProvider):
    def __init__(
        self,
        delegate: ScheduleProvider,
        cache: SqliteCache,
        *,
        kind: ScheduleCacheKind,
        ttl_seconds: int,
    ) -> None:
        self._delegate = delegate
        self._cache = cache
        self._kind = kind
        self._ttl_seconds = int(ttl_seconds)

    @property
    def provider_id(self) -> str:
        return self._delegate.provider_id

    @property
    def display_name(self) -> str:
        return self._delegate.display_name

    def list_sources(self, *, force_refresh: bool = False) -> list[Source]:
        return self._delegate.list_sources(force_refresh=force_refresh)

    def list_days(self, *, force_refresh: bool = False) -> list[date]:
        return self._delegate.list_days(force_refresh=force_refresh)

    def list_days_for_provider(self, provider_id: str, *, force_refresh: bool = False) -> list[date]:
        method = getattr(self._delegate, "list_days_for_provider", None)
        if callable(method):
            return method(provider_id, force_refresh=force_refresh)
        if provider_id == self._delegate.provider_id:
            return self._delegate.list_days(force_refresh=force_refresh)
        return []

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        key = _schedule_cache_key(self._kind, source, day)
        if not force_refresh:
            cached = self._cache.get_json(key)
            decoded = _decode_schedule_items(cached, source, day)
            if decoded is not None:
                return decoded

        items = self._delegate.get_schedule(source, day, force_refresh=force_refresh)
        try:
            self._cache.set_json(key, _encode_schedule_items(items), ttl_seconds=self._ttl_seconds)
        except Exception:  # noqa: BLE001
            pass
        return items

    def get_item_details(self, item: ScheduleItem, *, force_refresh: bool = False) -> str:
        return self._delegate.get_item_details(item, force_refresh=force_refresh)


class CachedArchiveProvider(ArchiveProvider):
    def __init__(
        self,
        delegate: ArchiveProvider,
        cache: SqliteCache,
        *,
        ttl_seconds: int,
    ) -> None:
        self._delegate = delegate
        self._cache = cache
        self._ttl_seconds = int(ttl_seconds)

    @property
    def provider_id(self) -> str:
        return self._delegate.provider_id

    @property
    def display_name(self) -> str:
        return self._delegate.display_name

    def list_years(self) -> list[int]:
        return self._delegate.list_years()

    def list_days_in_month(
        self,
        year: int,
        month: int,
        *,
        force_refresh: bool = False,
    ) -> list[date]:
        return self._delegate.list_days_in_month(year, month, force_refresh=force_refresh)

    def list_sources_for_day(self, day: date, *, force_refresh: bool = False) -> list[Source]:
        return self._delegate.list_sources_for_day(day, force_refresh=force_refresh)

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        key = _schedule_cache_key("archive", source, day)
        if not force_refresh:
            cached = self._cache.get_json(key)
            decoded = _decode_schedule_items(cached, source, day)
            if decoded is not None:
                return decoded

        items = self._delegate.get_schedule(source, day, force_refresh=force_refresh)
        try:
            self._cache.set_json(key, _encode_schedule_items(items), ttl_seconds=self._ttl_seconds)
        except Exception:  # noqa: BLE001
            pass
        return items


def _schedule_cache_key(kind: ScheduleCacheKind, source: Source, day: date) -> str:
    pid = getattr(source.provider_id, "value", source.provider_id)
    sid = getattr(source.id, "value", source.id)
    return f"schedule:v1:{kind}:{pid}:{sid}:{day.isoformat()}"


def _encode_schedule_items(items: list[ScheduleItem]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items:
        out.append(
            {
                "start": it.start_time.strftime("%H:%M") if it.start_time else None,
                "end": it.end_time.strftime("%H:%M") if it.end_time else None,
                "title": it.title,
                "subtitle": it.subtitle,
                "details_ref": it.details_ref,
                "details_summary": it.details_summary,
                "accessibility": list(it.accessibility) if it.accessibility else [],
            }
        )
    return out


def _decode_schedule_items(data: Any, source: Source, day: date) -> list[ScheduleItem] | None:
    if not isinstance(data, list):
        return None

    items: list[ScheduleItem] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "")
        if not title:
            continue

        start_raw = raw.get("start")
        end_raw = raw.get("end")
        start = parse_time_hhmm(str(start_raw)) if isinstance(start_raw, str) and start_raw else None
        end = parse_time_hhmm(str(end_raw)) if isinstance(end_raw, str) and end_raw else None

        acc_raw = raw.get("accessibility")
        acc: tuple[AccessibilityFeature, ...] = ()
        if isinstance(acc_raw, list):
            parsed: list[AccessibilityFeature] = []
            for x in acc_raw:
                if x in ("AD", "JM", "N"):
                    parsed.append(x)  # type: ignore[arg-type]
            acc = tuple(parsed)

        items.append(
            ScheduleItem(
                provider_id=ProviderId(str(getattr(source.provider_id, "value", source.provider_id))),
                source=source,
                day=day,
                start_time=start,
                end_time=end,
                title=title,
                subtitle=str(raw.get("subtitle")) if raw.get("subtitle") is not None else None,
                details_ref=str(raw.get("details_ref")) if raw.get("details_ref") is not None else None,
                details_summary=str(raw.get("details_summary")) if raw.get("details_summary") is not None else None,
                accessibility=acc,
            )
        )

    return items

