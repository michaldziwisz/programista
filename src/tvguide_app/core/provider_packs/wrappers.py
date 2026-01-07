from __future__ import annotations

import threading
from datetime import date

from tvguide_app.core.models import ScheduleItem, Source
from tvguide_app.core.providers.archive_base import ArchiveProvider
from tvguide_app.core.providers.base import ScheduleProvider


class EmptyScheduleProvider(ScheduleProvider):
    @property
    def provider_id(self) -> str:
        return "empty"

    @property
    def display_name(self) -> str:
        return "Brak dostawców"

    def list_sources(self, *, force_refresh: bool = False) -> list[Source]:
        return []

    def list_days(self, *, force_refresh: bool = False) -> list[date]:
        return []

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        return []

    def get_item_details(self, item: ScheduleItem, *, force_refresh: bool = False) -> str:
        return ""


class CompositeScheduleProvider(ScheduleProvider):
    def __init__(self, providers: list[ScheduleProvider]) -> None:
        self._providers = list(providers)
        self._by_id = {p.provider_id: p for p in self._providers}

    @property
    def provider_id(self) -> str:
        return "composite"

    @property
    def display_name(self) -> str:
        return "Dostawcy"

    def list_sources(self, *, force_refresh: bool = False) -> list[Source]:
        sources: list[Source] = []
        for p in self._providers:
            sources.extend(p.list_sources(force_refresh=force_refresh))
        sources.sort(key=lambda s: str(s.name).casefold())
        return sources

    def list_days(self, *, force_refresh: bool = False) -> list[date]:
        days: set[date] = set()
        for p in self._providers:
            days.update(p.list_days(force_refresh=force_refresh))
        return sorted(days)

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        p = self._by_id.get(str(source.provider_id))
        if not p:
            return []
        return p.get_schedule(source, day, force_refresh=force_refresh)

    def get_item_details(self, item: ScheduleItem, *, force_refresh: bool = False) -> str:
        p = self._by_id.get(str(item.provider_id))
        if not p:
            return ""
        return p.get_item_details(item, force_refresh=force_refresh)


class ReloadableScheduleProvider(ScheduleProvider):
    def __init__(self, delegate: ScheduleProvider) -> None:
        self._lock = threading.RLock()
        self._delegate: ScheduleProvider = delegate

    def set_delegate(self, delegate: ScheduleProvider) -> None:
        with self._lock:
            self._delegate = delegate

    def _get(self) -> ScheduleProvider:
        with self._lock:
            return self._delegate

    @property
    def provider_id(self) -> str:
        return self._get().provider_id

    @property
    def display_name(self) -> str:
        return self._get().display_name

    def list_sources(self, *, force_refresh: bool = False) -> list[Source]:
        return self._get().list_sources(force_refresh=force_refresh)

    def list_days(self, *, force_refresh: bool = False) -> list[date]:
        return self._get().list_days(force_refresh=force_refresh)

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        return self._get().get_schedule(source, day, force_refresh=force_refresh)

    def get_item_details(self, item: ScheduleItem, *, force_refresh: bool = False) -> str:
        return self._get().get_item_details(item, force_refresh=force_refresh)


class EmptyArchiveProvider(ArchiveProvider):
    @property
    def provider_id(self) -> str:
        return "empty-archive"

    @property
    def display_name(self) -> str:
        return "Brak dostawców"

    def list_years(self) -> list[int]:
        return []

    def list_days_in_month(
        self,
        year: int,
        month: int,
        *,
        force_refresh: bool = False,
    ) -> list[date]:
        return []

    def list_sources_for_day(self, day: date, *, force_refresh: bool = False) -> list[Source]:
        return []

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        return []


class CompositeArchiveProvider(ArchiveProvider):
    def __init__(self, providers: list[ArchiveProvider]) -> None:
        self._providers = list(providers)
        self._by_id = {p.provider_id: p for p in self._providers}

    @property
    def provider_id(self) -> str:
        return "composite-archive"

    @property
    def display_name(self) -> str:
        return "Programy archiwalne"

    def list_years(self) -> list[int]:
        years: set[int] = set()
        for p in self._providers:
            years.update(p.list_years())
        return sorted(years)

    def list_days_in_month(
        self,
        year: int,
        month: int,
        *,
        force_refresh: bool = False,
    ) -> list[date]:
        days: set[date] = set()
        for p in self._providers:
            days.update(p.list_days_in_month(year, month, force_refresh=force_refresh))
        return sorted(days)

    def list_sources_for_day(self, day: date, *, force_refresh: bool = False) -> list[Source]:
        sources: list[Source] = []
        for p in self._providers:
            sources.extend(p.list_sources_for_day(day, force_refresh=force_refresh))
        sources.sort(key=lambda s: str(s.name).casefold())
        return sources

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        p = self._by_id.get(str(source.provider_id))
        if not p:
            return []
        return p.get_schedule(source, day, force_refresh=force_refresh)


class ReloadableArchiveProvider(ArchiveProvider):
    def __init__(self, delegate: ArchiveProvider) -> None:
        self._lock = threading.RLock()
        self._delegate: ArchiveProvider = delegate

    def set_delegate(self, delegate: ArchiveProvider) -> None:
        with self._lock:
            self._delegate = delegate

    def _get(self) -> ArchiveProvider:
        with self._lock:
            return self._delegate

    @property
    def provider_id(self) -> str:
        return self._get().provider_id

    @property
    def display_name(self) -> str:
        return self._get().display_name

    def list_years(self) -> list[int]:
        return self._get().list_years()

    def list_days_in_month(
        self,
        year: int,
        month: int,
        *,
        force_refresh: bool = False,
    ) -> list[date]:
        return self._get().list_days_in_month(year, month, force_refresh=force_refresh)

    def list_sources_for_day(self, day: date, *, force_refresh: bool = False) -> list[Source]:
        return self._get().list_sources_for_day(day, force_refresh=force_refresh)

    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]:
        return self._get().get_schedule(source, day, force_refresh=force_refresh)

