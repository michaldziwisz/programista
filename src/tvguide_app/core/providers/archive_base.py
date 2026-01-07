from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from tvguide_app.core.models import ScheduleItem, Source


class ArchiveProvider(ABC):
    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @property
    @abstractmethod
    def display_name(self) -> str: ...

    @abstractmethod
    def list_years(self) -> list[int]: ...

    @abstractmethod
    def list_days_in_month(
        self,
        year: int,
        month: int,
        *,
        force_refresh: bool = False,
    ) -> list[date]: ...

    @abstractmethod
    def list_sources_for_day(self, day: date, *, force_refresh: bool = False) -> list[Source]: ...

    @abstractmethod
    def get_schedule(
        self,
        source: Source,
        day: date,
        *,
        force_refresh: bool = False,
    ) -> list[ScheduleItem]: ...

